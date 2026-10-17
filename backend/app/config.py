from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = 'sqlite:///./transparency.db'
    allowed_origins: str = 'http://localhost:3000'
    admin_api_key: str = ''
    tender_api_url: str = 'https://opendata.tender.gov.mn'
    tender_bearer_token: str = ''
    max_import_bytes: int = 5_000_000
    @field_validator('database_url', mode='before')
    @classmethod
    def psycopg_url(cls, value):
        if isinstance(value, str):
            for prefix in ('postgres://', 'postgresql://'):
                if value.startswith(prefix):
                    return 'postgresql+psycopg://' + value[len(prefix):]
        return value

    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
settings = Settings()
