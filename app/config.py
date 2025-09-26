"""
Centralized settings using Pydantic Settings (v2).
Newbies: this reads environment variables so secrets are NOT hard-coded.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
import os


class Settings(BaseSettings):
    # ---- API/Auth ----
    REQUIRE_AUTH: bool = Field(False, description="Require Bearer/JWT on requests")
    JWT_AUDIENCE: str | None = None
    JWT_ISSUER: str | None = None
    JWT_PUBLIC_KEY_PEM: str | None = Field(
        default=None,
        description="Paste the RSA/ECDSA public key (PEM) if you verify JWTs locally",
    )

    # ---- DB ----
    DB_URL: str = Field(
        default=os.getenv("DB_URL", "postgresql+asyncpg://postgres:postgres@db:5432/router"),
        description="Async SQLAlchemy URL",
    )

    # ---- Queue ----
    QUEUE_MODE: str = Field(
        default=os.getenv("QUEUE_MODE", "rabbitmq"),  # 'rabbitmq' or 'sqs'
        description="Select 'rabbitmq' or 'sqs'",
    )
    # RabbitMQ
    RABBITMQ_URL: str = Field(default=os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/"))
    RABBITMQ_QUEUE: str = Field(default="router_jobs")
    # AWS SQS
    AWS_REGION: str = Field(default=os.getenv("AWS_REGION", "us-east-1"))
    SQS_URL: str | None = Field(default=os.getenv("SQS_URL", None))

    # ---- Object Storage ----
    # Works for S3 and MinIO (when endpoint_url is set)
    S3_BUCKET: str = Field(default="router-artifacts")
    S3_ENDPOINT_URL: str | None = Field(default=os.getenv("S3_ENDPOINT_URL", None))  # e.g., http://minio:9000
    S3_ACCESS_KEY: str | None = Field(default=os.getenv("S3_ACCESS_KEY", None))
    S3_SECRET_KEY: str | None = Field(default=os.getenv("S3_SECRET_KEY", None))

    # ---- LLM Providers (keys read from env or secrets manager) ----
    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    MISTRAL_API_KEY: str | None = None
    OLLAMA_BASE_URL: str | None = Field(default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))  # optional

    # ---- Routing ----
    PRICE_TABLE_PATH: str = Field(default="config/price_table.yaml")
    DEFAULT_SYSTEM_PROMPT: str = Field(
        default="You are a helpful assistant. Keep answers clear and concise."
    )
    LLM_TIMEOUT_S: int = 60

    # ---- Rate limiting ----
    RATE_LIMIT_ENABLED: bool = Field(default=False)
    RATE_LIMIT_RPM: int = Field(default=30)

    # ---- Observability ----
    OTEL_EXPORTER_OTLP_ENDPOINT: str | None = None  # set to export traces
    SERVICE_NAME: str = Field(default="llm-router")

    # ---- PII Redaction ----
    PII_REDACTION_ENABLED: bool = Field(default=True)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
