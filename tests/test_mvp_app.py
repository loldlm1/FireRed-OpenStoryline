import os
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx

from mvp_fastapi import create_app
from open_storyline.mvp.jobs import JobManager, JobStore
from open_storyline.mvp.rate_limit import PersistentRateLimiter, RatePolicy


class MVPAppSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_up_reports_database_readiness_without_backend_details(self):
        class DatabaseStub:
            def __init__(self, ready: bool, code: str) -> None:
                self.result = type("Readiness", (), {"ready": ready, "code": code})()

            async def readiness(self):
                return self.result

        with patch.dict(os.environ, {"OPENSTORYLINE_WEB_TOKEN": "a-secure-test-token"}, clear=False):
            app = create_app()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                app.state.database = DatabaseStub(True, "DATABASE_READY")
                healthy = await client.get("/up")
                app.state.database = DatabaseStub(False, "DATABASE_UNAVAILABLE")
                unavailable = await client.get("/up")

        self.assertEqual(healthy.status_code, 200)
        self.assertEqual(healthy.json(), {"status": "ok"})
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(
            unavailable.json(),
            {"status": "unavailable", "code": "DATABASE_UNAVAILABLE"},
        )
        self.assertNotIn("postgres", unavailable.text.lower())

    async def test_health_is_public_but_job_api_requires_token(self):
        with patch.dict(os.environ, {"OPENSTORYLINE_WEB_TOKEN": "a-secure-test-token"}, clear=False):
            with TemporaryDirectory() as tmpdir:
                app = create_app()
                app.state.mvp_jobs = JobStore(tmpdir)
                app.state.mvp_manager = JobManager(app.state.mvp_jobs)
                app.state.rate_limiter = PersistentRateLimiter(os.path.join(tmpdir, "limits.sqlite3"))
                app.state.rate_policy = RatePolicy.from_env()
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    health = await client.get("/health")
                    unauthorized = await client.get("/api/mvp/jobs/invalid")
                    authorized = await client.get(
                        "/api/mvp/jobs/invalid",
                        headers={"Authorization": "Bearer a-secure-test-token"},
                    )

        self.assertEqual(health.status_code, 200)
        self.assertEqual(unauthorized.status_code, 401)
        self.assertNotEqual(authorized.status_code, 401)

    async def test_x_api_key_is_supported_and_job_quota_returns_429(self):
        environment = {
            "OPENSTORYLINE_WEB_TOKEN": "another-secure-test-token",
            "OPENSTORYLINE_JOBS_RPM": "1",
            "OPENSTORYLINE_JOBS_RPD": "10",
        }
        with patch.dict(os.environ, environment, clear=False):
            with TemporaryDirectory() as tmpdir:
                app = create_app()
                app.state.mvp_jobs = JobStore(tmpdir)
                app.state.mvp_manager = JobManager(app.state.mvp_jobs)
                app.state.rate_limiter = PersistentRateLimiter(os.path.join(tmpdir, "limits.sqlite3"))
                app.state.rate_policy = RatePolicy.from_env()
                transport = httpx.ASGITransport(app=app)
                headers = {"X-API-Key": "another-secure-test-token"}
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    first = await client.post("/api/mvp/jobs", headers=headers)
                    limited = await client.post("/api/mvp/jobs", headers=headers)

        self.assertNotEqual(first.status_code, 401)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["detail"]["scope"], "jobs")
        self.assertEqual(limited.headers["x-ratelimit-limit-minute"], "1")
        self.assertIn("retry-after", limited.headers)

    async def test_repeated_invalid_keys_are_limited_by_client(self):
        environment = {
            "OPENSTORYLINE_WEB_TOKEN": "third-secure-test-token",
            "OPENSTORYLINE_AUTH_RPM": "1",
            "OPENSTORYLINE_AUTH_RPD": "10",
        }
        with patch.dict(os.environ, environment, clear=False):
            with TemporaryDirectory() as tmpdir:
                app = create_app()
                app.state.mvp_jobs = JobStore(tmpdir)
                app.state.mvp_manager = JobManager(app.state.mvp_jobs)
                app.state.rate_limiter = PersistentRateLimiter(os.path.join(tmpdir, "limits.sqlite3"))
                app.state.rate_policy = RatePolicy.from_env()
                transport = httpx.ASGITransport(app=app, client=("192.0.2.4", 123))
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    first = await client.get("/api/mvp/jobs/invalid")
                    limited = await client.get("/api/mvp/jobs/invalid")

        self.assertEqual(first.status_code, 401)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["detail"]["scope"], "unauthorized_client")


if __name__ == "__main__":
    unittest.main()
