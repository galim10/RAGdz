import nltk
import re
import asyncio
import numpy as np

from config import settings
from sentence_transformers import util
from rag.bge_embedder import get_model

async def encode_async(sentences):
    return await asyncio.to_thread(get_model().encode, sentences)

async def split_into_sentences(text: str):
    text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)
    sentences = nltk.sent_tokenize(text, language='russian')
    return sentences

async def chunk_text(text: str, min_chunk_size: int = settings.min_chunk_size, max_chunk_size: int = settings.max_chunk_size, overlap: int = settings.overlap, breakpoint_threshold: int = settings.chunks_breakpoint_threshold):
    sentences = await split_into_sentences(text)

    if not sentences:
        return []

    temp = [""] + sentences + [""]
    embeddings = await encode_async([temp[i - 1] + temp[i] + temp[i + 1] for i in range(1, len(temp) - 1)])
    dense_embeddings = embeddings["dense_vecs"]

    distances = []
    for i in range(len(dense_embeddings) - 1):
        distances.append(1 - util.cos_sim(dense_embeddings[i], dense_embeddings[i + 1]))

    if distances:
        threshold = np.percentile(distances, 100 - breakpoint_threshold)
    else:
        threshold = 0

    chunks = [sentences[0]]
    current_len = len(sentences[0])
    for i in range(len(distances)):
        next_sentence = sentences[i + 1]
        distance = distances[i]

        if current_len < min_chunk_size:
            chunks[-1] += " " + next_sentence
            current_len += len(next_sentence) + 1
            continue

        if distance > threshold or current_len + len(next_sentence) > max_chunk_size:
            chunks.append("")
            current_len = 0

        chunks[-1] += " " + next_sentence
        current_len += len(next_sentence) + 1

    for i in range(len(chunks)):
        if i + 1 < len(chunks):
            next_chunk_len = len(chunks[i + 1])
            next_adding_len = int(overlap / 100 * next_chunk_len)
            right_addition = chunks[i + 1][:next_adding_len] + "..."
        else:
            right_addition = "..."

        if i > 0:
            prev_chunk_len = len(chunks[i - 1])
            prev_adding_len = int(overlap / 100 * prev_chunk_len)
            left_addition = "..." + chunks[i - 1][-prev_adding_len:]
        else:
            left_addition = "..."

        chunks[i] = left_addition + chunks[i] + right_addition

    return chunks

