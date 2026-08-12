from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "sanctions_entries"

    gemini_api_key: str
    gemini_embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768

    sanctions_vector_threshold: float = 0.55


settings = Settings()  # type: ignore[call-arg]
