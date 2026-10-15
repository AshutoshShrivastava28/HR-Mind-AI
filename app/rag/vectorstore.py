import time

import numpy as np
from fastembed import TextEmbedding
from langchain_core.embeddings import Embeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec

from app.core.config import get_settings

settings = get_settings()

_embeddings = None
_vectorstore = None

EMBEDDING_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "all-minilm-l6-v2": 384,
}


class FastEmbedEmbeddings(Embeddings):
    """Generate normalized embeddings locally using FastEmbed."""

    def __init__(self):
        self.model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")

    @staticmethod
    def _normalize(vector):
        vector = np.asarray(vector, dtype=np.float32)
        norm = np.linalg.norm(vector)

        if norm == 0:
            return vector.tolist()

        return (vector / norm).tolist()

    def embed_documents(self, texts):
        vectors = self.model.embed(texts)
        return [self._normalize(vector) for vector in vectors]

    def embed_query(self, text):
        vector = next(self.model.embed([text]))
        return self._normalize(vector)


def get_embedding_dimension(model_name: str | None = None) -> int:
    """Return the expected vector dimension for the configured model."""

    name = (model_name or settings.embedding_model or "").strip()

    if not name:
        raise RuntimeError("Embedding model is not configured.")

    normalized = name.lower()

    if normalized in EMBEDDING_DIMENSIONS:
        return EMBEDDING_DIMENSIONS[normalized]

    if "text-embedding-3-small" in normalized:
        return 1536

    if "text-embedding-3-large" in normalized:
        return 3072

    if "text-embedding-ada-002" in normalized:
        return 1536

    if "all-minilm" in normalized:
        return 384

    raise ValueError(
        f"Unsupported embedding model '{name}'. "
        "Add its dimension to EMBEDDING_DIMENSIONS."
    )


def get_embeddings():
    """Create the embedding model once per application process."""

    global _embeddings

    if _embeddings is None:
        _embeddings = FastEmbedEmbeddings()

    return _embeddings


def _get_index_names(client):
    """Return existing Pinecone index names."""

    return [item["name"] for item in client.list_indexes()]


def _get_dimension(index_info):
    """Read the index dimension across supported response formats."""

    if isinstance(index_info, dict):
        return index_info.get("dimension")

    return getattr(index_info, "dimension", None)


def _is_index_ready(index_info):
    """Check whether Pinecone reports an index as ready."""

    if isinstance(index_info, dict):
        status = index_info.get("status")
    else:
        status = getattr(index_info, "status", None)

    if isinstance(status, dict):
        return status.get("ready") is True

    return getattr(status, "ready", False) is True


def ensure_index():
    """
    Verify or create the configured Pinecone index.

    Important:
    This function never deletes an existing index automatically.
    """

    if not settings.pinecone_api_key:
        raise RuntimeError("PINECONE_API_KEY is missing.")

    if not settings.pinecone_index_name:
        raise RuntimeError("PINECONE_INDEX_NAME is missing.")

    desired_dimension = get_embedding_dimension()

    client = Pinecone(api_key=settings.pinecone_api_key)
    index_name = settings.pinecone_index_name

    existing_indexes = _get_index_names(client)

    # Reuse an existing index only when its dimensions match.
    if index_name in existing_indexes:
        index_info = client.describe_index(index_name)
        current_dimension = _get_dimension(index_info)

        if current_dimension is None:
            raise RuntimeError(
                f"Could not verify the dimensions of Pinecone index "
                f"'{index_name}'. No changes were made."
            )

        if current_dimension != desired_dimension:
            raise RuntimeError(
                f"Pinecone index '{index_name}' has dimension "
                f"{current_dimension}, but the configured embedding "
                f"model requires dimension {desired_dimension}. "
                "The existing index was NOT deleted or modified. "
                "Check your embedding model and index configuration "
                "before proceeding."
            )

        return client.Index(index_name)

    # Create an index only when it does not already exist.
    client.create_index(
        name=index_name,
        dimension=desired_dimension,
        metric="cosine",
        spec=ServerlessSpec(
            cloud="aws",
            region="us-east-1",
        ),
    )

    # Wait for creation, but do not wait indefinitely.
    deadline = time.monotonic() + 120

    while time.monotonic() < deadline:
        index_info = client.describe_index(index_name)

        if _is_index_ready(index_info):
            return client.Index(index_name)

        time.sleep(2)

    raise TimeoutError(
        f"Pinecone index '{index_name}' did not become ready "
        "within 120 seconds. Check its status in the Pinecone console."
    )


def get_vectorstore():
    """Initialize the Pinecone vector store once per process."""

    global _vectorstore

    if _vectorstore is None:
        index = ensure_index()

        _vectorstore = PineconeVectorStore(
            index=index,
            embedding=get_embeddings(),
            namespace=settings.pinecone_namespace,
        )

    return _vectorstore


def get_retriever():
    """Return the retriever used by the HR knowledge workflow."""

    return get_vectorstore().as_retriever(search_kwargs={"k": settings.top_k})


def add_documents(chunks):
    """Add document chunks to the configured Pinecone namespace."""

    store = get_vectorstore()
    return store.add_documents(chunks)
