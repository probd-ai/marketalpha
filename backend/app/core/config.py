from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        extra="ignore",
    )

    data_dir: Path = Path("data")
    tv_username: str | None = None
    tv_password: str | None = None
    mongo_uri: str = "mongodb://localhost:27017/"
    mongo_db: str = "market_hunt"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def seed_dir(self) -> Path:
        return self.data_dir / "seed"


settings = Settings()
