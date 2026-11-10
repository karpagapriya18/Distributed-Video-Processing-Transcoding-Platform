from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    rabbitmq_url: str = 'amqp://platform:platform@rabbitmq/'
    redis_url: str = 'redis://redis:6379/0'
    media_root: str = '/media'
    api_key: str = 'local-demo-key-change-me'
    internal_token: str = 'local-internal-token-change-me'
    signing_secret: str = 'local-signing-secret-change-me'
    public_base_url: str = 'http://localhost:8000'
    max_upload_bytes: int = 524288000
    consumer_max_retries: int = Field(default=5, ge=0, le=100)
    max_attempts: int = 3
    retry_delay_ms: int = 10000
    processing_timeout: int = 1800
    ffmpeg: str = 'ffmpeg'
    ffprobe: str = 'ffprobe'
    disable_broker: bool = False
    upload_url: str = 'http://upload:8000'
    metadata_url: str = 'http://metadata:8000'
    orchestrator_url: str = 'http://orchestrator:8000'
    worker_url: str = 'http://worker:8000'
    thumbnail_url: str = 'http://thumbnail:8000'
    delivery_url: str = 'http://delivery:8000'


@lru_cache
def settings() -> Settings:
    return Settings()
