import os
import httpx
import unittest
from unittest.mock import patch

from mvp_fastapi import SessionWorkspaceConfigurationError, create_app


class SessionWorkspaceConfigurationTests(unittest.TestCase):
    def test_workspace_mode_defaults_to_legacy_and_accepts_enabled(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(create_app().state.session_workspace_mode, "legacy")
        with patch.dict(
            os.environ,
            {"OPENSTORYLINE_SESSION_WORKSPACE_MODE": "enabled"},
            clear=False,
        ):
            self.assertEqual(create_app().state.session_workspace_mode, "enabled")

    def test_invalid_workspace_mode_fails_with_sanitized_error(self):
        invalid_value = "secret-bearing-unknown-workspace-mode"
        with patch.dict(
            os.environ,
            {"OPENSTORYLINE_SESSION_WORKSPACE_MODE": invalid_value},
            clear=False,
        ):
            with self.assertRaises(SessionWorkspaceConfigurationError) as raised:
                create_app()

        self.assertNotIn(invalid_value, str(raised.exception))


class MVPAppBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_workspace_modes_serve_deterministic_pages_without_cache(self):
        bodies = {}
        for mode in ("legacy", "enabled"):
            with patch.dict(
                os.environ,
                {"OPENSTORYLINE_SESSION_WORKSPACE_MODE": mode},
                clear=False,
            ):
                app = create_app()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            bodies[mode] = response.text

        self.assertIn("Mesa de shorts", bodies["legacy"])
        self.assertNotIn('/static/mvp/app.js', bodies["legacy"])
        self.assertIn("Estudio de edición", bodies["enabled"])
        self.assertIn('/static/mvp/app.js', bodies["enabled"])
        self.assertNotEqual(bodies["legacy"], bodies["enabled"])

    async def test_workspace_static_assets_are_scoped_and_not_cached(self):
        with patch.dict(
            os.environ,
            {"OPENSTORYLINE_SESSION_WORKSPACE_MODE": "enabled"},
            clear=False,
        ):
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
