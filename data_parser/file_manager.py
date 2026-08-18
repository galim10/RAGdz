import logging

from pathlib import Path
from config import settings
from .converters import FileConverter
from rag.chunker import chunk_text
from rag.bge_embedder import BGEEmbedder
from clients import get_llm_client

logger = logging.getLogger(__name__)

class FileManager:
    @staticmethod
    async def query_to_db(text, user_id, title):
        embedder = BGEEmbedder(user_id=user_id, title=title)
        return await embedder.query(text)


    @staticmethod
    async def add_to_db(text: str, user_id, title):
        chunks_texts = await chunk_text(text)

        async with get_llm_client(user_id) as client:
            chunks = await client.format_text_to_chunk(chunks_texts)
            compression_of_layers = [10, 7]
            layers = [chunks]
            current_layer = 0
            while current_layer < 3:
                if len(layers[-1]) == 1:
                    break

                compression = compression_of_layers[current_layer] if current_layer < len(compression_of_layers) else len(layers[-1])
                new_chunks = await client.upper_layer_summary(layers[-1], compression)
                layers.append(new_chunks)
                current_layer += 1

        final_chunks = []
        for layer in layers:
            for chunk in layer:
                final_chunks.append(chunk.format_to_embed())

        with open(settings.cache_dir / "chunks.txt", "a", encoding='utf-8') as f:
            f.write(f"--------PRECCESSED CHUNKS FOR {title.strip()}\n----------")
            for i, layer in enumerate(layers):
                f.write(f"{i + 1}th layer:\n")
                for j, chunk in enumerate(layer):
                    f.write(f"{j + 1}th chunk:\n{chunk.format_to_embed()}\n")
                f.write("\n")


        embedder = BGEEmbedder(user_id=user_id, title=title)
        await embedder.embed(final_chunks)

    async def add_file(self, bot, user_id: int, file_name: str, file_id: str) -> Path:
        try:
            file_name = Path(file_name).name
            cache_dir = settings.cache_dir
            file_path = cache_dir / file_name
            logger.info(f"Загружаю файл {file_name} для пользователя {user_id} в {file_path}")

            file_info = await bot.get_file(file_id)
            await bot.download_file(file_info.file_path, destination=str(file_path))

            ext = file_name.split(".")[-1].lower()
            if ext == "txt":
                with open(file_path, "r", encoding="utf-8") as f:
                    text = f.read()
            elif ext == "pdf":
                text = await FileConverter.pdf_to_txt_async(str(file_path))
            elif ext == "docx":
                text = await FileConverter.docx_to_txt_async(str(file_path))
            else:
                raise ValueError(f"Неподдерживаемый формат: {ext}")

            title = Path(file_name).stem
            return await self.save_text(user_id, text, title)
        except Exception as e:
            logger.error(f"Ошибка при загрузке файла {file_name} для пользователя {user_id}: {e}", exc_info=True)
            raise


    async def save_text(self, user_id: int, text: str, title: str) -> Path:
        try:
            user_dir = settings.get_user_books_dir(user_id)
            user_dir.mkdir(parents=True, exist_ok=True)

            title = title.replace('/', '_') + '.txt'

            file_path = user_dir / title
            logger.info(f"Сохраняю текст для пользователя {user_id} в {file_path}")

            await self.add_to_db(text, user_id, title)

            file_path.write_text(text, encoding='utf-8')

            logger.info(f"Текст успешно сохранен в {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Ошибка при сохранении текста для пользователя {user_id}: {e}", exc_info=True)
            raise

    @staticmethod
    def get_all_titles() -> list[tuple[int, str]]:
        try:
            books_dir = Path(settings.books_dir)
            result = []
            for user_dir in books_dir.iterdir():
                if not user_dir.is_dir() or not user_dir.name.isdigit():
                    continue
                user_id = int(user_dir.name)
                for title in FileManager.get_titles_from_user(user_id):
                    result.append((user_id, title))
            return result
        except Exception as e:
            logger.error(f"Ошибка при получении всех названий: {e}")
            return []

    @staticmethod
    def get_titles_from_user(user_id: int) -> list[str]:
        try:
            user_dir = Path(settings.get_user_books_dir(user_id))
            return [
                curr_path.name
                for curr_path in user_dir.iterdir()
                if curr_path.is_file() and curr_path.suffix.lower() == ".txt"
            ]
        except Exception as e:
            logger.error(f"Ошибка при получении названий пользователя {user_id}: {e}")
            return []

