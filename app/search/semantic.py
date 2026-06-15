from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


MODEL_NAME = "intfloat/multilingual-e5-small"
INDEX_PATH = Path("data/faiss/threads.index")
IDS_PATH = Path("data/faiss/thread_ids.json")
TEXT_LIMIT = 2500


@dataclass(frozen=True)
class SemanticCandidate:
    post_id: int
    score: float


class SemanticSearchUnavailable(RuntimeError):
    pass


def _load_dependencies() -> tuple[Any, Any, Any]:
    try:
        import faiss  # type: ignore
        import numpy as np  # type: ignore
        from sentence_transformers import SentenceTransformer  # type: ignore
    except ImportError as exc:
        raise SemanticSearchUnavailable(
            "Semantic search requires numpy, faiss-cpu, and sentence-transformers."
        ) from exc
    return faiss, np, SentenceTransformer


@lru_cache(maxsize=4)
def load_model(model_name: str = MODEL_NAME, local_files_only: bool = True):
    _, _, sentence_transformer = _load_dependencies()
    return sentence_transformer(model_name, local_files_only=local_files_only)


def encode_passages(model, texts: list[str], batch_size: int = 64):
    _, np, _ = _load_dependencies()
    prepared = [f"passage: {text[:TEXT_LIMIT]}" for text in texts]
    embeddings = model.encode(
        prepared,
        batch_size=batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=True,
    )
    return np.asarray(embeddings, dtype="float32")


def encode_query(model, query: str):
    _, np, _ = _load_dependencies()
    embedding = model.encode(
        [f"query: {query}"],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return np.asarray(embedding, dtype="float32")


def save_index(embeddings, post_ids: list[int], index_path: Path = INDEX_PATH, ids_path: Path = IDS_PATH) -> None:
    faiss, _, _ = _load_dependencies()
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)
    faiss.write_index(index, str(index_path))
    ids_path.write_text(json.dumps(post_ids, ensure_ascii=False), encoding="utf-8")


def semantic_search(
    query: str,
    top_k: int = 50,
    index_path: Path = INDEX_PATH,
    ids_path: Path = IDS_PATH,
    model_name: str = MODEL_NAME,
) -> list[SemanticCandidate]:
    if not index_path.exists() or not ids_path.exists():
        return []
    faiss, _, _ = _load_dependencies()
    try:
        model = load_model(model_name, local_files_only=True)
    except Exception as exc:
        raise SemanticSearchUnavailable(f"Semantic model is not available locally: {exc}") from exc
    index = faiss.read_index(str(index_path))
    post_ids = json.loads(ids_path.read_text(encoding="utf-8"))
    query_embedding = encode_query(model, query)
    scores, positions = index.search(query_embedding, top_k)
    candidates: list[SemanticCandidate] = []
    for score, position in zip(scores[0], positions[0]):
        if position < 0 or position >= len(post_ids):
            continue
        candidates.append(SemanticCandidate(post_id=int(post_ids[position]), score=float(score)))
    return candidates
