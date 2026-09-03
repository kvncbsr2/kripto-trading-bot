from typing import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from apps.api.app.config import get_settings

settings = get_settings()

# Async engine for FastAPI & Async Services
async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Sync engine for scripts / Alembic
sync_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    echo=False,
    future=True,
)

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    autocommit=False,
    autoflush=False,
)


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def get_sync_db() -> Session:
    db = SyncSessionLocal()
    try:
        return db
    finally:
        db.close()
