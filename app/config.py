from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Gaming PC
    pc_mac: str = ""
    pc_host: str = "192.168.1.100"
    pc_broadcast: str = "192.168.1.255"
    # TCP port probed to decide if the PC is up. Defaults to the shutdown
    # agent's port (8001), which listens whenever the PC is awake. (Windows
    # has no SSH/22 by default, so the old port-22 probe always read offline.)
    pc_reachability_port: int = 8001

    # Timeouts (seconds)
    host_reachability_timeout: int = 300
    poll_interval: float = 5.0

    # Auth
    api_token: str = ""

    # Optional shutdown agent on the PC
    shutdown_agent_url: str = ""
    shutdown_agent_token: str = ""

    # Misc
    broker_version: str = "0.1.0"


settings = Settings()
