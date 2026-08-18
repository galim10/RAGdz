import logging
from typing import Any

from openai import AsyncOpenAI

from config import settings
from rag.bge_embedder import BGEEmbedder
from clients.prompts import (
    SYSTEM_QUERY_PROMPT,
    SYSTEM_ANSWER_PROMPT,
    SYSTEM_CHUNKING_PROMPT,
    format_history,
)
from clients.llm_common import (
    JSON_RETRY_ATTEMPTS,
    SUMMARY_JSON_SCHEMA,
    Chunk,
    parse_json_response,
    parse_entities,
)

logger = logging.getLogger(__name__)

ROLE_SYSTEM = "system"
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"


class OpenRouterClient:
    """LLM-клиент на базе OpenAI-совместимого API OpenRouter.

    Интерфейс повторяет SberClient (query / format_text_to_chunk /
    upper_layer_summary), поэтому клиенты взаимозаменяемы и
    переключаются через settings.llm_client.
    """

    def __init__(
        self,
        user_id: int,
        base_url: str = settings.openrouter_base_url,
        model_name: str = settings.openrouter_model,
        timeout: int | float = settings.timeout,
    ):
        self.user_id = user_id
        self.base_url = base_url
        self.model_name = model_name
        self.timeout = timeout
        self.client: AsyncOpenAI | None = None

    async def __aenter__(self):
        self.client = AsyncOpenAI(
            api_key=settings.openrouter_api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.close()
            self.client = None

    @staticmethod
    def _history_to_messages(history: list[str]) -> list[dict]:
        return [
            {
                "role": ROLE_USER if i % 2 == 0 else ROLE_ASSISTANT,
                "content": message,
            }
            for i, message in enumerate(history)
        ]

    async def _achat_json_with_retry(
        self, messages: list[dict], schema: dict[str, Any], retries: int = JSON_RETRY_ATTEMPTS
    ) -> dict:
        """Запрашивает JSON по схеме, при невалидном ответе повторяет запрос.

        Не полагается на response_format (не все модели OpenRouter его
        поддерживают) — валидация и retry выполняются вручную.
        """
        for attempt in range(retries + 1):
            completion = await self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
            )
            content = completion.choices[0].message.content or ""
            parsed = parse_json_response(content, schema)

            if parsed is not None:
                return parsed

            if attempt < retries:
                logger.warning(f"Попытка {attempt + 1}/{retries} не дала валидный JSON по схеме, повторяю запрос")
                messages = messages + [
                    {"role": ROLE_ASSISTANT, "content": content},
                    {
                        "role": ROLE_USER,
                        "content": (
                            "Ответ выше не является валидным JSON по заданной схеме. "
                            "Верни ТОЛЬКО корректный JSON строго по схеме, без каких-либо "
                            "дополнительных полей, комментариев или текста вне JSON."
                        ),
                    },
                ]

        logger.error("Не удалось получить валидный JSON после всех retry")
        return {}

    async def create_request_to_db(self, history: list[str]) -> str:
        if not history:
            raise ValueError("history не может быть пустой")

        if len(history) == 1:
            return history[0]

        formatted_history = format_history(history)

        response = await self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": ROLE_SYSTEM, "content": SYSTEM_QUERY_PROMPT},
                {"role": ROLE_USER, "content": formatted_history},
            ],
        )
        return response.choices[0].message.content

    async def _build_answer_messages(
        self, messages: list[str], top_retrieved: list[str]
    ) -> list[dict]:
        if not messages:
            raise ValueError("messages не может быть пустым")

        chat_messages = self._history_to_messages(messages)
        chat_messages[-1]["content"] += f"\nИспользуй для ответа данные фрагменты: {top_retrieved}\n"

        return [
            {"role": ROLE_SYSTEM, "content": SYSTEM_ANSWER_PROMPT},
            *chat_messages,
        ]

    async def query(self, history: list[str], title: str) -> str:
        logger.info(f"history: {history}")

        db_request = await self.create_request_to_db(history)
        logger.info(f"request to db: {db_request}")

        embedder = BGEEmbedder(self.user_id, title)
        top_retrieved = await embedder.query(db_request)
        logger.info(f"top_retrieved: {top_retrieved}")

        answer_messages = await self._build_answer_messages(history, top_retrieved)

        try:
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=answer_messages,
            )
        except Exception:
            logger.exception("Ошибка при обращении к OpenRouter")
            raise

        logger.info(f"response: {response}")
        return response.choices[0].message.content

    async def format_text_to_chunk(self, chunk_texts: list[str]) -> list[Chunk]:
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

    async def _build_summary_query(self, chunks: list[Chunk], previous_summary: str) -> list[dict]:
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

        return [
            {"role": ROLE_SYSTEM, "content": SYSTEM_CHUNKING_PROMPT},
            {"role": ROLE_USER, "content": user_content},
        ]

    async def upper_layer_summary(self, chunks: list[Chunk], batch_size: int) -> list[Chunk]:
        result_chunks = []
        previous_summary = ""
        for i in range(0, len(chunks), batch_size):
            llm_messages = await self._build_summary_query(
                chunks[i: min(i + batch_size, len(chunks))], previous_summary
            )

            response = await self._achat_json_with_retry(llm_messages, SUMMARY_JSON_SCHEMA)

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
