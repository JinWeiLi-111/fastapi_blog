from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    database_url: str
    secret_key: SecretStr
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    s3_bucket_name: str
    s3_region: str
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_endpoint_url: str | None = None

    max_upload_size_bytes: int = 5 * 1024 * 1024  # 5 megabytesw
    posts_per_page: int = 10

    reset_token_expire_minutes: int = 60

    mail_server: str = "localhost"
    mail_port: int = 587
    mail_username: str = ""
    mail_password: SecretStr = SecretStr("")
    mail_from: str = "noreply@example.com"
    mail_use_tls: bool = True

    frontend_url: str = "http://localhost:8000"

    # agent features
    ai_encryption_key: SecretStr | None = None
    ai_default_max_tokens: int = 1024
    ai_default_temperature: float = 0.7
    ai_agent_max_iterations: int = 8


settings = Settings()  # type: ignore # Loaded from .env file

# email_token = "a7ac97a11445696c8a538915778f378d"
