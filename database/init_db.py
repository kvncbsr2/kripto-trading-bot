import asyncio

from sqlalchemy import text

from database.models import Base
from database.session import async_engine, sync_engine
from shared.logging import get_logger

logger = get_logger("database-init", service="database")


async def init_models_async():
    """Initializes all database tables asynchronously."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Attempt to create TimescaleDB hypertable if on PostgreSQL
        if "postgresql" in str(async_engine.url):
            try:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"))
                await conn.execute(
                    text("SELECT create_hypertable('candles', 'timestamp', if_not_exists => TRUE);")
                )
                await conn.execute(
                    text(
                        "SELECT create_hypertable('market_ticks', 'timestamp', if_not_exists => TRUE);"
                    )
                )
                logger.info("TimescaleDB hypertables created successfully.")
            except Exception as e:
                logger.warning(f"TimescaleDB hypertable setup skipped or not supported: {e}")
        # Safe column migration for SQLite positions table
        try:
            res = await conn.execute(text("PRAGMA table_info(positions);"))
            existing_cols = {row[1] for row in res.fetchall()}
            if existing_cols:
                if "price_stale" not in existing_cols:
                    await conn.execute(text("ALTER TABLE positions ADD COLUMN price_stale BOOLEAN DEFAULT 0;"))
                if "price_fetch_failures" not in existing_cols:
                    await conn.execute(text("ALTER TABLE positions ADD COLUMN price_fetch_failures INTEGER DEFAULT 0;"))
                if "last_price_update_at" not in existing_cols:
                    await conn.execute(text("ALTER TABLE positions ADD COLUMN last_price_update_at TIMESTAMP NULL;"))
        except Exception as e:
            logger.debug(f"positions column migration skipped: {e}")

    logger.info("Database tables verified and initialized successfully.")


def init_models_sync():
    """Initializes all database tables synchronously."""
    Base.metadata.create_all(bind=sync_engine)
    if "postgresql" in str(sync_engine.url):
        with sync_engine.connect() as conn:
            try:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"))
                conn.execute(
                    text("SELECT create_hypertable('candles', 'timestamp', if_not_exists => TRUE);")
                )
                conn.execute(
                    text(
                        "SELECT create_hypertable('market_ticks', 'timestamp', if_not_exists => TRUE);"
                    )
                )
                conn.commit()
            except Exception as e:
                logger.warning(f"TimescaleDB hypertable setup skipped: {e}")


if __name__ == "__main__":
    asyncio.run(init_models_async())
