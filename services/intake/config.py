from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    s3_endpoint_url: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    s3_region: str = "us-east-1"
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60
    max_upload_bytes: int = 10 * 1024 * 1024
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"


settings = Settings()  # type: ignore[call-arg]
