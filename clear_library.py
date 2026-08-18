"""Скрипт полной очистки данных.

Удаляет:
  - все коллекции Chroma (векторная БД из settings.embeddings_dir);
  - все книги пользователей (содержимое settings.books_dir);
  - временные файлы кэша (содержимое settings.cache_dir).

ВНИМАНИЕ: операция необратима. Запускать осознанно, когда данные «перепутаны»
и нужно начать заново.
"""

import shutil
from pathlib import Path

import chromadb

from config import settings


def clear_chroma() -> int:
    client = chromadb.PersistentClient(path=str(settings.embeddings_dir))
    collections = client.list_collections()
    for col in collections:
        client.delete_collection(col.name)
        print(f"  Удалена коллекция: {col.name}")

    emb_dir = Path(settings.embeddings_dir)
    purged = 0
    for entry in emb_dir.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
            purged += 1

    print(f"Chroma очищена: удалено коллекций — {len(collections)}, сегментов — {purged}")
    return len(collections)


def _clear_dir_contents(path: Path) -> int:
    if not path.exists():
        return 0

    count = 0
    for entry in path.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink()
        count += 1
    return count


def clear_books() -> int:
    count = _clear_dir_contents(Path(settings.books_dir))
    print(f"Книги очищены: удалено объектов — {count}")
    return count


def clear_cache() -> int:
    count = _clear_dir_contents(Path(settings.cache_dir))
    print(f"Кэш очищен: удалено объектов — {count}")
    return count


if __name__ == "__main__":
    print("Начинаю полную очистку данных...")
    clear_chroma()
    clear_books()
    clear_cache()
    print("Очистка завершена.")
