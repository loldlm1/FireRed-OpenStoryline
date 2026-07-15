from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import sqlite3
import time


@dataclass(frozen=True)
class RateRule:
    minute: int
    day: int

    def __post_init__(self) -> None:
        if self.minute < 1 or self.day < 1:
            raise ValueError("rate limits must be positive")


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    limit_minute: int
    remaining_minute: int
    limit_day: int
    remaining_day: int
    retry_after: int

    def headers(self) -> dict[str, str]:
        values = {
            "X-RateLimit-Limit-Minute": str(self.limit_minute),
            "X-RateLimit-Remaining-Minute": str(self.remaining_minute),
            "X-RateLimit-Limit-Day": str(self.limit_day),
            "X-RateLimit-Remaining-Day": str(self.remaining_day),
        }
        if not self.allowed:
            values["Retry-After"] = str(self.retry_after)
        return values


@dataclass(frozen=True)
class RatePolicy:
    unauthorized_client: RateRule
    unauthorized_global: RateRule
    api: RateRule
    jobs: RateRule

    @classmethod
    def from_env(cls) -> "RatePolicy":
        return cls(
            unauthorized_client=RateRule(
                _env_int("OPENSTORYLINE_AUTH_RPM", 20),
                _env_int("OPENSTORYLINE_AUTH_RPD", 200),
            ),
            unauthorized_global=RateRule(
                _env_int("OPENSTORYLINE_AUTH_GLOBAL_RPM", 600),
                _env_int("OPENSTORYLINE_AUTH_GLOBAL_RPD", 50_000),
            ),
            api=RateRule(
                _env_int("OPENSTORYLINE_API_RPM", 120),
                _env_int("OPENSTORYLINE_API_RPD", 10_000),
            ),
            jobs=RateRule(
                _env_int("OPENSTORYLINE_JOBS_RPM", 4),
                _env_int("OPENSTORYLINE_JOBS_RPD", 50),
            ),
        )


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not 1 <= value <= 10_000_000:
        raise ValueError(f"{name} must be between 1 and 10000000")
    return value


class PersistentRateLimiter:
    """SQLite-backed fixed UTC minute/day windows for one or more app processes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS rate_counters (
                    scope TEXT NOT NULL,
                    window TEXT NOT NULL,
                    bucket INTEGER NOT NULL,
                    hits INTEGER NOT NULL,
                    PRIMARY KEY (scope, window, bucket)
                )
                """
            )

    def check(
        self,
        scope: str,
        rule: RateRule,
        *,
        cost: int = 1,
        now: float | None = None,
    ) -> RateDecision:
        if not scope or len(scope) > 200:
            raise ValueError("invalid rate-limit scope")
        if cost < 1:
            raise ValueError("rate-limit cost must be positive")

        current = float(time.time() if now is None else now)
        epoch = max(0, int(current))
        minute_bucket = epoch // 60
        day_bucket = epoch // 86_400
        minute_reset = ((minute_bucket + 1) * 60) - epoch
        day_reset = ((day_bucket + 1) * 86_400) - epoch

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                minute_hits = self._hits(connection, scope, "minute", minute_bucket)
                day_hits = self._hits(connection, scope, "day", day_bucket)
                minute_allowed = minute_hits + cost <= rule.minute
                day_allowed = day_hits + cost <= rule.day
                allowed = minute_allowed and day_allowed
                if allowed:
                    self._increment(connection, scope, "minute", minute_bucket, cost)
                    self._increment(connection, scope, "day", day_bucket, cost)
                    minute_hits += cost
                    day_hits += cost
                self._cleanup(connection, minute_bucket, day_bucket)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        retry_after = 0
        if not day_allowed:
            retry_after = max(1, day_reset)
        elif not minute_allowed:
            retry_after = max(1, minute_reset)
        return RateDecision(
            allowed=allowed,
            limit_minute=rule.minute,
            remaining_minute=max(0, rule.minute - minute_hits),
            limit_day=rule.day,
            remaining_day=max(0, rule.day - day_hits),
            retry_after=retry_after,
        )

    @staticmethod
    def _hits(
        connection: sqlite3.Connection,
        scope: str,
        window: str,
        bucket: int,
    ) -> int:
        row = connection.execute(
            "SELECT hits FROM rate_counters WHERE scope = ? AND window = ? AND bucket = ?",
            (scope, window, bucket),
        ).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _increment(
        connection: sqlite3.Connection,
        scope: str,
        window: str,
        bucket: int,
        cost: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO rate_counters (scope, window, bucket, hits)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(scope, window, bucket)
            DO UPDATE SET hits = hits + excluded.hits
            """,
            (scope, window, bucket, cost),
        )

    @staticmethod
    def _cleanup(connection: sqlite3.Connection, minute_bucket: int, day_bucket: int) -> None:
        connection.execute(
            """
            DELETE FROM rate_counters
            WHERE (window = 'minute' AND bucket < ?)
               OR (window = 'day' AND bucket < ?)
            """,
            (minute_bucket - 2, day_bucket - 2),
        )
