import os

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class Settings(BaseSettings):
        """
        Конфигурация микросервиса через Pydantic Settings.
        Считывается из переменных окружения или .env файла.
        """
        app_name: str = "Autocheck 1C Integration"
        database_url: str = "sqlite+aiosqlite:///./db/autocheck.db"
        dump_file_path: str = "./data/1c_dump.json"
        parse_interval_minutes: int = 5
        debug: bool = False

        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore"
        )
except ImportError:
    class Settings:  # type: ignore
        """Резервная конфигурация на базе os.getenv."""
        def __init__(self):
            self.app_name: str = os.getenv("APP_NAME", "Autocheck 1C Integration")
            self.database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./db/autocheck.db")
            self.dump_file_path: str = os.getenv("DUMP_FILE_PATH", "./data/1c_dump.json")
            self.parse_interval_minutes: int = int(os.getenv("PARSE_INTERVAL_MINUTES", "5"))
            self.debug: bool = os.getenv("DEBUG", "False").lower() in ("true", "1", "t")


# Экземпляр синглтона настроек
settings = Settings()


from datetime import datetime
try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Asia/Almaty")
except Exception:
    LOCAL_TZ = None


def get_local_now() -> datetime:
    """
    Возвращает текущее локальное время в часовом поясе Asia/Almaty (UTC+5).
    Возвращает naive datetime для корректной работы с SQLite и отображения в шаблонах без смещения.
    """
    if LOCAL_TZ:
        return datetime.now(LOCAL_TZ).replace(tzinfo=None)
    return datetime.now()

