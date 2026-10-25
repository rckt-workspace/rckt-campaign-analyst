"""Async engine and session lifecycle. Tables are never created here; migrations own the schema."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, Pool

from app.core.config import DatabaseSettings


class DatabaseUnavailableError(Exception):
    """The database cannot be reached or rejected the connection. The message is safe to log and return."""


class Database:
    def __init__(self, settings: DatabaseSettings, *, poolclass: type[Pool] | None = None) -> None:
        pool_options = {} if poolclass is NullPool else {"pool_size": settings.pool_size, "max_overflow": settings.max_overflow}
        self._engine: AsyncEngine = create_async_engine(
            settings.url,
            pool_pre_ping=True,
            poolclass=poolclass,
            connect_args={"timeout": settings.connect_timeout_seconds},
            **pool_options,
        )
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False, autoflush=False)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Commit on success, roll back on any error, always close. Raw driver errors are not propagated."""
        async with self._sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except (OperationalError, OSError):
                # Driver messages can contain host and port details; they are never re-raised.
                await session.rollback()
                raise DatabaseUnavailableError("database unavailable") from None
            except Exception:
                await session.rollback()
                raise

    async def check_connection(self) -> None:
        """Run SELECT 1 on a fresh connection. Used by /readyz."""
        try:
            async with self._engine.connect() as connection:
                await connection.exec_driver_sql("SELECT 1")
        except (OperationalError, DBAPIError, OSError) as exc:
            raise DatabaseUnavailableError("database unavailable") from exc

    async def dispose(self) -> None:
        await self._engine.dispose()
