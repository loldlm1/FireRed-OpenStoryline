import httpx
import unittest

from mvp_fastapi import create_app


class MVPAppBoundaryTests(unittest.IsolatedAsyncioTestCase):
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

        self.assertEqual(healthy.status_code, 200)
        self.assertEqual(healthy.json(), {"status": "ok"})
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(
            unavailable.json(),
            {"status": "unavailable", "code": "DATABASE_UNAVAILABLE"},
        )
        self.assertNotIn("postgres", unavailable.text.lower())


if __name__ == "__main__":
    unittest.main()
