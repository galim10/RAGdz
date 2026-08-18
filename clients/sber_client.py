import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

import jsonschema
from json_repair import repair_json

from gigachat import GigaChatAsyncClient
from gigachat.models import Chat, JsonSchemaResponseFormat, Messages, MessagesRole

from config import settings
from rag.bge_embedder import BGEEmbedder
from clients.prompts import SYSTEM_QUERY_PROMPT, SYSTEM_ANSWER_PROMPT, SYSTEM_EXTRACT_METADATA_PROMPT, SYSTEM_CHUNKING_PROMPT, format_history, format_chunks

logger = logging.getLogger(__name__)

ROLE_SYSTEM = MessagesRole.SYSTEM
ROLE_USER = MessagesRole.USER
ROLE_ASSISTANT = MessagesRole.ASSISTANT

EntityType = Literal["person", "place", "organization", "object", "concept"]
PositionLabel = Literal["начало", "первая треть", "середина", "вторая половина", "конец"]

JSON_RETRY_ATTEMPTS = 2


@dataclass
class EntityItem:
    name: str
    type: EntityType


FIRST_LAYER_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "block_id": {"type": "integer"},
        "key_entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["person", "place", "organization", "object", "concept"],
                    },
                },
                "required": ["name", "type"],
                "additionalProperties": False,
            },
        },
        "timeline_markers": {"type": "array", "items": {"type": "string"}},
        "positional_label": {
            "type": "string",
            "enum": ["начало", "первая треть", "середина", "вторая половина", "конец"],
        },
    },
    "required": ["block_id", "key_entities", "timeline_markers", "positional_label"],
    "additionalProperties": False,
}

SUMMARY_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "position_label": {
            "type": "string",
            "enum": ["начало", "первая треть", "середина", "вторая половина", "конец"],
        },
        "section_title": {"type": ["string", "null"]},
        "summary": {"type": "string"},
        "connection_to_previous": {"type": ["string", "null"]},
        "key_entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["person", "place", "organization", "object", "concept"],
                    },
                },
                "required": ["name", "type"],
                "additionalProperties": False,
            },
        },
        "timeline_markers": {"type": "array", "items": {"type": "string"}},
        "summary_for_next": {"type": "string"},
    },
    "required": [
        "position_label",
        "section_title",
        "summary",
        "connection_to_previous",
        "key_entities",
        "timeline_markers",
        "summary_for_next",
    ],
    "additionalProperties": False,
}


def _strip_markdown_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    return text


def parse_json_response(content: str, schema: dict[str, Any]) -> Any:
    text = _strip_markdown_fence(content)

    parsed = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(f"Невалидный JSON, пробую repair_json: {text}")
        logger.warning(e)
        try:
            repaired = repair_json(text)
            parsed = json.loads(repaired)
            logger.warning(f"JSON синтаксически восстановлен. Было: {text!r} -> Стало: {repaired!r}")
        except Exception as e2:
            logger.error(f"ОШИБКА JSON формат (не удалось починить) {text}")
            logger.error(e2)
            return None

    try:
        jsonschema.validate(parsed, schema)
    except jsonschema.ValidationError as e:
        logger.error(f"JSON распарсился, но не соответствует схеме (вероятно, repair_json исказил структуру): {parsed}")
        logger.error(e)
        return None

    return parsed


def parse_entities(raw_entities: list[dict] | None) -> list[EntityItem]:
    result: list[EntityItem] = []
    for item in raw_entities or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        etype = item.get("type")
        if isinstance(name, str) and name and isinstance(etype, str) and etype:
            result.append(EntityItem(name=name, type=etype))
    return result


