from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from getpass import getpass
import argparse
import asyncio
import hashlib
import hmac
import ipaddress
import os
import secrets
from typing import Callable
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from open_storyline.mvp.database import Database
from open_storyline.mvp.models import AuthSession, LoginAttemptBucket


SESSION_COOKIE = "openstoryline_session"
CSRF_COOKIE = "openstoryline_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
Clock = Callable[[], datetime]


class AuthConfigurationError(RuntimeError):
    pass


class AuthUnavailable(RuntimeError):
    pass


class AuthRejected(RuntimeError):
    def __init__(self, code: str, status_code: int, retry_after: int = 0) -> None:
        self.code = code
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(code)


@dataclass(frozen=True)
class LoginRule:
    minute: int
    day: int


@dataclass(frozen=True)
class LoginPolicy:
    client: LoginRule
    global_scope: LoginRule


@dataclass(frozen=True)
class AuthSettings:
    password_hash: str
    pepper: str
    public_origin: str
    allow_insecure_http: bool
    trust_proxy_headers: bool
    idle_ttl: timedelta
    absolute_ttl: timedelta
    last_seen_interval: timedelta
    login_policy: LoginPolicy

    @property
    def secure_cookies(self) -> bool:
        return self.public_origin.startswith("https://")

    @classmethod
    def from_env(cls) -> "AuthSettings":
        password_hash = _required("OPENSTORYLINE_WEB_PASSWORD_HASH")
        pepper = _required("OPENSTORYLINE_SECURITY_PEPPER")
        if len(pepper) < 32:
            raise AuthConfigurationError(
                "OPENSTORYLINE_SECURITY_PEPPER must contain at least 32 characters"
            )
        allow_insecure = _enabled("OPENSTORYLINE_ALLOW_INSECURE_HTTP")
        public_origin = _normalize_origin(_required("OPENSTORYLINE_PUBLIC_ORIGIN"))
        if not public_origin.startswith("https://") and not allow_insecure:
            raise AuthConfigurationError(
                "OPENSTORYLINE_PUBLIC_ORIGIN must use HTTPS unless insecure development is explicit"
            )
        return cls(
            password_hash=password_hash,
            pepper=pepper,
            public_origin=public_origin,
            allow_insecure_http=allow_insecure,
            trust_proxy_headers=_enabled("OPENSTORYLINE_TRUST_PROXY_HEADERS"),
            idle_ttl=timedelta(
                hours=_bounded_int("OPENSTORYLINE_SESSION_IDLE_HOURS", 12, 1, 72)
            ),
            absolute_ttl=timedelta(
                days=_bounded_int("OPENSTORYLINE_SESSION_ABSOLUTE_DAYS", 7, 1, 30)
            ),
            last_seen_interval=timedelta(
                seconds=_bounded_int(
                    "OPENSTORYLINE_SESSION_TOUCH_SECONDS", 300, 30, 3600
                )
            ),
            login_policy=LoginPolicy(
                client=LoginRule(
                    minute=_bounded_int("OPENSTORYLINE_LOGIN_RPM", 10, 1, 10_000),
                    day=_bounded_int("OPENSTORYLINE_LOGIN_RPD", 100, 1, 1_000_000),
                ),
                global_scope=LoginRule(
                    minute=_bounded_int(
                        "OPENSTORYLINE_LOGIN_GLOBAL_RPM", 120, 1, 1_000_000
                    ),
                    day=_bounded_int(
                        "OPENSTORYLINE_LOGIN_GLOBAL_RPD", 5_000, 1, 10_000_000
                    ),
                ),
            ),
        )


@dataclass(frozen=True)
class SessionContext:
    token_digest: str
    csrf_digest: str
    idle_expires_at: datetime
    absolute_expires_at: datetime


@dataclass(frozen=True)
class NewSession:
    token: str
    csrf_token: str
    context: SessionContext


@dataclass(frozen=True)
class LoginLimitDecision:
    allowed: bool
    retry_after: int = 0


