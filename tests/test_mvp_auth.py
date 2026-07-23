from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from starlette.requests import Request

from mvp_fastapi import create_app
from open_storyline.mvp.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    AuthConfigurationError,
    AuthService,
    AuthSettings,
    LoginAttemptLimiter,
    LoginPolicy,
    LoginRule,
    hash_password,
)
from open_storyline.mvp.database import Database, normalize_database_url
from open_storyline.mvp.jobs import JobManager, JobStore
from open_storyline.mvp.models import AuthSession, LoginAttemptBucket


ROOT = Path(__file__).resolve().parents[1]
TEST_PASSWORD = "correct horse battery staple"
TEST_PEPPER = "test-pepper-value-with-at-least-thirty-two-characters"


def _settings(
    password_hash: str,
    *,
    trust_proxy_headers: bool = False,
    client_limit: int = 10,
    global_limit: int = 120,
) -> AuthSettings:
    return AuthSettings(
        password_hash=password_hash,
        pepper=TEST_PEPPER,
        public_origin="https://test",
        allow_insecure_http=False,
        trust_proxy_headers=trust_proxy_headers,
        idle_ttl=timedelta(hours=12),
        absolute_ttl=timedelta(days=7),
        last_seen_interval=timedelta(minutes=5),
        login_policy=LoginPolicy(
            client=LoginRule(minute=client_limit, day=1000),
            global_scope=LoginRule(minute=global_limit, day=10000),
        ),
    )


def _request(*, forwarded_for: str = "") -> Request:
    headers = [(b"origin", b"https://test")]
    if forwarded_for:
        headers.append((b"x-forwarded-for", forwarded_for.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/mvp/auth/login",
            "headers": headers,
            "client": ("192.0.2.4", 443),
            "server": ("test", 443),
            "scheme": "https",
        }
    )


class PasswordAndSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password(TEST_PASSWORD)

    def test_generated_hash_is_argon2id_and_verifies(self):
        self.assertTrue(self.password_hash.startswith("$argon2id$"))
        service = AuthService(object(), _settings(self.password_hash))
        self.assertTrue(service._verify_password(TEST_PASSWORD))
        self.assertFalse(service._verify_password("wrong password"))

    def test_insecure_http_requires_an_explicit_development_setting(self):
        environment = {
            "OPENSTORYLINE_WEB_PASSWORD_HASH": self.password_hash,
            "OPENSTORYLINE_SECURITY_PEPPER": TEST_PEPPER,
            "OPENSTORYLINE_PUBLIC_ORIGIN": "http://127.0.0.1:8000",
            "OPENSTORYLINE_ALLOW_INSECURE_HTTP": "false",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(AuthConfigurationError):
                AuthSettings.from_env()
        environment["OPENSTORYLINE_ALLOW_INSECURE_HTTP"] = "true"
        with patch.dict(os.environ, environment, clear=True):
            settings = AuthSettings.from_env()
        self.assertFalse(settings.secure_cookies)

    def test_direct_mode_ignores_spoofed_forwarding_headers(self):
        direct = AuthService(object(), _settings(self.password_hash))
        proxied = AuthService(
            object(), _settings(self.password_hash, trust_proxy_headers=True)
        )
        base = _request()
        spoofed = _request(forwarded_for="198.51.100.9")

        self.assertEqual(direct.client_digest(base), direct.client_digest(spoofed))
        self.assertNotEqual(proxied.client_digest(base), proxied.client_digest(spoofed))

    def test_proxy_mode_uses_the_proxy_appended_address(self):
        proxied = AuthService(
            object(), _settings(self.password_hash, trust_proxy_headers=True)
        )
        appended = _request(forwarded_for="198.51.100.9, 203.0.113.44")
        expected = _request(forwarded_for="203.0.113.44")

        self.assertEqual(
            proxied.client_digest(appended),
            proxied.client_digest(expected),
        )

    def test_password_and_session_configuration_errors_are_sanitized(self):
        with self.assertRaises(AuthConfigurationError) as raised:
            AuthService(
                object(),
                _settings("$argon2id$secret-but-invalid"),
            )
        self.assertNotIn("secret-but-invalid", str(raised.exception))

    def test_non_argon2id_hash_is_rejected(self):
        with self.assertRaises(AuthConfigurationError):
            AuthService(object(), _settings("$argon2i$v=19$m=65536,t=3,p=4$invalid"))


def _integration_url() -> str:
    raw = os.getenv("TEST_DATABASE_URL", "").strip()
    if not raw:
        return ""
    url = make_url(normalize_database_url(raw))
    if not str(url.database or "").startswith("openstoryline_test"):
        raise RuntimeError("TEST_DATABASE_URL must use an openstoryline_test database")
    return raw


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class PasswordAndSessionTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.database_url = _integration_url()
        cls.password_hash = hash_password(TEST_PASSWORD)
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env={**os.environ, "DATABASE_URL": cls.database_url},
            check=True,
            capture_output=True,
            text=True,
        )

    async def asyncSetUp(self):
        self.database = Database(self.database_url)
        async with self.database.engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE job_stage_checkpoints, session_analysis_cache, "
                    "audit_reviews, audit_documents, artifacts, job_events, "
                    "video_jobs, prompt_versions, session_input_videos, editing_sessions, "
                    "auth_sessions, login_attempt_buckets"
                )
            )
        self.temporary_directory = TemporaryDirectory()
        self.store = JobStore(self.temporary_directory.name, self.database)
        self.manager = JobManager(self.store)

    async def asyncTearDown(self):
        await self.database.dispose()
        self.temporary_directory.cleanup()

    def _app(self, *, client_limit: int = 10, global_limit: int = 120):
        app = create_app()
        app.state.database = self.database
        app.state.auth_service = AuthService(
            self.database,
            _settings(
                self.password_hash,
                client_limit=client_limit,
                global_limit=global_limit,
            ),
        )
        app.state.mvp_jobs = self.store
        app.state.mvp_manager = self.manager
        return app

    async def _login(self, client: httpx.AsyncClient, password: str = TEST_PASSWORD):
        return await client.post(
            "/api/mvp/auth/login",
            json={"password": password},
            headers={"Origin": "https://test"},
        )

    async def test_login_uses_opaque_server_session_and_secure_cookie_flags(self):
        app = self._app()
        transport = httpx.ASGITransport(app=app, client=("192.0.2.4", 443))
        async with httpx.AsyncClient(
            transport=transport, base_url="https://test"
        ) as client:
            login = await self._login(client)
            session_token = client.cookies.get(SESSION_COOKIE)
            csrf_token = client.cookies.get(CSRF_COOKIE)
            cookie_headers = login.headers.get_list("set-cookie")
            status = await client.get("/api/mvp/auth/session")
            old_token = session_token
            rotated = await self._login(client)

        self.assertEqual(login.status_code, 200)
        self.assertEqual(rotated.status_code, 200)
        self.assertTrue(session_token)
        self.assertTrue(csrf_token)
        self.assertIn("HttpOnly", cookie_headers[0])
        self.assertIn("Secure", cookie_headers[0])
        self.assertIn("SameSite=lax", cookie_headers[0])
        self.assertNotIn("HttpOnly", cookie_headers[1])
        self.assertIn("Secure", cookie_headers[1])
        self.assertIn("SameSite=strict", cookie_headers[1])
        self.assertTrue(status.json()["authenticated"])
        self.assertIsNone(await app.state.auth_service.resolve_session(old_token))

        async with self.database.sessions() as session:
            rows = list((await session.execute(select(AuthSession))).scalars())
        serialized = " ".join(
            f"{row.token_digest} {row.csrf_digest}" for row in rows
        )
        self.assertNotIn(session_token, serialized)
        self.assertNotIn(csrf_token, serialized)
        self.assertTrue(all(len(row.token_digest) == 64 for row in rows))

    async def test_csrf_logout_and_legacy_headers_fail_closed(self):
        app = self._app()
        transport = httpx.ASGITransport(app=app, client=("192.0.2.4", 443))
        async with httpx.AsyncClient(
            transport=transport, base_url="https://test"
        ) as client:
            self.assertEqual((await self._login(client)).status_code, 200)
            csrf_token = client.cookies.get(CSRF_COOKIE)
            missing = await client.post("/api/mvp/jobs")
            wrong_origin = await client.post(
                "/api/mvp/jobs",
                headers={"Origin": "https://evil.test", "X-CSRF-Token": csrf_token},
            )
            passed_middleware = await client.post(
                "/api/mvp/jobs",
                headers={"Origin": "https://test", "X-CSRF-Token": csrf_token},
            )
            logout = await client.post(
                "/api/mvp/auth/logout",
                headers={"Origin": "https://test", "X-CSRF-Token": csrf_token},
            )
            after_logout = await client.get("/api/mvp/jobs/invalid")

        async with httpx.AsyncClient(
            transport=transport, base_url="https://test"
        ) as legacy_client:
            bearer = await legacy_client.get(
                "/api/mvp/jobs/invalid",
                headers={"Authorization": "Bearer obsolete-token"},
            )
            api_key = await legacy_client.get(
                "/api/mvp/jobs/invalid",
                headers={"X-API-Key": "obsolete-token"},
            )

        self.assertEqual(missing.status_code, 403)
        self.assertEqual(wrong_origin.status_code, 403)
        self.assertEqual(passed_middleware.status_code, 409)
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(after_logout.status_code, 401)
        self.assertEqual(bearer.status_code, 401)
        self.assertEqual(api_key.status_code, 401)
        self.assertNotIn("www-authenticate", bearer.headers)

    async def test_idle_and_absolute_expiry_are_enforced_server_side(self):
        app = self._app()
        transport = httpx.ASGITransport(app=app, client=("192.0.2.4", 443))
        async with httpx.AsyncClient(
            transport=transport, base_url="https://test"
        ) as client:
            await self._login(client)
            idle_token = client.cookies.get(SESSION_COOKIE)
            idle_digest = app.state.auth_service.digest(idle_token)
            async with self.database.engine.begin() as connection:
                await connection.execute(
                    update(AuthSession)
                    .where(AuthSession.token_digest == idle_digest)
                    .values(idle_expires_at=datetime.now(UTC) - timedelta(seconds=1))
                )
            idle_expired = await client.get("/api/mvp/jobs/invalid")

            await self._login(client)
            absolute_token = client.cookies.get(SESSION_COOKIE)
            absolute_digest = app.state.auth_service.digest(absolute_token)
            absolute_expiry = datetime.now(UTC) - timedelta(seconds=1)
            async with self.database.engine.begin() as connection:
                await connection.execute(
                    update(AuthSession)
                    .where(AuthSession.token_digest == absolute_digest)
                    .values(
                        idle_expires_at=absolute_expiry - timedelta(seconds=1),
                        absolute_expires_at=absolute_expiry,
                    )
                )
            absolute_expired = await client.get("/api/mvp/jobs/invalid")

        self.assertEqual(idle_expired.status_code, 401)
        self.assertEqual(absolute_expired.status_code, 401)

    async def test_password_hash_rotation_invalidates_existing_sessions(self):
        app = self._app()
        transport = httpx.ASGITransport(app=app, client=("192.0.2.4", 443))
        async with httpx.AsyncClient(
            transport=transport, base_url="https://test"
        ) as client:
            self.assertEqual((await self._login(client)).status_code, 200)
            session_token = client.cookies.get(SESSION_COOKIE)

        rotated = AuthService(
            self.database,
            _settings(hash_password("a different project password")),
        )
        self.assertIsNone(await rotated.resolve_session(session_token))

    async def test_only_failed_passwords_consume_persistent_limits(self):
        app = self._app(client_limit=2)
        transport = httpx.ASGITransport(app=app, client=("192.0.2.4", 443))
        async with httpx.AsyncClient(
            transport=transport, base_url="https://test"
        ) as client:
            self.assertEqual((await self._login(client)).status_code, 200)
            self.assertEqual((await self._login(client)).status_code, 200)
        async with self.database.sessions() as session:
            success_count = await session.scalar(
                select(func.count()).select_from(LoginAttemptBucket)
            )
        self.assertEqual(success_count, 0)

        async with self.database.engine.begin() as connection:
            await connection.execute(text("TRUNCATE auth_sessions, login_attempt_buckets"))
        app = self._app(client_limit=2)
        transport = httpx.ASGITransport(app=app, client=("192.0.2.4", 443))
        async with httpx.AsyncClient(
            transport=transport, base_url="https://test"
        ) as client:
            first = await self._login(client, "wrong-one")
            second = await self._login(client, "wrong-two")
            limited = await self._login(client, "wrong-three")
            correct_but_locked = await self._login(client)

        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 401)
        self.assertEqual(limited.status_code, 429)
        self.assertIn("retry-after", limited.headers)
        self.assertEqual(correct_but_locked.status_code, 429)

    async def test_authenticated_reads_and_session_creation_have_no_quota(self):
        app = self._app()
        transport = httpx.ASGITransport(app=app, client=("192.0.2.4", 443))
        async with httpx.AsyncClient(
            transport=transport, base_url="https://test"
        ) as client:
            self.assertEqual((await self._login(client)).status_code, 200)
            csrf_token = client.cookies.get(CSRF_COOKIE)
            reads = [
                await client.get(f"/api/mvp/jobs/{index:032x}")
                for index in range(8)
            ]
            sessions = [
                await client.post(
                    "/api/mvp/sessions",
                    headers={
                        "Origin": "https://test",
                        "X-CSRF-Token": csrf_token,
                    },
                    json={"title": f"quota regression {index}"},
                )
                for index in range(6)
            ]

        self.assertTrue(all(response.status_code == 404 for response in reads))
        self.assertTrue(all(response.status_code == 201 for response in sessions))
        for response in [*reads, *sessions]:
            self.assertNotIn("retry-after", response.headers)
            self.assertFalse(
                any(name.lower().startswith("x-ratelimit") for name in response.headers)
            )

    async def test_concurrent_failed_attempts_do_not_oversubscribe_limit(self):
        policy = LoginPolicy(
            client=LoginRule(minute=5, day=100),
            global_scope=LoginRule(minute=100, day=1000),
        )
        limiter = LoginAttemptLimiter(
            self.database,
            policy,
            lambda: datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
        )
        decisions = await asyncio.gather(
            *[limiter.record_failure("a" * 64) for _ in range(20)]
        )
        self.assertEqual(sum(item.allowed for item in decisions), 5)


if __name__ == "__main__":
    unittest.main()
