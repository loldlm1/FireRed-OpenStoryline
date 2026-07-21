from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


LEGACY_SCHEMA_REVISION = "20260717_0001"
WORKSPACE_SCHEMA_REVISION = "20260719_0002"
CHECKPOINT_SCHEMA_REVISION = "20260721_0003"
COMPATIBLE_SCHEMA_REVISIONS = frozenset(
    {LEGACY_SCHEMA_REVISION, WORKSPACE_SCHEMA_REVISION, CHECKPOINT_SCHEMA_REVISION}
)


class DatabaseConfigurationError(RuntimeError):
    pass


def normalize_database_url(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if not value:
        raise DatabaseConfigurationError("DATABASE_URL is required")
    if value.startswith("postgres://"):
        value = "postgresql+psycopg://" + value.removeprefix("postgres://")
    elif value.startswith("postgresql://"):
        value = "postgresql+psycopg://" + value.removeprefix("postgresql://")
    try:
        url = make_url(value)
    except Exception:
        raise DatabaseConfigurationError("DATABASE_URL is invalid") from None
    if url.drivername != "postgresql+psycopg" or not url.database or not url.username:
        raise DatabaseConfigurationError("DATABASE_URL must use PostgreSQL with Psycopg")
    return url.render_as_string(hide_password=False)


@dataclass(frozen=True)
class DatabaseReadiness:
    ready: bool
    code: str


class Database:
    def __init__(self, url: str) -> None:
        normalized = normalize_database_url(url)
        timeout = _bounded_int("OPENSTORYLINE_DATABASE_CONNECT_TIMEOUT", 10, 1, 60)
        self.query_timeout = _bounded_int(
            "OPENSTORYLINE_DATABASE_QUERY_TIMEOUT", 5, 1, 30
        )
        self.engine: AsyncEngine = create_async_engine(
            normalized,
            pool_pre_ping=True,
            pool_size=_bounded_int("OPENSTORYLINE_DATABASE_POOL_SIZE", 5, 1, 20),
            max_overflow=_bounded_int("OPENSTORYLINE_DATABASE_MAX_OVERFLOW", 5, 0, 20),
            connect_args={"connect_timeout": timeout},
        )
        self.sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
        )

    @classmethod
    def from_env(cls) -> "Database":
        return cls(os.getenv("DATABASE_URL", ""))

    async def readiness(self) -> DatabaseReadiness:
        try:
            async with asyncio.timeout(self.query_timeout):
                async with self.engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                    revision = await connection.scalar(
                        text("SELECT version_num FROM alembic_version LIMIT 1")
                    )
        except (TimeoutError, SQLAlchemyError):
            return DatabaseReadiness(False, "DATABASE_UNAVAILABLE")
        if revision not in COMPATIBLE_SCHEMA_REVISIONS:
            return DatabaseReadiness(False, "DATABASE_SCHEMA_OUTDATED")
        return DatabaseReadiness(True, "DATABASE_READY")

    async def dispose(self) -> None:
        await self.engine.dispose()


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        raise DatabaseConfigurationError(f"{name} must be an integer") from None
    if not minimum <= value <= maximum:
        raise DatabaseConfigurationError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value
