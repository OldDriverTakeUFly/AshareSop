import os

from stockhot.core.config import DB_PATH


class Settings:
    DB_PATH: str = str(DB_PATH)
    API_PASSWORD: str = os.environ.get("STOCKHOT_API_PASSWORD", "stockhot")
    CORS_ORIGINS: list[str] = os.environ.get(
        "CORS_ORIGINS", "http://localhost:3000"
    ).split(",")


settings = Settings()
