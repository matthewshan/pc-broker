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

    # Ollama on the gaming PC. If OLLAMA_URL is unset, it is derived from
    # PC_HOST and OLLAMA_PORT.
    ollama_url: str = ""
    ollama_port: int = 11434
    ollama_health_timeout: float = 3.0

    # Idle auto-shutdown. Disabled unless explicitly enabled; every ambiguous
    # signal is treated as "user is active" (never shut down when unsure).
    idle_shutdown_enabled: bool = False
    idle_shutdown_minutes: int = 30
    idle_user_threshold_minutes: int = 20
    idle_post_wake_grace_minutes: int = 15
    idle_activity_poll_interval: float = 60.0
    idle_gpu_util_threshold: int = 15
    idle_consecutive_checks: int = 2

    @property
    def ollama_base_url(self) -> str:
        return self.ollama_url or f"http://{self.pc_host}:{self.ollama_port}"

    # Misc
    broker_version: str = "0.3.0"


settings = Settings()
