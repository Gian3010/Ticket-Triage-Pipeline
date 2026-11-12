"""Configuração via variáveis de ambiente (12-factor)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    """Lê a variável tratando string vazia como ausente (ex.: secret não definido no GitHub Actions)."""
    return os.getenv(name) or default


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "sqlite:///data/triage.db"))
    inbox_dir: Path = field(default_factory=lambda: Path(_env("INBOX_DIR", "data/inbox")))
    processed_dir: Path = field(default_factory=lambda: Path(_env("PROCESSED_DIR", "data/processed")))
    mode: str = field(default_factory=lambda: _env("TRIAGE_MODE", "auto"))  # auto | llm | rules
    anthropic_model: str = field(default_factory=lambda: _env("TRIAGE_MODEL", ""))
    workers: int = field(default_factory=lambda: int(_env("TRIAGE_WORKERS", "8")))
    review_threshold: float = field(default_factory=lambda: _env_float("REVIEW_THRESHOLD", 0.6))
    # Premissas do cenário fictício (usadas só no cálculo de ROI simulado)
    tickets_per_week: int = field(default_factory=lambda: int(_env("TICKETS_PER_WEEK", "1300")))
    manual_seconds: float = field(default_factory=lambda: _env_float("MANUAL_SECONDS_PER_TICKET", 45))
    review_seconds: float = field(default_factory=lambda: _env_float("REVIEW_SECONDS_PER_TICKET", 20))

    def use_llm(self) -> bool:
        if self.mode == "rules":
            return False
        has_creds = bool(os.getenv("ANTHROPIC_API_KEY")) and bool(self.anthropic_model)
        if self.mode == "llm" and not has_creds:
            raise RuntimeError("TRIAGE_MODE=llm exige ANTHROPIC_API_KEY e TRIAGE_MODEL.")
        return has_creds
