from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from open_storyline.mvp.database import (
    COMPATIBLE_SCHEMA_REVISIONS,
    LEGACY_SCHEMA_REVISION,
    WORKSPACE_SCHEMA_REVISION,
    Database,
    DatabaseConfigurationError,
    normalize_database_url,
)
from open_storyline.mvp.models import Base


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TABLES = {
    "alembic_version",
    "artifacts",
    "audit_documents",
    "audit_reviews",
    "auth_sessions",
    "editing_sessions",
    "job_events",
    "login_attempt_buckets",
    "prompt_versions",
    "session_input_videos",
    "video_jobs",
}


class DatabaseConfigurationTests(unittest.TestCase):
    def test_normalizes_standard_postgres_urls_for_psycopg(self):
        normalized = normalize_database_url(
            "postgresql://openstoryline:secret@db:5432/openstoryline"
        )
        self.assertTrue(normalized.startswith("postgresql+psycopg://"))

    def test_missing_or_non_postgres_urls_fail_closed(self):
        for value in ("", "sqlite:///tmp/test.db", "postgresql+psycopg:///missing-user"):
            with self.subTest(value=value):
                with self.assertRaises(DatabaseConfigurationError):
                    normalize_database_url(value)

    def test_configuration_errors_do_not_retain_secret_bearing_causes(self):
        secret = "do-not-leak-this-password"
        with self.assertRaises(DatabaseConfigurationError) as raised:
            normalize_database_url(f"mysql://user:{secret}@db/example")
        self.assertNotIn(secret, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    def test_pool_settings_are_bounded(self):
        environment = {
            "OPENSTORYLINE_DATABASE_POOL_SIZE": "1000",
            "OPENSTORYLINE_DATABASE_MAX_OVERFLOW": "0",
        }
        with patch.dict(os.environ, environment, clear=False):
            with self.assertRaises(DatabaseConfigurationError) as raised:
                Database("postgresql+psycopg://user:password@db/example")
        self.assertNotIn("password", str(raised.exception))


class WorkspaceModelContractTests(unittest.TestCase):
    def test_workspace_metadata_exposes_additive_constraints_and_indexes(self):
        tables = Base.metadata.tables
        self.assertTrue((REQUIRED_TABLES - {"alembic_version"}).issubset(tables))
        self.assertIn("workflow_version", tables["editing_sessions"].c)
        self.assertIn("prompt_version_id", tables["video_jobs"].c)
        self.assertIn("attempt_number", tables["video_jobs"].c)
        self.assertIn("is_favorite", tables["video_jobs"].c)
        self.assertIn("audience", tables["job_events"].c)

        constraint_names = {
            constraint.name
            for table_name in (
                "editing_sessions",
                "session_input_videos",
                "prompt_versions",
                "video_jobs",
                "job_events",
            )
            for constraint in tables[table_name].constraints
        }
        self.assertIn("uq_session_input_videos_session", constraint_names)
        self.assertIn("uq_prompt_versions_session_number", constraint_names)
        self.assertIn("uq_video_jobs_prompt_attempt", constraint_names)
        self.assertIn("ck_session_input_videos_ready_metadata", constraint_names)
        self.assertIn("ck_job_events_audience", constraint_names)
        self.assertIn(
            "uq_video_jobs_session_favorite",
            {index.name for index in tables["video_jobs"].indexes},
        )

    def test_workspace_migration_is_schema_only(self):
        migration = (
            ROOT
            / "migrations"
            / "versions"
            / "20260719_0002_add_reusable_session_workspace.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("UPDATE video_jobs", migration)
        self.assertNotIn("SELECT * FROM video_jobs", migration)
        restore_check = (ROOT / "scripts" / "mvp-postgres-restore-check.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("20260719_0002", restore_check)
        self.assertIn("REQUIRED_TABLE_COUNT=10", restore_check)


class _FakeConnection:
    def __init__(self, revision: str | None, delay: float = 0) -> None:
        self.revision = revision
        self.delay = delay

    async def execute(self, _statement):
        if self.delay:
            await asyncio.sleep(self.delay)

    async def scalar(self, _statement):
        return self.revision


class _ConnectionContext:
    def __init__(self, connection=None, error: Exception | None = None) -> None:
        self.connection = connection
        self.error = error

    async def __aenter__(self):
        if self.error is not None:
            raise self.error
        return self.connection

    async def __aexit__(self, *_args):
        return False


class _FakeEngine:
    def __init__(self, context: _ConnectionContext) -> None:
        self.context = context

    def connect(self):
        return self.context


def _database_with_engine(engine, *, timeout: float = 1) -> Database:
    database = object.__new__(Database)
    database.engine = engine
    database.query_timeout = timeout
    return database


class DatabaseReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_compatible_schemas_are_ready(self):
        self.assertEqual(
            COMPATIBLE_SCHEMA_REVISIONS,
            frozenset({LEGACY_SCHEMA_REVISION, WORKSPACE_SCHEMA_REVISION}),
        )
        for revision in COMPATIBLE_SCHEMA_REVISIONS:
            with self.subTest(revision=revision):
                database = _database_with_engine(
                    _FakeEngine(_ConnectionContext(_FakeConnection(revision)))
                )
                readiness = await database.readiness()
                self.assertTrue(readiness.ready)
                self.assertEqual(readiness.code, "DATABASE_READY")

    async def test_missing_obsolete_and_unknown_schemas_are_not_ready(self):
        for revision in (None, "20260716_0000", "20260720_unknown"):
            with self.subTest(revision=revision):
                database = _database_with_engine(
                    _FakeEngine(_ConnectionContext(_FakeConnection(revision)))
                )
                readiness = await database.readiness()
                self.assertFalse(readiness.ready)
                self.assertEqual(readiness.code, "DATABASE_SCHEMA_OUTDATED")

    async def test_database_errors_and_timeouts_are_sanitized(self):
        failing = _database_with_engine(
            _FakeEngine(
                _ConnectionContext(error=SQLAlchemyError("secret-bearing backend error"))
            )
        )
        timed_out = _database_with_engine(
            _FakeEngine(_ConnectionContext(_FakeConnection(LEGACY_SCHEMA_REVISION, 0.1))),
            timeout=0.01,
        )
        for database in (failing, timed_out):
            with self.subTest(database=database):
                readiness = await database.readiness()
                self.assertFalse(readiness.ready)
                self.assertEqual(readiness.code, "DATABASE_UNAVAILABLE")


class BackupScriptTests(unittest.TestCase):
    def test_remote_backup_and_restore_stream_into_the_accessory_container(self):
        scripts = (
            (ROOT / "scripts" / "mvp-postgres-backup.sh", b"pg_dump"),
            (ROOT / "scripts" / "mvp-postgres-restore-check.sh", b"createdb"),
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            command_dir = root / "bin"
            command_dir.mkdir()
            argument_capture = root / "ssh-arguments"
            stdin_capture = root / "ssh-stdin"
            ssh = command_dir / "ssh"
            ssh.write_text(
                "#!/bin/sh\n"
                ": > \"$SSH_ARGUMENT_CAPTURE\"\n"
                "for argument in \"$@\"; do printf '%s\\0' \"$argument\" >> \"$SSH_ARGUMENT_CAPTURE\"; done\n"
                "dd of=\"$SSH_STDIN_CAPTURE\" status=none\n",
                encoding="utf-8",
            )
            ssh.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": f"{command_dir}:{os.environ['PATH']}",
                "OPENSTORYLINE_POSTGRES_ADMIN_MODE": "kamal",
                "KAMAL_HOST": "203.0.113.10",
                "SSH_ARGUMENT_CAPTURE": str(argument_capture),
                "SSH_STDIN_CAPTURE": str(stdin_capture),
            }

            for script, expected_command in scripts:
                with self.subTest(script=script.name):
                    result = subprocess.run(
                        [str(script)],
                        cwd=ROOT,
                        env=environment,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    arguments = argument_capture.read_bytes()
                    self.assertIn(
                        b"docker exec -i 'openstoryline-mvp-db' sh",
                        arguments,
                    )
                    self.assertIn(expected_command, stdin_capture.read_bytes())

    def test_remote_migration_uses_the_exact_image_without_publishing_a_port(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            command_dir = root / "bin"
            command_dir.mkdir()
            ssh_capture = root / "ssh-arguments"
            env_file = root / "kamal.env"
            database_url = "postgresql+psycopg://app:test-password@db/openstoryline"
            env_file.write_text(
                "KAMAL_HOST=203.0.113.10\n"
                f"DATABASE_URL={database_url}\n",
                encoding="utf-8",
            )

            ssh = command_dir / "ssh"
            ssh.write_text(
                "#!/bin/sh\n"
                "printf '<call>\\0' >> \"$SSH_CAPTURE\"\n"
                "for argument in \"$@\"; do printf '%s\\0' \"$argument\" >> \"$SSH_CAPTURE\"; done\n",
                encoding="utf-8",
            )
            ssh.chmod(0o755)
            scp = command_dir / "scp"
            scp.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            scp.chmod(0o755)

            environment = {
                **os.environ,
                "PATH": f"{command_dir}:{os.environ['PATH']}",
                "KAMAL_ENV_FILE": str(env_file),
                "OPENSTORYLINE_APP_VERSION": "release-test",
                "SSH_CAPTURE": str(ssh_capture),
            }
            result = subprocess.run(
                [str(ROOT / "bin" / "kamal-mvp"), "db", "migrate"],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            ssh_arguments = ssh_capture.read_bytes()
            self.assertIn(b"localhost:5555/openstoryline-mvp:release-test", ssh_arguments)
            self.assertIn(b"docker run --rm --network kamal", ssh_arguments)
            self.assertIn(b"alembic upgrade head", ssh_arguments)
            self.assertNotIn(b"--publish", ssh_arguments)
            self.assertNotIn(database_url.encode(), ssh_arguments)

    def test_failed_dump_keeps_the_previous_backup(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            backup_dir = root / "backups"
            command_dir = root / "bin"
            backup_dir.mkdir()
            command_dir.mkdir()
            target = backup_dir / "openstoryline.latest.dump"
            target.write_bytes(b"previous-valid-backup")

            pg_dump = command_dir / "pg_dump"
            pg_dump.write_text(
                "#!/bin/sh\n"
                "previous=\n"
                "for argument in \"$@\"; do\n"
                "  if [ \"$previous\" = --file ]; then file=$argument; fi\n"
                "  case \"$argument\" in --file=*) file=${argument#--file=};; esac\n"
                "  previous=$argument\n"
                "done\n"
                "printf partial > \"$file\"\n"
                "exit 1\n",
                encoding="utf-8",
            )
            pg_dump.chmod(0o755)
            pg_restore = command_dir / "pg_restore"
            pg_restore.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            pg_restore.chmod(0o755)

            environment = {
                **os.environ,
                "PATH": f"{command_dir}:{os.environ['PATH']}",
                "OPENSTORYLINE_POSTGRES_ADMIN_MODE": "local",
                "OPENSTORYLINE_POSTGRES_BACKUP_DIR": str(backup_dir),
                "PGDATABASE": "openstoryline_test",
            }
            result = subprocess.run(
                [str(ROOT / "scripts" / "mvp-postgres-backup.sh")],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(target.read_bytes(), b"previous-valid-backup")


def _integration_url() -> str:
    raw = os.getenv("TEST_DATABASE_URL", "").strip()
    if not raw:
        return ""
    url = make_url(normalize_database_url(raw))
    if not str(url.database or "").startswith("openstoryline_test"):
        raise RuntimeError("TEST_DATABASE_URL must use an openstoryline_test database")
    return raw


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not configured")
class MigrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.database_url = _integration_url()
        environment = {**os.environ, "DATABASE_URL": cls.database_url}
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

    async def test_empty_database_upgrades_to_current_schema(self):
        database = Database(self.database_url)
        try:
            readiness = await database.readiness()
            async with database.engine.connect() as connection:
                table_names = set(
                    await connection.scalars(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public'"
                        )
                    )
                )
                constraints = set(
                    await connection.scalars(
                        text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE connamespace = 'public'::regnamespace"
                        )
                    )
                )
        finally:
            await database.dispose()

        self.assertTrue(readiness.ready)
        self.assertTrue(REQUIRED_TABLES.issubset(table_names))
        self.assertIn("ck_video_jobs_progress", constraints)
        self.assertIn("ck_session_input_videos_ready_metadata", constraints)
        self.assertIn("uq_prompt_versions_session_number", constraints)
        self.assertIn("uq_video_jobs_prompt_attempt", constraints)
        self.assertIn("ck_job_events_audience", constraints)
        self.assertIn("uq_job_events_job_sequence", constraints)
        self.assertIn("ck_audit_reviews_verdict", constraints)

    async def test_migration_matches_orm_metadata(self):
        environment = {**os.environ, "DATABASE_URL": self.database_url}
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "check"],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
