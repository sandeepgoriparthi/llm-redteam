from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # --- Target ---
    target_provider: Literal["openai", "ollama", "custom"] = "openai"
    target_model: str = "gpt-4o"
    target_endpoint: str = ""          # blank = provider default
    openai_api_key: str = ""

    # --- Ollama ---
    ollama_host: str = "http://localhost:11434"

    # --- Analyst LLM (suggest_patch node) ---
    analyst_provider: Literal["openai", "ollama", "custom"] = "openai"
    analyst_model: str = "gpt-4o"
    analyst_api_key: str = ""

    # --- Paths ---
    db_path: Path = Path("data/redteam.db")
    reports_dir: Path = Path("reports/")

    # --- Garak ---
    garak_probe_categories: list[str] = Field(
        default=["dan", "gcg", "encoding", "promptinject"]
    )
    garak_timeout: int = 300           # seconds per run

    @model_validator(mode="after")
    def validate_keys(self) -> "Settings":
        if self.target_provider == "openai" and not self.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY is required when TARGET_PROVIDER=openai"
            )
        if self.target_provider == "ollama" and not self.ollama_host:
            raise ValueError(
                "OLLAMA_HOST is required when TARGET_PROVIDER=ollama"
            )
        if self.target_provider == "custom" and not self.target_endpoint:
            raise ValueError(
                "TARGET_ENDPOINT is required when TARGET_PROVIDER=custom"
            )
        return self

    @property
    def garak_target_uri(self) -> str:
        """Resolved endpoint URI passed to garak."""
        if self.target_provider == "openai":
            return "https://api.openai.com/v1"
        if self.target_provider == "ollama":
            return f"{self.ollama_host}/v1"
        return self.target_endpoint


# Module-level singleton -- import this everywhere
settings = Settings()
