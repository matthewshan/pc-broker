from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Gaming PC
    pc_mac: str = ""
    pc_host: str = "192.168.1.100"
    pc_broadcast: str = "192.168.1.255"

    # Ollama
    ollama_base_url: str = "http://192.168.1.100:11434"

    # Timeouts (seconds)
    host_reachability_timeout: int = 300
    ollama_readiness_timeout: int = 600
    poll_interval: float = 5.0

    # Auth
    api_token: str = ""

    # Optional shutdown agent on the PC
    shutdown_agent_url: str = ""
    shutdown_agent_token: str = ""

    # Misc
    broker_version: str = "0.1.0"


settings = Settings()
