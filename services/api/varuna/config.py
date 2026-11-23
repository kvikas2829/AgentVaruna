"""Runtime configuration, read from environment variables."""
import os
from pathlib import Path


def _data_dir() -> Path:
    p = Path(os.getenv("VARUNA_DATA_DIR", "./data")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


class Settings:
    @property
    def db_url(self) -> str:
        return os.getenv("VARUNA_DB_URL", f"sqlite:///{_data_dir() / 'varuna.db'}")

    @property
    def anthropic_key(self) -> str | None:
        return os.getenv("ANTHROPIC_API_KEY") or None

    @property
    def judge_mode(self) -> str:  # auto | claude | heuristic
        return os.getenv("VARUNA_JUDGE", "auto").lower()

    @property
    def judge_model(self) -> str:
        return os.getenv("VARUNA_JUDGE_MODEL", "claude-sonnet-5-5")

    @property
    def judge_timeout(self) -> float:
        return float(os.getenv("VARUNA_JUDGE_TIMEOUT", "1.5"))

    @property
    def admin_user(self) -> str:
        return os.getenv("VARUNA_ADMIN_USER", "admin")

    @property
    def admin_password(self) -> str:
        return os.getenv("VARUNA_ADMIN_PASSWORD", "varuna")

    @property
    def secret(self) -> str:
        return os.getenv("VARUNA_SECRET", "dev-secret-change-me")

    @property
    def seed_on_empty(self) -> bool:
        return os.getenv("VARUNA_SEED", "1") not in ("0", "false", "no")

    @property
    def web_dist(self) -> Path:
        return Path(os.getenv("VARUNA_WEB_DIST", Path(__file__).resolve().parents[3] / "web" / "dist"))

    @property
    def public_url(self) -> str:
        return os.getenv("VARUNA_PUBLIC_URL", "http://localhost:8000")

    @property
    def smtp(self) -> dict | None:
        host = os.getenv("VARUNA_SMTP_HOST")
        if not host:
            return None
        return {
            "host": host,
            "port": int(os.getenv("VARUNA_SMTP_PORT", "587")),
            "user": os.getenv("VARUNA_SMTP_USER"),
            "password": os.getenv("VARUNA_SMTP_PASSWORD"),
            "sender": os.getenv("VARUNA_SMTP_FROM", "varuna@localhost"),
        }


settings = Settings()