class LoginAttemptLimiter:
    def __init__(
        self,
        database: Database,
        policy: LoginPolicy,
        clock: Clock,
    ) -> None:
        self.database = database
        self.policy = policy
        self.clock = clock

    async def check(self, client_digest: str) -> LoginLimitDecision:
        now = self.clock()
        windows = self._windows(client_digest, now)
        try:
            async with self.database.sessions() as session:
                rows = (
                    await session.execute(
                        select(LoginAttemptBucket).where(self._predicate(windows))
                    )
                ).scalars()
                hits = {
                    (row.scope_digest, row.window_kind, row.bucket): row.hits
                    for row in rows
                }
        except SQLAlchemyError:
            raise AuthUnavailable("authentication storage is unavailable") from None
        return self._decision(windows, hits, cost=0)

    async def record_failure(self, client_digest: str) -> LoginLimitDecision:
        now = self.clock()
        windows = self._windows(client_digest, now)
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    for scope, window_kind, bucket, _limit, _reset in windows:
                        await session.execute(
                            pg_insert(LoginAttemptBucket)
                            .values(
                                scope_digest=scope,
                                window_kind=window_kind,
                                bucket=bucket,
                                hits=0,
                                updated_at=now,
                            )
                            .on_conflict_do_nothing(
                                index_elements=["scope_digest", "window_kind", "bucket"]
                            )
                        )
                    rows = list(
                        (
                            await session.execute(
                                select(LoginAttemptBucket)
                                .where(self._predicate(windows))
                                .order_by(
                                    LoginAttemptBucket.scope_digest,
                                    LoginAttemptBucket.window_kind,
                                    LoginAttemptBucket.bucket,
                                )
                                .with_for_update()
                            )
                        ).scalars()
                    )
                    hits = {
                        (row.scope_digest, row.window_kind, row.bucket): row.hits
                        for row in rows
                    }
                    decision = self._decision(windows, hits, cost=1)
                    if decision.allowed:
                        for row in rows:
                            row.hits += 1
                            row.updated_at = now
                    await session.execute(
                        text(
                            "DELETE FROM login_attempt_buckets WHERE ctid IN "
                            "(SELECT ctid FROM login_attempt_buckets "
                            "WHERE updated_at < :cutoff ORDER BY updated_at LIMIT 200)"
                        ),
                        {"cutoff": now - timedelta(days=3)},
                    )
        except SQLAlchemyError:
            raise AuthUnavailable("authentication storage is unavailable") from None
        return decision

    def _windows(
        self, client_digest: str, now: datetime
    ) -> list[tuple[str, str, int, int, int]]:
        epoch = max(0, int(now.timestamp()))
        minute_bucket = epoch // 60
        day_bucket = epoch // 86_400
        minute_reset = max(1, ((minute_bucket + 1) * 60) - epoch)
        day_reset = max(1, ((day_bucket + 1) * 86_400) - epoch)
        return [
            (
                client_digest,
                "minute",
                minute_bucket,
                self.policy.client.minute,
                minute_reset,
            ),
            (client_digest, "day", day_bucket, self.policy.client.day, day_reset),
            (
                "global",
                "minute",
                minute_bucket,
                self.policy.global_scope.minute,
                minute_reset,
            ),
            (
                "global",
                "day",
                day_bucket,
                self.policy.global_scope.day,
                day_reset,
            ),
        ]

    @staticmethod
    def _predicate(windows):
        return or_(
            *[
                and_(
                    LoginAttemptBucket.scope_digest == scope,
                    LoginAttemptBucket.window_kind == window_kind,
                    LoginAttemptBucket.bucket == bucket,
                )
                for scope, window_kind, bucket, _limit, _reset in windows
            ]
        )

    @staticmethod
    def _decision(windows, hits, *, cost: int) -> LoginLimitDecision:
        blocked_resets = []
        for scope, window_kind, bucket, limit, reset in windows:
            current = int(hits.get((scope, window_kind, bucket), 0))
            if current + cost > limit or (cost == 0 and current >= limit):
                blocked_resets.append(reset)
        if blocked_resets:
            return LoginLimitDecision(False, max(blocked_resets))
        return LoginLimitDecision(True)


