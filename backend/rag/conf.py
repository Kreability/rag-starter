"""Central RAG configuration.

Every knob is an environment variable so the whole system is plug-and-play:
point the OpenAI-compatible base URL at OpenAI, Azure, vLLM, STACKIT, Ollama,
OpenRouter, ... and nothing else has to change.

Ported/condensed from `libs/rag-core-lib/.../impl/settings/*` and
`libs/rag-core-api/.../impl/settings/*` of the upstream rag-template.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    """Chat model. Any OpenAI-compatible endpoint."""

    model_config = SettingsConfigDict(env_prefix="LLM_", case_sensitive=False, extra="ignore")

    model: str = Field(default="gpt-4o-mini")
    api_key: SecretStr = Field(default=SecretStr(""))
    base_url: str | None = Field(default=None)
    temperature: float = Field(default=0.0)
    top_p: float = Field(default=0.1)
    max_tokens: int = Field(default=2048)
    timeout: float = Field(default=120.0)
    # Bounded fan-out so a big ingest cannot melt the provider quota.
    max_concurrency: int = Field(default=8)


class EmbedderSettings(BaseSettings):
    """Dense embeddings. Must match the Qdrant collection's vector size."""

    model_config = SettingsConfigDict(env_prefix="EMBEDDER_", case_sensitive=False, extra="ignore")

    # "openai" = any OpenAI-compatible endpoint; "local" = in-process FastEmbed
    # (no API key, no extra service). Use "local" when the chat provider has no
    # embedding models, e.g. OpenRouter, Groq, DeepSeek.
    provider: str = Field(default="openai")
    local_model: str = Field(default="BAAI/bge-small-en-v1.5")

    model: str = Field(default="text-embedding-3-small")
    api_key: SecretStr = Field(default=SecretStr(""))
    base_url: str | None = Field(default=None)
    dimensions: int = Field(default=1536)
    batch_size: int = Field(default=64)
    timeout: float = Field(default=120.0)


class SparseEmbedderSettings(BaseSettings):
    """Sparse (keyword-ish) embeddings powering Qdrant hybrid search."""

    model_config = SettingsConfigDict(env_prefix="SPARSE_EMBEDDER_", case_sensitive=False, extra="ignore")

    model: str = Field(default="Qdrant/bm25")
    enabled: bool = Field(default=True)


class VectorDBSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VECTOR_DB_", case_sensitive=False, extra="ignore")

    url: str = Field(default="http://qdrant:6333")
    api_key: SecretStr = Field(default=SecretStr(""))
    collection_name: str = Field(default="rag")
    # HYBRID needs the sparse embedder; DENSE works without it.
    retrieval_mode: str = Field(default="HYBRID")
    timeout: float = Field(default=60.0)
    prefer_grpc: bool = Field(default=False)


class RetrieverSettings(BaseSettings):
    """Per-content-type retrieval budgets. Ported from RetrieverSettings upstream."""

    model_config = SettingsConfigDict(env_prefix="RETRIEVER_", case_sensitive=False, extra="ignore")

    threshold: float = Field(default=0.5)
    k_documents: int = Field(default=10)
    table_threshold: float = Field(default=0.37)
    table_k_documents: int = Field(default=10)
    summary_threshold: float = Field(default=0.5)
    summary_k_documents: int = Field(default=10)
    image_threshold: float = Field(default=0.5)
    image_k_documents: int = Field(default=10)
    total_k_documents: int = Field(default=10)


class RerankerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RERANKER_", case_sensitive=False, extra="ignore")

    enabled: bool = Field(default=True)
    model: str = Field(default="ms-marco-MiniLM-L-12-v2")
    k_documents: int = Field(default=5)
    min_relevance_score: float = Field(default=0.001)


class ChunkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHUNKER_", case_sensitive=False, extra="ignore")

    max_size: int = Field(default=1000)
    overlap: int = Field(default=100)


class SummarizerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SUMMARIZER_", case_sensitive=False, extra="ignore")

    enabled: bool = Field(default=True)
    maximum_input_size: int = Field(default=8000)
    maximum_concurrency: int = Field(default=4)
    maximum_number_of_retries: int = Field(default=5)
    retry_base_delay: float = Field(default=2.0)


class ChatHistorySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHAT_HISTORY_", case_sensitive=False, extra="ignore")

    limit: int = Field(default=4)
    reverse: bool = Field(default=True)


class LangfuseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LANGFUSE_", case_sensitive=False, extra="ignore")

    public_key: SecretStr = Field(default=SecretStr(""))
    secret_key: SecretStr = Field(default=SecretStr(""))
    host: str = Field(default="https://cloud.langfuse.com")

    @property
    def enabled(self) -> bool:
        return bool(self.public_key.get_secret_value() and self.secret_key.get_secret_value())


class S3Settings(BaseSettings):
    """Original-document storage. MinIO locally, any S3 in production."""

    model_config = SettingsConfigDict(env_prefix="S3_", case_sensitive=False, extra="ignore")

    endpoint: str = Field(default="http://minio:9000")
    access_key_id: SecretStr = Field(default=SecretStr(""))
    secret_access_key: SecretStr = Field(default=SecretStr(""))
    bucket: str = Field(default="rag-documents")
    region: str = Field(default="us-east-1")
    # Pre-signed download links handed to the frontend for citations.
    presign_ttl_seconds: int = Field(default=900)


class IngestionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INGESTION_", case_sensitive=False, extra="ignore")

    max_file_size_mb: int = Field(default=50)
    # Prefer Docling when the optional extra is installed; MarkItDown otherwise.
    prefer_docling: bool = Field(default=True)
    ocr_languages: str = Field(default="eng")
    sitemap_max_pages: int = Field(default=200)
    # SSRF guard: only these schemes may be fetched for URL/sitemap sources.
    allowed_url_schemes: str = Field(default="https,http")


class ErrorMessages(BaseSettings):
    """User-facing chat fallbacks. Ported from upstream ErrorMessages."""

    model_config = SettingsConfigDict(env_prefix="ERROR_MESSAGES_", case_sensitive=False, extra="ignore")

    no_documents_message: str = Field(
        default="I couldn't find anything in the knowledge base that answers this."
    )
    no_or_empty_collection: str = Field(
        default="The knowledge base is empty. Upload a document first."
    )
    empty_message: str = Field(default="Please ask a question.")
    harmful_question: str = Field(default="I can't help with that.")


class RagConfig:
    """One handle for every settings group."""

    def __init__(self) -> None:
        self.llm = LLMSettings()
        self.embedder = EmbedderSettings()
        self.sparse_embedder = SparseEmbedderSettings()
        self.vector_db = VectorDBSettings()
        self.retriever = RetrieverSettings()
        self.reranker = RerankerSettings()
        self.chunker = ChunkerSettings()
        self.summarizer = SummarizerSettings()
        self.chat_history = ChatHistorySettings()
        self.langfuse = LangfuseSettings()
        self.s3 = S3Settings()
        self.ingestion = IngestionSettings()
        self.errors = ErrorMessages()


@lru_cache(maxsize=1)
def get_config() -> RagConfig:
    """Process-wide singleton. Cached so env parsing happens once."""
    return RagConfig()
