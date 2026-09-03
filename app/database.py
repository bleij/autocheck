import os
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


# Создание директории для БД при использовании SQLite
if "sqlite" in settings.database_url:
    # Извлекаем путь к файлу БД (например sqlite+aiosqlite:///./db/autocheck.db)
    db_raw_path = settings.database_url.split("sqlite:///")[-1].split("sqlite+aiosqlite:///")[-1]
    db_path = Path(db_raw_path)
    if db_path.parent and not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)

# Создание асинхронного движка SQLAlchemy
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    future=True,
)

# Фабрика асинхронных сессий
async_session_maker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Базовый класс для всех моделей SQLAlchemy."""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency для FastAPI эндпоинтов, предоставляющий асинхронную сессию БД.
    Сессия автоматически закрывается после завершения запроса.
    """
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    """
    Инициализация таблиц базы данных при старте приложения.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
