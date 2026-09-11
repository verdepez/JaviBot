from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "botGastos API"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/botgastos"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    meta_verify_token: str = ""
    meta_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_api_version: str = "v23.0"
    encryption_secret: str = "botgastos-secret-encryption-key-32bytes"
    admin_phone: str = ""
    access_mode: str = "whitelist"
    allow_free_trial: bool = False
    free_trial_max_expenses: int = 5

    @field_validator("gemini_model", mode="before")
    @classmethod
    def sanitize_gemini_model(cls, v: str) -> str:
        # Si la variable de entorno quedó con un modelo retirado o no disponible en Google API,
        # lo corregimos automáticamente al modelo activo oficial para evitar errores 404 en producción.
        deprecated = {
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-1.5-flash",
            "gemini-1.5-flash-8b",
            "gemini-1.5-pro",
            "gemini-1.0-pro",
        }
        if isinstance(v, str) and v.strip().lower() in deprecated:
            return "gemini-3.6-flash"
        return v or "gemini-3.6-flash"

    @field_validator("database_url", mode="before")
    @classmethod
    def format_database_url(cls, v: str) -> str:
        if isinstance(v, str):
            if v.startswith("postgres://"):
                return v.replace("postgres://", "postgresql+asyncpg://", 1)
            if v.startswith("postgresql://") and not v.startswith("postgresql+asyncpg://"):
                return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()