import asyncio
import uuid
import logging

import chromadb
from chromadb import EmbeddingFunction

from config import settings
from FlagEmbedding import BGEM3FlagModel

chroma_client = chromadb.PersistentClient(path=settings.embeddings_dir)
logger = logging.getLogger(__name__)

_model = None

def get_model():
    global _model
    if _model is None:
        if settings.embedding_model_dir:
            _model = BGEM3FlagModel(settings.embedding_model_dir)
        else:
            _model = BGEM3FlagModel(settings.embedding_model_name)
    return _model


class BGEEmbedderFunction(EmbeddingFunction):
    def __init__(self):
        self.model = get_model()

    def __call__(self, chunks: list[str]) -> list[list[float]]:
        return self.model.encode(chunks)["dense_vecs"].tolist()

def hash_title(title: str):
    h = 0
    for s in title:
        h = h * 1000 + ord(s)
        h %= 1000000007
    return h


def _collection_name(user_id, title: str) -> str:
    return f"{user_id}_{hash_title(title)}"

class BGEEmbedder:
    def __init__(self, user_id: int, title: str):
        self.collection = chroma_client.get_or_create_collection(
            _collection_name(user_id, title),
            embedding_function=BGEEmbedderFunction(),
            metadata={"hnsw:space": "cosine"},
        )

    async def embed(self, chunks: list[str]) -> None:
        ids = [f"chunk_{uuid.uuid4().hex}" for _ in chunks]
        await asyncio.to_thread(
            self.collection.add,
            documents=chunks,
            ids=ids,
        )

    async def query(self, query_text: str):
        result = await asyncio.to_thread(
            self.collection.query,
            query_texts=query_text,
            n_results=settings.n_results,
        )
        return result["documents"][0]