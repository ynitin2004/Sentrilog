from pydantic import Field, SecretStr

from services.common.settings_base import ServiceSettings


class Settings(ServiceSettings):
    database_url: SecretStr = Field(json_schema_extra={"secret": True})
    gemini_api_key: SecretStr = Field(json_schema_extra={"secret": True})

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "sanctions_entries"

    gemini_embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768

    sanctions_vector_threshold: float = 0.55


settings = Settings()  # type: ignore[call-arg]
