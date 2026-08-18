from pathlib import Path
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    gigachat_auth_key: str
    gigachat_client_id: str
    hf_token: str
    openrouter_api_key: str
    embedding_model_dir: str | None = None

    embedding_model_name: str = "BAAI/bge-m3"
    gigachat_url: str = "https://api.giga.chat/v1"
    gigachat_model: str = "GigaChat-2-Max"

    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openrouter/free"
    titles_limit: int = 5

    chunks_breakpoint_threshold: int = 25
    min_chunk_size: int = 100
    max_chunk_size: int = 3000
    overlap: int = 10
    timeout: int = 30
    max_file_size_mb: int = 50

    n_results: int = 10
    embedding_distance_func: Literal["cosine", "l2", "ip"] = "cosine"

    wiki_url: str = "https://ru.wikisource.org/w/api.php"
    samples_limit: int = 5
    wiki_headers: dict[str, str] = {
        "User-Agent": "mametodlyaychebi@gmail.com"
    }

    data_dir: Path = Path("data_parser")
    books_dir: Path = Path("data_parser/books")
    cache_dir: Path = Path("data_parser/cache")
    embeddings_dir: Path = Path("chroma_db")

    model_config = SettingsConfigDict(env_file='.env')

    def get_user_books_dir(self, user_id: int | str) -> Path:
        return self.books_dir / str(user_id)

settings = Settings()
