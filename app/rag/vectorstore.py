import time

import numpy as np
from fastembed import TextEmbedding
from langchain_core.embeddings import Embeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec

from app.core.config import get_settings

settings = get_settings()

_embeddings = None
_index = None
_vectorstores: dict[str, PineconeVectorStore] = {}

EMBEDDING_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "all-minilm-l6-v2": 384,
}


class FastEmbedEmbeddings(Embeddings):
    """Normalized 384-dimensional local embeddings using ONNX/FastEmbed."""

    def __init__(self):
        self.model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")

    @staticmethod
    def _normalize(vector):
        vector = np.asarray(vector, dtype=np.float32)
        norm = np.linalg.norm(vector)

        return vector.tolist() if norm == 0 else (vector / norm).tolist()

    def embed_documents(self, texts):
        return [self._normalize(vector) for vector in self.model.embed(texts)]

    def embed_query(self, text):
        return self._normalize(next(self.model.embed([text])))


def get_embedding_dimension(model_name: str | None = None) -> int:
    name = (model_name or settings.embedding_model or "").strip().lower()

    if name in EMBEDDING_DIMENSIONS:
        return EMBEDDING_DIMENSIONS[name]

    if "text-embedding-3-small" in name or "text-embedding-ada-002" in name:
        return 1536

    if "text-embedding-3-large" in name:
        return 3072

    if "all-minilm" in name:
        return 384

    raise ValueError(
        f"Unsupported embedding model '{name}'. "
        "Add its dimension to EMBEDDING_DIMENSIONS."
    )


def get_embeddings():
    global _embeddings

    if _embeddings is None:
        _embeddings = FastEmbedEmbeddings()

    return _embeddings


def ensure_index():
    global _index

    if _index is not None:
        return _index

    if not settings.pinecone_api_key:
        raise RuntimeError("PINECONE_API_KEY is missing")

    if not settings.pinecone_index_name:
        raise RuntimeError("PINECONE_INDEX_NAME is missing")

    client = Pinecone(api_key=settings.pinecone_api_key)

    names = [item["name"] for item in client.list_indexes()]

    desired_dimension = get_embedding_dimension()
    index_name = settings.pinecone_index_name

    if index_name in names:
        info = client.describe_index(index_name)

        dimension = getattr(info, "dimension", None)

        if dimension is None and isinstance(info, dict):
            dimension = info.get("dimension")

        if dimension is None:
            raise RuntimeError(
                f"Could not verify dimension of Pinecone index "
                f"'{index_name}'. No changes made."
            )

        if dimension != desired_dimension:
            raise RuntimeError(
                f"Pinecone index '{index_name}' has dimension "
                f"{dimension}; embedding model requires "
                f"{desired_dimension}. The existing index was "
                "not deleted or modified."
            )

    else:
        client.create_index(
            name=index_name,
            dimension=desired_dimension,
            metric="cosine",
            spec=ServerlessSpec(
                cloud="aws",
                region="us-east-1",
            ),
        )

        deadline = time.monotonic() + 120

        while time.monotonic() < deadline:
            info = client.describe_index(index_name)
            status = getattr(info, "status", None)

            ready = (
                status.get("ready", False)
                if isinstance(status, dict)
                else getattr(status, "ready", False)
            )

            if ready:
                break

            time.sleep(2)

        else:
            raise TimeoutError(
                f"Pinecone index '{index_name}' did not "
                "become ready within 120 seconds."
            )

    _index = client.Index(index_name)

    return _index


def get_vectorstore(namespace: str | None = None):
    ns = namespace or settings.pinecone_namespace

    if not ns:
        raise RuntimeError("PINECONE_NAMESPACE is missing")

    if ns not in _vectorstores:
        _vectorstores[ns] = PineconeVectorStore(
            index=ensure_index(),
            embedding=get_embeddings(),
            namespace=ns,
        )

    return _vectorstores[ns]


def get_retriever(namespace: str | None = None, k: int | None = None):
    candidate_count = settings.top_k if k is None else max(1, int(k))
    return get_vectorstore(namespace).as_retriever(search_kwargs={"k": candidate_count})


def add_documents(
    chunks,
    namespace: str | None = None,
    ids: list[str] | None = None,
):
    """Index chunks with predefined IDs so failed uploads can be rolled back."""
    if ids is not None and len(ids) != len(chunks):
        raise ValueError("The number of vector IDs must match the number of chunks.")

    return get_vectorstore(namespace).add_documents(chunks, ids=ids)


def delete_vectors(
    ids: list[str],
    namespace: str | None = None,
) -> None:
    if ids:
        get_vectorstore(namespace).delete(ids=ids)
