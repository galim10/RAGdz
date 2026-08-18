from config import settings

from .openrouter_client import OpenRouterClient
from .sber_client import SberClient
from .wiki_client import WikiClient

LLMClient = SberClient | OpenRouterClient


def get_llm_client(user_id: int) -> LLMClient:
    if settings.llm_client == "gigachat":
        return SberClient(user_id)
    if settings.llm_client == "openrouter":
        return OpenRouterClient(user_id)
    raise ValueError(f"Неизвестный settings.llm_client: {settings.llm_client!r}")
