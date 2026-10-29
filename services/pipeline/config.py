from pydantic import Field, SecretStr

from services.common.settings_base import ServiceSettings


class Settings(ServiceSettings):
    gemini_api_key: SecretStr = Field(json_schema_extra={"secret": True})
    database_url: SecretStr = Field(json_schema_extra={"secret": True})
    s3_secret_key: SecretStr = Field(json_schema_extra={"secret": True})

    gemini_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    extraction_max_retries: int = 3

    s3_endpoint_url: str
    s3_access_key: str
    s3_bucket: str
    s3_region: str = "us-east-1"

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"


settings = Settings()  # type: ignore[call-arg]
