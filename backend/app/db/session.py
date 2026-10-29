"""Async engine and session lifecycle. Tables are never created here; migrations own the schema.

Identity for row level security. A request that carries a verified identity runs every transaction with app.subject set,
transaction-locally (set_config(..., true)): the value is discarded at COMMIT or ROLLBACK, so a pooled connection never
keeps it. The value lives in a ContextVar, which belongs to one asyncio task: each HTTP request is its own task, so
concurrent requests cannot see each other's identity, and nothing global holds it. Only the verified-identity dependency
in app.api.deps binds it (bind_identity). A session opened without an identity sets nothing, so row level security returns
no business rows: the failure is closed, not open.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, Pool

from app.core.config import DatabaseSettings

_bound_subject: ContextVar[str | None] = ContextVar("bound_subject", default=None)

SNAPSHOT_SQL = text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
BIND_SUBJECT_SQL = text("SELECT set_config('app.subject', :subject, true)")


class DatabaseUnavailableError(Exception):
    """The database cannot be reached or rejected the connection. The message is safe to log and return."""


def bind_identity(subject: str) -> Token[str | None]:
    """Binds a verified subject to the current context. Called only after token verification (app.api.deps).

    Returns the token that unbind_identity uses to restore the previous value. A context is not cleared by itself when
    a request ends: the caller must restore it, and the request dependency does (see app.api.deps).
    """
    if not subject or not subject.strip():
        raise ValueError("an identity needs a non-empty subject")
    return _bound_subject.set(subject)


def unbind_identity(token: Token[str | None]) -> None:
    """Restores the value that was bound before bind_identity. Never leaves a subject behind, even if the token belongs
    to another context: then the value is cleared instead."""
    try:
        _bound_subject.reset(token)
    except ValueError:
        _bound_subject.set(None)


def bound_identity() -> str | None:
    return _bound_subject.get()


@contextmanager
def identity_scope(subject: str) -> Iterator[None]:
    """Binds a subject for the duration of a block, then restores the previous binding. For tests and tools only."""
    if not subject or not subject.strip():
        raise ValueError("an identity needs a non-empty subject")
    token = _bound_subject.set(subject)
    try:
        yield
    finally:
        _bound_subject.reset(token)


class Database:
    def __init__(self, settings: DatabaseSettings, *, poolclass: type[Pool] | None = None) -> None:
        pool_options = {} if poolclass is NullPool else {"pool_size": settings.pool_size, "max_overflow": settings.max_overflow}
        connect_args: dict[str, object] = {"timeout": settings.connect_timeout_seconds}
        if settings.session_role is not None:
            # Test fixtures only (DatabaseSettings.session_role): the owner-equivalent role for the business-logic suite.
            connect_args["server_settings"] = {"role": settings.session_role}
        self._engine: AsyncEngine = create_async_engine(
            settings.url,
            pool_pre_ping=True,
            poolclass=poolclass,
            connect_args=connect_args,
            **pool_options,
        )
        self._sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False, autoflush=False)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def session(self, *, snapshot: bool = False) -> AsyncIterator[AsyncSession]:
        """Commit on success, roll back on any error, always close. Raw driver errors are not propagated.

        snapshot=True makes the transaction REPEATABLE READ and READ ONLY. That mode must be the first statement of the
        transaction, so it is executed before the identity is bound. Both happen inside this one transaction.
        """
        async with self._sessionmaker() as session:
            try:
                if snapshot:
                    await session.execute(SNAPSHOT_SQL)
                subject = _bound_subject.get()
                if subject is not None:
                    await session.execute(BIND_SUBJECT_SQL, {"subject": subject})
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
