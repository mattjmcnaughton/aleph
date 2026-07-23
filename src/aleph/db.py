"""Database engine and session configuration."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from aleph.config import settings


class Base(DeclarativeBase):
    """Base class for SQLAlchemy ORM models."""


engine = create_async_engine(settings.database_url)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def configure_database_url(database_url: str) -> None:
    """Retarget the module-level engine/sessionmaker to a database URL.

    The per-test database fixture uses this to point the application engine at a
    freshly cloned test database (see ``tests/integration/conftest.py``).
    """
    global async_session, engine

    await engine.dispose()
    engine = create_async_engine(database_url)
    async_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )


async def dispose_engine() -> None:
    """Dispose the current module-level engine."""
    await engine.dispose()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session (use as a FastAPI dependency)."""
    async with async_session() as session:
        yield session
