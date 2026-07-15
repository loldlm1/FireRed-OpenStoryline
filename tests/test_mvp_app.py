import os
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx

from mvp_fastapi import create_app
from open_storyline.mvp.jobs import JobManager, JobStore


class MVPAppSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_is_public_but_job_api_requires_token(self):
        with patch.dict(os.environ, {"OPENSTORYLINE_WEB_TOKEN": "a-secure-test-token"}, clear=False):
            with TemporaryDirectory() as tmpdir:
                app = create_app()
                app.state.mvp_jobs = JobStore(tmpdir)
                app.state.mvp_manager = JobManager(app.state.mvp_jobs)
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


if __name__ == "__main__":
    unittest.main()