class AuthService:
    def __init__(
        self,
        database: Database,
        settings: AuthSettings,
        *,
        clock: Clock | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.clock = clock or (lambda: datetime.now(UTC))
        self.hasher = PasswordHasher(
            time_cost=3,
            memory_cost=65_536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
        )
        if not settings.password_hash.startswith("$argon2id$"):
            raise AuthConfigurationError(
                "OPENSTORYLINE_WEB_PASSWORD_HASH must be a valid Argon2id hash"
            )
        try:
            self.hasher.check_needs_rehash(settings.password_hash)
        except InvalidHashError:
            raise AuthConfigurationError(
                "OPENSTORYLINE_WEB_PASSWORD_HASH must be a valid Argon2id hash"
            ) from None
        self.login_limiter = LoginAttemptLimiter(
            database, settings.login_policy, self.clock
        )
        self.digest_key = hmac.new(
            settings.pepper.encode("utf-8"),
            f"openstoryline-auth:{settings.password_hash}".encode("utf-8"),
            hashlib.sha256,
        ).digest()

    async def login(self, password: str, request: Request) -> NewSession:
        client_digest = self.client_digest(request)
        decision = await self.login_limiter.check(client_digest)
        if not decision.allowed:
            raise AuthRejected("LOGIN_RATE_LIMITED", 429, decision.retry_after)
        if not await asyncio.to_thread(self._verify_password, password):
            decision = await self.login_limiter.record_failure(client_digest)
            if not decision.allowed:
                raise AuthRejected("LOGIN_RATE_LIMITED", 429, decision.retry_after)
            raise AuthRejected("INVALID_CREDENTIALS", 401)
        existing = await self.resolve_session(request.cookies.get(SESSION_COOKIE))
        if existing is not None:
            await self.revoke(existing.token_digest)
        return await self._create_session(client_digest, request)

    def _verify_password(self, password: str) -> bool:
        try:
            return bool(self.hasher.verify(self.settings.password_hash, password))
        except VerifyMismatchError:
            return False
        except (InvalidHashError, VerificationError):
            raise AuthUnavailable("password verification is unavailable") from None

    async def _create_session(self, client_digest: str, request: Request) -> NewSession:
        now = self.clock()
        absolute_expires_at = now + self.settings.absolute_ttl
        idle_expires_at = min(now + self.settings.idle_ttl, absolute_expires_at)
        for _attempt in range(2):
            token = secrets.token_urlsafe(32)
            csrf_token = secrets.token_urlsafe(32)
            row = AuthSession(
                token_digest=self.digest(token),
                csrf_digest=self.digest(csrf_token),
                client_digest=client_digest,
                user_agent_digest=self.digest(request.headers.get("user-agent", "unknown")),
                last_seen_at=now,
                idle_expires_at=idle_expires_at,
                absolute_expires_at=absolute_expires_at,
            )
            try:
                async with self.database.sessions() as session:
                    async with session.begin():
                        await session.execute(
                            text(
                                "DELETE FROM auth_sessions WHERE ctid IN "
                                "(SELECT ctid FROM auth_sessions "
                                "WHERE absolute_expires_at < :cutoff "
                                "OR revoked_at < :cutoff "
                                "ORDER BY absolute_expires_at LIMIT 200)"
                            ),
                            {"cutoff": now - timedelta(days=1)},
                        )
                        session.add(row)
            except IntegrityError:
                continue
            except SQLAlchemyError:
                raise AuthUnavailable("authentication storage is unavailable") from None
            return NewSession(token, csrf_token, self._context(row))
        raise AuthUnavailable("authentication session could not be created")

    async def resolve_session(self, raw_token: str | None) -> SessionContext | None:
        if not raw_token or len(raw_token) > 256:
            return None
        token_digest = self.digest(raw_token)
        now = self.clock()
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    row = await session.get(AuthSession, token_digest)
                    if row is None or row.revoked_at is not None:
                        return None
                    if now >= row.idle_expires_at or now >= row.absolute_expires_at:
                        row.revoked_at = now
                        return None
                    if now - row.last_seen_at >= self.settings.last_seen_interval:
                        row.last_seen_at = now
                        row.idle_expires_at = min(
                            now + self.settings.idle_ttl, row.absolute_expires_at
                        )
                    return self._context(row)
        except SQLAlchemyError:
            raise AuthUnavailable("authentication storage is unavailable") from None

    async def rotate_csrf(self, token_digest: str) -> str:
        csrf_token = secrets.token_urlsafe(32)
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    row = await session.get(AuthSession, token_digest)
                    if row is None or row.revoked_at is not None:
                        raise AuthRejected("UNAUTHENTICATED", 401)
                    row.csrf_digest = self.digest(csrf_token)
        except AuthRejected:
            raise
        except SQLAlchemyError:
            raise AuthUnavailable("authentication storage is unavailable") from None
        return csrf_token

    async def revoke(self, token_digest: str) -> None:
        try:
            async with self.database.sessions() as session:
                async with session.begin():
                    row = await session.get(AuthSession, token_digest)
                    if row is not None and row.revoked_at is None:
                        row.revoked_at = self.clock()
        except SQLAlchemyError:
            raise AuthUnavailable("authentication storage is unavailable") from None

    def valid_csrf(self, context: SessionContext, raw_token: str | None) -> bool:
        if not raw_token or len(raw_token) > 256:
            return False
        return hmac.compare_digest(context.csrf_digest, self.digest(raw_token))

    def same_origin(self, request: Request) -> bool:
        origin = request.headers.get("origin", "").strip().rstrip("/")
        fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
        if fetch_site and fetch_site not in {"same-origin", "none"}:
            return False
        return bool(origin) and hmac.compare_digest(origin, self.settings.public_origin)

    def client_digest(self, request: Request) -> str:
        address = request.client.host if request.client else "unknown"
        if self.settings.trust_proxy_headers:
            forwarded = request.headers.get("x-forwarded-for", "").rsplit(",", 1)[-1].strip()
            if forwarded:
                try:
                    address = str(ipaddress.ip_address(forwarded))
                except ValueError:
                    pass
        return self.digest(f"client:{address}")

    def digest(self, value: str) -> str:
        return hmac.new(
            self.digest_key,
            value.encode("utf-8", errors="replace"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _context(row: AuthSession) -> SessionContext:
        return SessionContext(
            token_digest=row.token_digest,
            csrf_digest=row.csrf_digest,
            idle_expires_at=row.idle_expires_at,
            absolute_expires_at=row.absolute_expires_at,
        )


class LoginPayload(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


def create_auth_router(get_service: Callable[[], AuthService | None]) -> APIRouter:
    router = APIRouter(prefix="/api/mvp/auth", tags=["mvp-auth"])

    @router.post("/login")
    async def login(payload: LoginPayload, request: Request):
        service = get_service()
        if service is None:
            return _error("AUTH_UNAVAILABLE", 503)
        if not service.same_origin(request):
            return _error("REQUEST_ORIGIN_INVALID", 403)
        try:
            created = await service.login(payload.password, request)
        except AuthRejected as exc:
            headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
            return _error(exc.code, exc.status_code, headers=headers)
        except AuthUnavailable:
            return _error("AUTH_UNAVAILABLE", 503)
        response = JSONResponse({"authenticated": True})
        _set_auth_cookies(response, service.settings, created.token, created.csrf_token)
        return response

    @router.get("/session")
    async def session_status(request: Request):
        service = get_service()
        if service is None:
            return _error("AUTH_UNAVAILABLE", 503)
        try:
            context = await service.resolve_session(request.cookies.get(SESSION_COOKIE))
        except AuthUnavailable:
            return _error("AUTH_UNAVAILABLE", 503)
        if context is None:
            response = JSONResponse({"authenticated": False})
            _clear_auth_cookies(response, service.settings)
            return response
        response = JSONResponse(
            {
                "authenticated": True,
                "idle_expires_at": context.idle_expires_at.isoformat(),
                "absolute_expires_at": context.absolute_expires_at.isoformat(),
            }
        )
        csrf_token = request.cookies.get(CSRF_COOKIE)
        if not service.valid_csrf(context, csrf_token):
            try:
                csrf_token = await service.rotate_csrf(context.token_digest)
            except (AuthRejected, AuthUnavailable):
                return _error("AUTH_UNAVAILABLE", 503)
            _set_csrf_cookie(response, service.settings, csrf_token)
        return response

    @router.post("/logout")
    async def logout(request: Request):
        service = get_service()
        context = getattr(request.state, "auth_session", None)
        if service is None or context is None:
            return _error("UNAUTHENTICATED", 401)
        try:
            await service.revoke(context.token_digest)
        except AuthUnavailable:
            return _error("AUTH_UNAVAILABLE", 503)
        response = JSONResponse({"authenticated": False})
        _clear_auth_cookies(response, service.settings)
        return response

    return router


def _set_auth_cookies(
    response: JSONResponse,
    settings: AuthSettings,
    session_token: str,
    csrf_token: str,
) -> None:
    max_age = int(settings.absolute_ttl.total_seconds())
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    _set_csrf_cookie(response, settings, csrf_token)


def _set_csrf_cookie(
    response: JSONResponse, settings: AuthSettings, csrf_token: str
) -> None:
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=int(settings.absolute_ttl.total_seconds()),
        httponly=False,
        secure=settings.secure_cookies,
        samesite="strict",
        path="/",
    )


def _clear_auth_cookies(response: JSONResponse, settings: AuthSettings) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        CSRF_COOKIE,
        path="/",
        secure=settings.secure_cookies,
        httponly=False,
        samesite="strict",
    )


def _error(code: str, status_code: int, *, headers: dict[str, str] | None = None):
    message = {
        "INVALID_CREDENTIALS": "invalid password",
        "LOGIN_RATE_LIMITED": "too many failed login attempts",
        "REQUEST_ORIGIN_INVALID": "request origin is not allowed",
        "UNAUTHENTICATED": "login required",
        "AUTH_UNAVAILABLE": "authentication is temporarily unavailable",
    }.get(code, "authentication request failed")
    return JSONResponse(
        {"detail": {"code": code, "message": message}},
        status_code=status_code,
        headers=headers,
    )


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("password must contain at least 12 characters")
    return PasswordHasher(
        time_cost=3,
        memory_cost=65_536,
        parallelism=4,
        hash_len=32,
        salt_len=16,
    ).hash(password)


def _hash_password_command() -> int:
    password = getpass("Password: ")
    confirmation = getpass("Confirm password: ")
    if not hmac.compare_digest(password, confirmation):
        print("Error: passwords do not match", file=os.sys.stderr)
        return 1
    try:
        generated = hash_password(password)
    except ValueError as exc:
        print(f"Error: {exc}", file=os.sys.stderr)
        return 1
    print(generated)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenStoryline MVP authentication tools")
    parser.add_argument("command", choices=["hash-password"])
    arguments = parser.parse_args()
    if arguments.command == "hash-password":
        return _hash_password_command()
    return 1


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.startswith("replace-"):
        raise AuthConfigurationError(f"{name} is required")
    return value


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise AuthConfigurationError(f"{name} must be true or false")


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        raise AuthConfigurationError(f"{name} must be an integer") from None
    if not minimum <= value <= maximum:
        raise AuthConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _normalize_origin(raw: str) -> str:
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise AuthConfigurationError(
            "OPENSTORYLINE_PUBLIC_ORIGIN must be an http(s) origin without a path"
        )
    return f"{parsed.scheme}://{parsed.netloc}".lower()


if __name__ == "__main__":
    raise SystemExit(main())
