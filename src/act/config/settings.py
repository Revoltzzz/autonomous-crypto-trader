"""Environment configuration. Live execution is deliberately impossible."""
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_ignore_empty=True, extra="ignore", frozen=True
    )
    paper_only: bool = True
    supabase_url: str = ""
    supabase_service_key: SecretStr = SecretStr("")
    ledger_path: Path = Path("data/paper-ledger.json")
    report_dir: Path = Path("reports")
    http_timeout: float = Field(default=15, gt=0)
    http_retries: int = Field(default=3, ge=0, le=10)
    fee_pct: float = Field(default=0.25, ge=0, lt=100)
    slippage_pct: float = Field(default=0.5, ge=0, lt=100)

    @field_validator("paper_only")
    @classmethod
    def enforce_paper(cls, value: bool) -> bool:
        if not value:
            raise ValueError("PAPER TRADING ONLY: paper_only cannot be false")
        return value
