from openai import AsyncOpenAI

from config import settings
from rag.bge_embedder import BGEEmbedder
from clients.prompts import SYSTEM_QUERY_PROMPT, SYSTEM_ANSWER_PROMPT, format_history
import logging

logger = logging.getLogger(__name__)

ROLE_SYSTEM = "system"
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"


class OpenRouterClient:
    def __init__(
        self,
        user_id: int,
        base_url: str = settings.openrouter_base_url,
        model_name: str = settings.openrouter_model,
    ):
        self.base_url = base_url
        self.user_id = user_id
        self.model_name = model_name
        self.client: AsyncOpenAI | None = None

    async def __aenter__(self):
        self.client = AsyncOpenAI(
            api_key=settings.openrouter_api_key,
            base_url=self.base_url,
            timeout=settings.timeout,
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

    async def create_request_to_db(self, history: list[str]) -> str:
        if not history:
            raise ValueError("history не может быть пустой")

        last_message = history[-1]
        formatted_history = format_history(history)

        system_prompt = {
            "role": ROLE_SYSTEM,
            "content": SYSTEM_QUERY_PROMPT,
        }

        query_prompt = {
            "role": ROLE_USER,
            "content": f,
        }

        response = await self.client.chat.completions.create(
            model=self.model_name,
            messages=[system_prompt, query_prompt],
        )
        return response.choices[0].message.content

    def _build_answer_messages(
        self, messages: list[str], top_retrieved: list[str]
    ) -> list[dict]:
        if not messages:
            raise ValueError("messages не может быть пустым")

        chat_messages = self._history_to_messages(messages)
        chat_messages[-1]["content"] += f"\nИспользуй для ответа данные фрагменты: {top_retrieved}\n"

        system_prompt = {
            "role": ROLE_SYSTEM,
            "content": SYSTEM_ANSWER_PROMPT,
        }

        return [system_prompt, *chat_messages]

    async def query(self, history: list[str], title: str) -> str:
        db_request = await self.create_request_to_db(history)
        logger.info(f"request to db: {db_request}")

        embedder = BGEEmbedder(self.user_id, title)
        top_retrieved = await embedder.query(db_request)

        answer_messages = self._build_answer_messages(history, top_retrieved)

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


    async def _build_chunking_query(self, chunks: list[str]) -> list[dict]:
        pass


    async def chunking_query(self, chunks: list[str], first_layer: bool = True):
        butch_size = 10

        for i in range(0, len(chunks), butch_size):
            if first_layer:
                prompt = self._build_chunking_query(chunks[i : min(len(chunks), i + butch_size)])