class Chunk:
    def __init__(
        self,
        text: str,
        positional_label: str,
        positional_index: str,
        section_title: str | None,
        connection_to_previous: str | None,
        key_entities: list[EntityItem],
        timeline_markers: list[str],
    ):
        self.text = text
        self.positional_label = positional_label
        self.positional_index = positional_index
        self.section_title = section_title
        self.connection_to_previous = connection_to_previous
        self.key_entities = key_entities
        self.timeline_markers = timeline_markers

    def format_to_embed(self) -> str:
        parts: list[str] = []

        if self.section_title:
            parts.append(f"Раздел: {self.section_title}")

        positional_bits = []
        if self.positional_label:
            positional_bits.append(self.positional_label)
        if self.positional_index:
            positional_bits.append(f"блок {self.positional_index}")
        if positional_bits:
            parts.append(f"Позиция: {', '.join(positional_bits)}")

        if self.connection_to_previous:
            parts.append(f"Связь с предыдущим: {self.connection_to_previous}")

        if self.key_entities:
            entities_str = ', '.join(
                f"{e.name} ({e.type})" for e in self.key_entities
            )
            parts.append(f"Ключевые сущности: {entities_str}")

        if self.timeline_markers:
            parts.append(f"Временные маркеры: {', '.join(self.timeline_markers)}")

        header = "\n".join(parts)
        body = self.text or ""

        if header:
            return f"{header}\n\n{body}".strip()
        return body.strip()


