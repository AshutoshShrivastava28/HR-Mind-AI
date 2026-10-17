from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "HR Mind AI"
    app_env: str = "development"

    # AI and vector database
    groq_api_key: str = ""
    tavily_api_key: str = ""
    pinecone_api_key: str = ""
    pinecone_index_name: str = "fde-hr-policy-rag"
    pinecone_namespace: str = "company-hr-kb"
    embedding_model: str = "all-minilm-l6-v2"
    top_k: int = 4
    max_retries: int = 1

    # Authentication
    admin_email: str = ""
    public_base_url: str = ""
    auth_db_path: str = str(BASE_DIR / "data" / "auth.db")
    conversation_db_path: str = str(BASE_DIR / "data" / "conversations.db")

    # Email delivery for verification and password reset
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_use_tls: bool = True

    # Retained temporarily for compatibility with existing settings
    admin_api_key: str = "change-me-in-production"

    # Existing application storage
    audit_db_path: str = str(BASE_DIR / "data" / "audit.db")
    upload_dir: str = str(BASE_DIR / "uploads")
    sample_kb_dir: str = str(BASE_DIR / "data" / "sample_kb")

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
