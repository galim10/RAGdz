import logging
import re
import aiohttp
from config import settings

logger = logging.getLogger(__name__)

wiki_url = settings.wiki_url
samples_limit = settings.samples_limit
wiki_headers = settings.wiki_headers


class WikiClient:
    def __init__(self, url: str = wiki_url, headers: dict = wiki_headers):
        self.base_url = url
        self.headers = headers or {}
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=30)
        self.session = aiohttp.ClientSession(headers=self.headers, timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def _make_request(self, params: dict) -> dict:
        if self.session is None:
            raise RuntimeError(
                "WikiClient session is not initialized. "
                "Use 'async with WikiClient(...) as client:'"
            )
        logger.info(f"making request with params: {params}")
        async with self.session.get(self.base_url, params=params) as response:
            response.raise_for_status()
            data = await response.json()
        if "error" in data:
            raise ValueError(f"Вики-API вернул ошибку: {data['error']}")
        return data

    async def get_samples(self, title: str) -> list[str]:
        logger.info(f"getting samples for {title}")
        params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": title,
            "srnamespace": 0,
            "srlimit": samples_limit,
            "srinfo": "totalhits",
        }
        data = await self._make_request(params)
        result = [sample["title"] for sample in data["query"]["search"]]
        logger.info(f"Found {len(result)} samples\n\n{result}")
        return result

    async def get_page_text(self, title: str) -> str:
        logger.info(f"getting page text for {title}")
        params = {
            "action": "parse",
            "format": "json",
            "prop": "text",
            "page": title,
        }
        data = await self._make_request(params)
        if "parse" not in data:
            raise ValueError("Страница не найдена на Викитеке")
        html = data["parse"]["text"]["*"]
        text = re.sub(r"<[^<]+?>", "", html)
        return text