class SberClient:
    def __init__(
        self,
        user_id: int,
        base_url: str = settings.gigachat_url,
        model_name: str = settings.gigachat_model,
        verify_ssl_certs: bool = False,
        timeout: int | float = settings.timeout,
    ):
        self.user_id = user_id
        self.base_url = base_url
        self.model_name = model_name
        self.verify_ssl_certs = verify_ssl_certs
        self.timeout = timeout
        self.client: GigaChatAsyncClient | None = None

    async def __aenter__(self):
        self.client = GigaChatAsyncClient(
            credentials=settings.gigachat_auth_key,
            base_url=self.base_url,
            verify_ssl_certs=self.verify_ssl_certs,
            model=self.model_name,
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()
            self.client = None

    async def _achat_json_with_retry(self, llm_request: Chat, schema: dict[str, Any], retries: int = JSON_RETRY_ATTEMPTS) -> dict:
        messages = list(llm_request.messages)

        for attempt in range(retries + 1):
            completion = await self.client.achat(llm_request)
            content = completion.choices[0].message.content
            parsed = parse_json_response(content, schema)

            if parsed is not None:
                return parsed

            if attempt < retries:
                logger.warning(f"Попытка {attempt + 1}/{retries} не дала валидный JSON по схеме, повторяю запрос")
                messages = messages + [
                    Messages(role=ROLE_ASSISTANT, content=content),
                    Messages(
                        role=ROLE_USER,
                        content="Ответ выше не является валидным JSON по заданной схеме. Верни ТОЛЬКО корректный JSON строго по схеме, без каких-либо дополнительных полей, комментариев или текста вне JSON.",
                    ),
                ]
                llm_request = llm_request.copy(update={"messages": messages})

        logger.error("Не удалось получить валидный JSON после всех retry")
        return {}

    async def create_request_to_db(self, history: list[str]) -> str:
        if not history:
            raise ValueError("history не может быть пустой")

        if len(history) == 1:
            return history[0]

        formatted_history = format_history(history)

        system_prompt = Messages(role=ROLE_SYSTEM, content=SYSTEM_QUERY_PROMPT)

        query_prompt = Messages(
            role=ROLE_USER,
            content=formatted_history,
        )

        full_request = Chat(
            model=self.model_name,
            function_call="auto",
            messages=[system_prompt, query_prompt],
        )

        response = await self.client.achat(full_request)
        return response.choices[0].message.content

    async def _build_answer_messages(
        self, messages: list[str], top_retrieved: list[str]
    ) -> Chat:
        if not messages:
            raise ValueError("messages не может быть пустым")

        chat_messages = [
            Messages(
                role=ROLE_USER if i % 2 == 0 else ROLE_ASSISTANT,
                content=message,
            )
            for i, message in enumerate(messages)
        ]
        chat_messages[-1].content += f"\nИспользуй для ответа данные фрагменты: {top_retrieved}\n"

        return Chat(
            model=self.model_name,
            function_call="auto",
            messages=[
                Messages(role=ROLE_SYSTEM, content=SYSTEM_ANSWER_PROMPT),
                *chat_messages,
            ],
        )

    async def query(self, history: list[str], title: str) -> str:
        logger.info(f"history: {history}")

        db_request = await self.create_request_to_db(history)
        logger.info(f"request to db: {db_request}")

        embedder = BGEEmbedder(self.user_id, title)
        top_retrieved = await embedder.query(db_request)
        logger.info(f"top_retrieved: {top_retrieved}")

        llm_request = await self._build_answer_messages(history, top_retrieved)

        try:
            response = await self.client.achat(llm_request)
        except Exception:
            logger.exception("Ошибка при обращении к GigaChat")
            raise

        logger.info(f"response: {response}")
        return response.choices[0].message.content

    async def format_text_to_chunk(self, chunk_texts: list[str]):
        return [
            Chunk(
                chunk_texts[i],
                positional_label="",
                positional_index=f"{i} / {len(chunk_texts)}",
                section_title="",
                connection_to_previous="",
                key_entities=[],
                timeline_markers=[],
            )
            for i in range(len(chunk_texts))
        ]

    async def _build_summary_query(self, chunks: list[Chunk], previous_summary: str) -> Chat:
        blocks = []
        for i, chunk in enumerate(chunks):
            meta = []
            if chunk.positional_index:
                meta.append(f"Позиция: {chunk.positional_index}")
            if chunk.positional_label:
                meta.append(f"Часть документа: {chunk.positional_label}")
            if chunk.section_title:
                meta.append(f"Заголовок секции: {chunk.section_title}")
            if chunk.connection_to_previous:
                meta.append(f"Связь с предыдущим: {chunk.connection_to_previous}")
            if chunk.key_entities:
                entities_str = ', '.join(
                    f"{e.name} ({e.type})" for e in chunk.key_entities
                )
                meta.append(f"Ключевые сущности: {entities_str}")
            if chunk.timeline_markers:
                meta.append(f"Временные маркеры: {', '.join(chunk.timeline_markers)}")

            header = "\n".join(meta)
            blocks.append(f"--- BLOCK {i + 1} / {len(chunks)} ---\n{header}\n\n{chunk.text}")

        user_content = "\n\n".join(blocks)
        if previous_summary:
            user_content += f"\n\nСаммари предыдущих блоков:\n{previous_summary}"

        return Chat(
            model=self.model_name,
            max_tokens=3000,
            temperature=0.0001,
            top_p=0.1,
            response_format=JsonSchemaResponseFormat(
                schema=SUMMARY_JSON_SCHEMA,
                strict=True,
            ),
            messages=[
                Messages(role=ROLE_SYSTEM, content=SYSTEM_CHUNKING_PROMPT),
                Messages(role=ROLE_USER, content=user_content),
            ],
        )

    async def upper_layer_summary(self, chunks: list[Chunk], batch_size: int) -> list[Chunk]:
        result_chunks = []
        previous_summary = ""
        for i in range(0, len(chunks), batch_size):
            llm_request = await self._build_summary_query(chunks[i: min(i + batch_size, len(chunks))], previous_summary)

            response = await self._achat_json_with_retry(llm_request, SUMMARY_JSON_SCHEMA)

            if not response:
                logger.warning(f"Пропускаю блок {i + 1}: не удалось получить валидный JSON от модели")
                continue

            logger.info(f"Саммари блоков {i + 1}-{i + batch_size}: {response.get('summary')}")

            result_chunks.append(Chunk(
                response.get("summary", ""),
                response.get("position_label", ""),
                f"{i + 1} / {len(chunks)}",
                response.get("section_title"),
                response.get("connection_to_previous"),
                parse_entities(response.get("key_entities")),
                response.get("timeline_markers") or [],
            ))
            previous_summary = response.get("summary_for_next", "")

        return result_chunks