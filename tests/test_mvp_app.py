import httpx
import unittest

from mvp_fastapi import create_app


class MVPAppBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_agentic_workspace_page_is_deterministic_without_cache(self):
        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertIn("Estudio de edición", response.text)
        self.assertIn('/static/mvp/app.js', response.text)
        self.assertNotIn("mvp-legacy", response.text)
        csp = response.headers["content-security-policy"]
        for directive in (
            "default-src 'self'",
            "script-src 'self'",
            "connect-src 'self'",
            "media-src 'self' blob:",
            "object-src 'none'",
            "frame-ancestors 'none'",
        ):
            self.assertIn(directive, csp)
        self.assertNotIn("'unsafe-inline'", csp)
        self.assertNotIn("'unsafe-eval'", csp)

    async def test_workspace_static_assets_are_scoped_and_not_cached(self):
        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            module = await client.get("/static/mvp/app.js")
            traversal = await client.get("/static/mvp/%2e%2e/mvp-legacy.html")

        self.assertEqual(module.status_code, 200)
        self.assertIn("javascript", module.headers["content-type"])
        self.assertEqual(module.headers["cache-control"], "no-store, max-age=0")
        self.assertEqual(module.headers["x-content-type-options"], "nosniff")
        self.assertIn("script-src 'self'", module.headers["content-security-policy"])
        self.assertEqual(traversal.status_code, 404)

    async def test_health_is_public_and_api_fails_closed_without_auth_service(self):
        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            health = await client.get("/health")
            protected = await client.get("/api/mvp/jobs/invalid")
            session = await client.get("/api/mvp/auth/session")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["inference"], "remote-only")
        self.assertEqual(health.headers["x-content-type-options"], "nosniff")
        self.assertEqual(health.headers["referrer-policy"], "no-referrer")
        self.assertNotIn("content-security-policy", health.headers)
        self.assertEqual(protected.status_code, 503)
        self.assertEqual(protected.json()["detail"]["code"], "AUTH_UNAVAILABLE")
        self.assertEqual(session.status_code, 503)

    async def test_up_reports_database_readiness_without_backend_details(self):
        class DatabaseStub:
            def __init__(self, ready: bool, code: str) -> None:
                self.result = type("Readiness", (), {"ready": ready, "code": code})()

            async def readiness(self):
                return self.result

        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            app.state.database = DatabaseStub(True, "DATABASE_READY")
            healthy = await client.get("/up")
            app.state.database = DatabaseStub(False, "DATABASE_UNAVAILABLE")
            unavailable = await client.get("/up")
            app.state.database = DatabaseStub(False, "DATABASE_SCHEMA_OUTDATED")
            outdated = await client.get("/up")

        self.assertEqual(healthy.status_code, 200)
        self.assertEqual(healthy.json(), {"status": "ok"})
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(
            unavailable.json(),
            {"status": "unavailable", "code": "DATABASE_UNAVAILABLE"},
        )
        self.assertNotIn("postgres", unavailable.text.lower())
        self.assertEqual(outdated.status_code, 503)
        self.assertEqual(
            outdated.json(),
            {"status": "unavailable", "code": "DATABASE_SCHEMA_OUTDATED"},
        )
        self.assertNotIn("revision", outdated.text.lower())


if __name__ == "__main__":
    unittest.main()
