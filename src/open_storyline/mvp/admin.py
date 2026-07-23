from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import asyncio
import json
import sys
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from open_storyline.mvp.audit import AuditService, parse_since
from open_storyline.mvp.database import Database, DatabaseConfigurationError
from open_storyline.mvp.jobs import JobStore, JobStoreError
from open_storyline.mvp.models import Artifact, EditingSession, PromptVersion, VideoJob
from open_storyline.mvp.prompt_versions import PromptVersionService
from open_storyline.mvp.retention import RetentionService, RetentionSettings
from open_storyline.mvp.security import sanitize_for_persistence
from open_storyline.mvp.session_media import SessionMediaStore
from open_storyline.mvp.settings import default_mvp_config_path, load_mvp_settings


WORKSPACE_BACKFILL_ADVISORY_LOCK = 7_303_110_792_764


def _format_value(value: Any, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return
    if output_format == "ndjson":
        items = value.get("items") if isinstance(value, dict) else value
        if not isinstance(items, list):
            items = [value]
        for item in items:
            print(json.dumps(item, ensure_ascii=False, sort_keys=True))
        return
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        items = value["items"]
        if not items:
            print(
                "No matching defect records."
                if value.get("kind") == "defect_records"
                else "No matching jobs."
            )
            return
        if value.get("kind") == "defect_records":
            print("JOB_ID                           CODE                           STAGE                  DISPOSITION")
            for item in items:
                print(
                    f"{item['job_id']:<32} "
                    f"{str(item.get('code') or '-'):<30} "
                    f"{str(item.get('stage') or '-'):<22} "
                    f"{str(item.get('disposition') or '-')}"
                )
            if value.get("truncated"):
                print("Results truncated; narrow the filters or increase --limit.")
            return
        print("JOB_ID                           STATE      STAGE                 VERDICT       MEDIA")
        for item in items:
            print(
                f"{item['id']:<32} "
                f"{str(item.get('state') or '-'):<10} "
                f"{str(item.get('stage') or '-'):<21} "
                f"{str(item.get('latest_verdict') or '-'):<13} "
                f"{'yes' if item.get('media_available') else 'no'}"
            )
        if value.get("next_cursor"):
            print(f"Next cursor: {value['next_cursor']}")
        return
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _default_root() -> Path:
    config = load_mvp_settings(default_mvp_config_path())
    return Path(config.project.outputs_dir) / "mvp_jobs"


def _build_services(
    database: Database,
    root: str | Path | None = None,
) -> tuple[JobStore, AuditService, RetentionService]:
    settings = RetentionSettings.from_env()
    store = JobStore(
        root or _default_root(),
        database,
        media_retention_days=settings.media_days,
        audit_retention_days=settings.audit_days,
    )
    audit = AuditService(store)
    store.attach_audit(audit)
    retention = RetentionService(store, settings)
    store.attach_retention(retention)
    return store, audit, retention


async def _import_legacy(arguments: argparse.Namespace, database: Database) -> dict[str, Any]:
    store, _audit, _retention = _build_services(database, arguments.root)
    report = await store.import_legacy_jobs(
        arguments.root,
        dry_run=arguments.dry_run,
        batch_size=arguments.batch_size,
        session_title=arguments.session_title,
    )
    return {"ok": True, "dry_run": arguments.dry_run, **report}


def _review_payload(path: str) -> dict[str, Any]:
    if path == "-":
        raw = sys.stdin.read(1024 * 1024 + 1)
    else:
        review_path = Path(path).expanduser()
        if review_path.stat().st_size > 1024 * 1024:
            raise JobStoreError("AUDIT_REVIEW_INVALID", "review input is too large")
        try:
            raw = review_path.read_text(encoding="utf-8")
        except UnicodeError:
            raise JobStoreError(
                "AUDIT_REVIEW_INVALID",
                "review input must be UTF-8 JSON",
            ) from None
    if len(raw.encode("utf-8")) > 1024 * 1024:
        raise JobStoreError("AUDIT_REVIEW_INVALID", "review input is too large")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise JobStoreError("AUDIT_REVIEW_INVALID", "review input must be valid JSON") from None
    if not isinstance(payload, dict):
        raise JobStoreError("AUDIT_REVIEW_INVALID", "review input must be an object")
    return payload


async def _audit_command(
    arguments: argparse.Namespace,
    database: Database,
) -> Any:
    store, audit, retention = _build_services(database)
    if arguments.audit_command == "list":
        return await audit.list_jobs(
            since=parse_since(arguments.since),
            editing_session_id=arguments.session,
            state=arguments.state,
            stage=arguments.stage,
            verdict=arguments.verdict,
            error_code=arguments.error_code,
            media_available=(
                True
                if arguments.media == "available"
                else False
                if arguments.media == "unavailable"
                else None
            ),
            audit_hold=(
                True
                if arguments.hold == "held"
                else False
                if arguments.hold == "unheld"
                else None
            ),
            limit=arguments.limit,
            cursor=arguments.cursor,
        )
    if arguments.audit_command == "show":
        return await audit.show_job(arguments.job_id, limit=arguments.limit)
    if arguments.audit_command == "outcomes":
        return await audit.outcome_slo_summary(
            since=parse_since(arguments.since),
            limit=arguments.limit,
        )
    if arguments.audit_command == "defects":
        return await audit.defect_records(
            since=parse_since(arguments.since),
            code=arguments.code,
            strategy=arguments.strategy,
            disposition=arguments.disposition,
            stage=arguments.stage,
            limit=arguments.limit,
        )
    if arguments.audit_command == "events":
        return await store.events(
            arguments.job_id,
            limit=arguments.limit,
            include_deleted=True,
        )
    if arguments.audit_command == "documents":
        return await audit.documents(arguments.job_id, limit=arguments.limit)
    if arguments.audit_command == "verify":
        return await audit.verify_job(arguments.job_id)
    if arguments.audit_command == "review":
        payload = _review_payload(arguments.input)
        return await audit.add_review(
            arguments.job_id,
            verdict=str(payload.get("verdict") or ""),
            source=str(payload.get("source") or ""),
            reviewer_label=payload.get("reviewer_label"),
            notes=payload.get("notes"),
            findings=payload.get("findings") or {},
        )
    if arguments.audit_command == "backfill":
        return await audit.backfill(dry_run=arguments.dry_run, limit=arguments.limit)
    if arguments.audit_command == "hold":
        if arguments.clear:
            return await retention.clear_audit_hold(arguments.session_id)
        if not arguments.input:
            raise JobStoreError("AUDIT_HOLD_INVALID", "--input is required when setting a hold")
        payload = _review_payload(arguments.input)
        return await retention.set_audit_hold(
            arguments.session_id,
            str(payload.get("reason") or ""),
        )
    raise JobStoreError("AUDIT_COMMAND_INVALID", "audit command is invalid")


async def _retention_command(
    arguments: argparse.Namespace,
    database: Database,
) -> Any:
    _store, _audit, retention = _build_services(database)
    if arguments.retention_command == "status":
        return await retention.status()
    if arguments.retention_command == "preview":
        return await retention.preview(limit=arguments.limit)
    if arguments.retention_command == "run":
        if arguments.apply:
            return await retention.run(limit=arguments.limit)
        return await retention.preview(limit=arguments.limit)
    raise JobStoreError("RETENTION_COMMAND_INVALID", "retention command is invalid")


async def backfill_legacy_prompt_versions(
    database: Database,
    *,
    dry_run: bool,
    limit: int,
    batch_size: int,
) -> dict[str, Any]:
    if not 1 <= int(limit) <= 10_000:
        raise JobStoreError("WORKSPACE_BACKFILL_INVALID", "limit is invalid")
    if not 1 <= int(batch_size) <= 500:
        raise JobStoreError("WORKSPACE_BACKFILL_INVALID", "batch size is invalid")

    async with database.engine.connect() as connection:
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            acquired = await session.scalar(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": WORKSPACE_BACKFILL_ADVISORY_LOCK},
            )
            await session.commit()
            if not acquired:
                raise JobStoreError(
                    "WORKSPACE_BACKFILL_BUSY", "workspace backfill is already running"
                )
            try:
                eligible = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(VideoJob)
                        .join(
                            EditingSession,
                            EditingSession.id == VideoJob.editing_session_id,
                        )
                        .where(
                            VideoJob.prompt_version_id.is_(None),
                            EditingSession.workflow_version == 1,
                        )
                    )
                    or 0
                )
                await session.commit()
                if dry_run:
                    return {
                        "ok": True,
                        "dry_run": True,
                        "eligible": eligible,
                        "processed": 0,
                        "remaining": eligible,
                        "complete": eligible == 0,
                    }

                processed = 0
                while processed < int(limit):
                    current_batch = min(int(batch_size), int(limit) - processed)
                    async with session.begin():
                        rows = list(
                            (
                                await session.execute(
                                    select(VideoJob)
                                    .join(
                                        EditingSession,
                                        EditingSession.id == VideoJob.editing_session_id,
                                    )
                                    .where(
                                        VideoJob.prompt_version_id.is_(None),
                                        EditingSession.workflow_version == 1,
                                    )
                                    .order_by(
                                        VideoJob.editing_session_id,
                                        VideoJob.created_at,
                                        VideoJob.id,
                                    )
                                    .limit(current_batch)
                                    .with_for_update(skip_locked=True)
                                )
                            ).scalars()
                        )
                        if not rows:
                            break
                        next_versions: dict[str, int] = {}
                        for row in rows:
                            if row.editing_session_id not in next_versions:
                                owner = await session.scalar(
                                    select(EditingSession)
                                    .where(EditingSession.id == row.editing_session_id)
                                    .with_for_update()
                                )
                                if owner is None:
                                    raise JobStoreError(
                                        "WORKSPACE_BACKFILL_INVALID",
                                        "job session is unavailable",
                                    )
                                current = await session.scalar(
                                    select(
                                        func.coalesce(
                                            func.max(PromptVersion.version_number), 0
                                        )
                                    ).where(
                                        PromptVersion.editing_session_id
                                        == row.editing_session_id
                                    )
                                )
                                next_versions[row.editing_session_id] = int(current or 0)
                            next_versions[row.editing_session_id] += 1
                            settings = sanitize_for_persistence(row.request_data or {})
                            if not isinstance(settings, dict):
                                settings = {}
                            prompt_version = PromptVersion(
                                id=uuid.uuid4().hex,
                                editing_session_id=row.editing_session_id,
                                version_number=next_versions[row.editing_session_id],
                                prompt=row.prompt,
                                settings_data=settings,
                                created_at=row.created_at,
                            )
                            session.add(prompt_version)
                            await session.flush()
                            row.prompt_version_id = prompt_version.id
                            row.attempt_number = 1
                        processed += len(rows)

                remaining = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(VideoJob)
                        .join(
                            EditingSession,
                            EditingSession.id == VideoJob.editing_session_id,
                        )
                        .where(
                            VideoJob.prompt_version_id.is_(None),
                            EditingSession.workflow_version == 1,
                        )
                    )
                    or 0
                )
                await session.commit()
                return {
                    "ok": True,
                    "dry_run": False,
                    "eligible": eligible,
                    "processed": processed,
                    "remaining": remaining,
                    "complete": remaining == 0,
                }
            finally:
                if session.in_transaction():
                    await session.rollback()
                await session.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": WORKSPACE_BACKFILL_ADVISORY_LOCK},
                )
                await session.commit()


async def inventory_workflows(database: Database) -> dict[str, Any]:
    """Return aggregate-only workflow compatibility evidence."""
    async with database.sessions() as session:
        revision = await session.scalar(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        )
        session_rows = list(
            (
                await session.execute(
                    select(
                        EditingSession.workflow_version,
                        func.count(EditingSession.id),
                    ).group_by(EditingSession.workflow_version)
                )
            ).all()
        )
        job_rows = list(
            (
                await session.execute(
                    select(
                        EditingSession.workflow_version,
                        VideoJob.state,
                        func.count(VideoJob.id),
                    )
                    .join(
                        VideoJob,
                        VideoJob.editing_session_id == EditingSession.id,
                    )
                    .group_by(EditingSession.workflow_version, VideoJob.state)
                )
            ).all()
        )
        artifact_rows = list(
            (
                await session.execute(
                    select(
                        EditingSession.workflow_version,
                        Artifact.availability,
                        func.count(Artifact.id),
                    )
                    .join(VideoJob, VideoJob.id == Artifact.job_id)
                    .join(
                        EditingSession,
                        EditingSession.id == VideoJob.editing_session_id,
                    )
                    .group_by(
                        EditingSession.workflow_version,
                        Artifact.availability,
                    )
                )
            ).all()
        )
        prompt_rows = list(
            (
                await session.execute(
                    select(
                        EditingSession.workflow_version,
                        func.count(PromptVersion.id),
                    )
                    .join(
                        PromptVersion,
                        PromptVersion.editing_session_id == EditingSession.id,
                    )
                    .group_by(EditingSession.workflow_version)
                )
            ).all()
        )

    session_counts = {str(version): int(count) for version, count in session_rows}
    job_counts: dict[str, dict[str, int]] = {}
    for version, state, count in job_rows:
        job_counts.setdefault(str(version), {})[str(state)] = int(count)
    artifact_counts: dict[str, dict[str, int]] = {}
    for version, availability, count in artifact_rows:
        artifact_counts.setdefault(str(version), {})[str(availability)] = int(count)
    prompt_counts = {str(version): int(count) for version, count in prompt_rows}
    unknown_versions = sorted(
        int(value) for value in session_counts if int(value) not in {1, 2}
    )
    if unknown_versions:
        raise JobStoreError(
            "SESSION_WORKFLOW_VERSION_UNKNOWN",
            "unknown workflow versions require manual review",
        )

    def workflow(version: int) -> dict[str, Any]:
        jobs = job_counts.get(str(version), {})
        artifacts = artifact_counts.get(str(version), {})
        return {
            "sessions": session_counts.get(str(version), 0),
            "jobs": {"total": sum(jobs.values()), "by_state": jobs},
            "active_jobs": sum(jobs.get(state, 0) for state in ("uploading", "queued", "running")),
            "prompt_versions": prompt_counts.get(str(version), 0),
            "artifacts": {"total": sum(artifacts.values()), "by_availability": artifacts},
            "executable": version == 2,
        }

    return {
        "ok": True,
        "read_only": True,
        "schema_revision": str(revision or "unknown"),
        "workflow_v1_history": workflow(1),
        "workflow_v2_agentic": workflow(2),
    }


async def _workspace_command(
    arguments: argparse.Namespace,
    database: Database,
) -> dict[str, Any]:
    if arguments.workspace_command == "inventory":
        return await inventory_workflows(database)
    if arguments.workspace_command == "backfill-prompts":
        return await backfill_legacy_prompt_versions(
            database,
            dry_run=arguments.dry_run,
            limit=arguments.limit,
            batch_size=arguments.batch_size,
        )
    if arguments.workspace_command in {"rerun-latest", "rerun-version"}:
        if not 30 <= int(arguments.timeout_seconds) <= 7200:
            raise JobStoreError(
                "WORKSPACE_RERUN_INVALID", "timeout must be between 30 and 7200 seconds"
            )
        store, _audit, _retention = _build_services(database)
        session = await store.get_session(arguments.session_id)
        if session["workflow_version"] != 2:
            raise JobStoreError(
                "SESSION_WORKFLOW_LEGACY",
                "only reusable sessions support immutable prompt reruns",
            )
        async with database.sessions() as db_session:
            prompt_query = select(PromptVersion.id).where(
                PromptVersion.editing_session_id == arguments.session_id
            )
            if arguments.workspace_command == "rerun-version":
                prompt_query = prompt_query.where(
                    PromptVersion.id == arguments.prompt_version_id
                )
            else:
                prompt_query = prompt_query.order_by(
                    PromptVersion.version_number.desc(), PromptVersion.id.desc()
                ).limit(1)
            prompt_version_id = await db_session.scalar(prompt_query)
        if prompt_version_id is None:
            raise JobStoreError(
                "PROMPT_VERSION_NOT_FOUND",
                (
                    "the prompt version is unavailable for this session"
                    if arguments.workspace_command == "rerun-version"
                    else "the session has no prompt versions"
                ),
            )
        media_root = _default_root().parent / "mvp_sessions"
        prompt_versions = PromptVersionService(
            store,
            SessionMediaStore(media_root, database),
        )
        run = await prompt_versions.rerun(str(prompt_version_id))
        if arguments.wait:
            deadline = asyncio.get_running_loop().time() + int(arguments.timeout_seconds)
            while asyncio.get_running_loop().time() < deadline:
                run = await store.load(run["id"])
                if run.get("state") in {"completed", "failed"}:
                    break
                await asyncio.sleep(2)
            else:
                raise JobStoreError(
                    "PROMPT_RUN_TIMEOUT", "the queued prompt run did not finish in time"
                )
        outcome = run.get("outcome") if isinstance(run.get("outcome"), dict) else {}
        semantic = (
            outcome.get("semantic_qa")
            if isinstance(outcome.get("semantic_qa"), dict)
            else {}
        )
        delivery = (
            outcome.get("delivery")
            if isinstance(outcome.get("delivery"), dict)
            else {}
        )
        limitations = [
            item for item in (outcome.get("limitations") or [])
            if isinstance(item, dict) and item.get("code")
        ]
        return {
            "ok": True,
            "editing_session_id": arguments.session_id,
            "prompt_version_id": str(prompt_version_id),
            "job_id": run["id"],
            "attempt_number": run.get("attempt_number"),
            "state": run.get("state"),
            "stage": run.get("stage"),
            "technical_status": outcome.get("technical_status"),
            "semantic_status": (
                outcome.get("semantic_status") or semantic.get("status")
            ),
            "semantic_provider_calls": semantic.get("provider_calls"),
            "semantic_frame_count": semantic.get("frame_count"),
            "delivery_decision": (
                outcome.get("delivery_decision") or delivery.get("decision")
            ),
            "limitation_codes": (
                outcome.get("limitation_codes")
                or [str(item["code"]) for item in limitations]
            ),
        }
    raise JobStoreError("WORKSPACE_COMMAND_INVALID", "workspace command is invalid")


async def _run(arguments: argparse.Namespace) -> int:
    database: Database | None = None
    try:
        database = Database.from_env()
        if arguments.command == "import-legacy-jobs":
            result = await _import_legacy(arguments, database)
            _format_value(result, "json")
        elif arguments.command == "audit":
            result = await _audit_command(arguments, database)
            _format_value(result, arguments.format)
        elif arguments.command == "retention":
            result = await _retention_command(arguments, database)
            _format_value(result, arguments.format)
        elif arguments.command == "workspace":
            result = await _workspace_command(arguments, database)
            _format_value(result, "json")
        else:
            raise JobStoreError("ADMIN_COMMAND_INVALID", "admin command is invalid")
    except (DatabaseConfigurationError, JobStoreError, OSError) as exc:
        code = (
            exc.code
            if isinstance(exc, JobStoreError)
            else "DATABASE_CONFIG_INVALID"
            if isinstance(exc, DatabaseConfigurationError)
            else "ADMIN_INPUT_FAILED"
        )
        print(json.dumps({"ok": False, "code": code}, sort_keys=True), file=sys.stderr)
        return 1
    finally:
        if database is not None:
            await database.dispose()
    return 0


def _add_format(parser: argparse.ArgumentParser, default: str = "table") -> None:
    parser.add_argument("--format", choices=("table", "json", "ndjson"), default=default)


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenStoryline MVP administration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    importer = subparsers.add_parser(
        "import-legacy-jobs",
        help="import filesystem job snapshots into PostgreSQL",
    )
    importer.add_argument("--root", required=True)
    import_mode = importer.add_mutually_exclusive_group(required=True)
    import_mode.add_argument("--dry-run", action="store_true")
    import_mode.add_argument("--apply", action="store_true")
    importer.add_argument("--batch-size", type=int, default=100)
    importer.add_argument("--session-title", default="Imported legacy jobs")

    audit = subparsers.add_parser("audit", help="query and review persistent video audits")
    audit_commands = audit.add_subparsers(dest="audit_command", required=True)

    audit_list = audit_commands.add_parser("list", help="list bounded audit summaries")
    audit_list.add_argument("--since")
    audit_list.add_argument("--session")
    audit_list.add_argument("--state")
    audit_list.add_argument("--stage")
    audit_list.add_argument("--verdict", choices=("approved", "rejected", "needs_review"))
    audit_list.add_argument("--error-code")
    audit_list.add_argument("--media", choices=("all", "available", "unavailable"), default="all")
    audit_list.add_argument("--hold", choices=("all", "held", "unheld"), default="all")
    audit_list.add_argument("--limit", type=int, default=50)
    audit_list.add_argument("--cursor")
    _add_format(audit_list)

    show = audit_commands.add_parser("show")
    show.add_argument("job_id")
    show.add_argument("--limit", type=int, default=200)
    _add_format(show, default="json")

    outcomes = audit_commands.add_parser(
        "outcomes",
        help="summarize classified outcomes and playable-output SLO evidence",
    )
    outcomes.add_argument("--since")
    outcomes.add_argument("--limit", type=int, default=5000)
    _add_format(outcomes, default="json")

    defects = audit_commands.add_parser(
        "defects",
        help="query bounded defect, repair, fallback, and delivery records",
    )
    defects.add_argument("--since")
    defects.add_argument("--code")
    defects.add_argument("--strategy")
    defects.add_argument("--disposition")
    defects.add_argument("--stage")
    defects.add_argument("--limit", type=int, default=100)
    defects.add_argument("--format", choices=("table", "json"), default="table")

    verify = audit_commands.add_parser("verify")
    verify.add_argument("job_id")
    _add_format(verify, default="json")

    for name in ("events", "documents"):
        command = audit_commands.add_parser(name)
        command.add_argument("job_id")
        command.add_argument("--limit", type=int, default=200)
        _add_format(command, default="json" if name == "events" else "ndjson")

    review = audit_commands.add_parser("review", help="record a review from JSON stdin/file")
    review.add_argument("job_id")
    review.add_argument("--input", required=True, help="JSON file path or - for stdin")
    _add_format(review, default="json")

    backfill = audit_commands.add_parser("backfill", help="ingest legacy JSON/SRT evidence")
    backfill_mode = backfill.add_mutually_exclusive_group(required=True)
    backfill_mode.add_argument("--dry-run", action="store_true")
    backfill_mode.add_argument("--apply", action="store_true")
    backfill.add_argument("--limit", type=int, default=100)
    _add_format(backfill, default="json")

    hold = audit_commands.add_parser("hold", help="set or clear an editing-session audit hold")
    hold.add_argument("session_id")
    hold_mode = hold.add_mutually_exclusive_group(required=True)
    hold_mode.add_argument("--set", action="store_true")
    hold_mode.add_argument("--clear", action="store_true")
    hold.add_argument("--input", help="JSON file path or - with a private reason")
    _add_format(hold, default="json")

    retention = subparsers.add_parser("retention", help="preview and apply bounded retention")
    retention_commands = retention.add_subparsers(
        dest="retention_command",
        required=True,
    )
    retention_status = retention_commands.add_parser("status")
    _add_format(retention_status, default="json")
    for name in ("preview", "run"):
        command = retention_commands.add_parser(name)
        command.add_argument("--limit", type=int, default=100)
        if name == "run":
            command.add_argument(
                "--apply",
                action="store_true",
                help="perform deletion; without this flag the command previews only",
            )
        _add_format(command, default="json")

    workspace = subparsers.add_parser(
        "workspace", help="manage reusable workspace data"
    )
    workspace_commands = workspace.add_subparsers(
        dest="workspace_command",
        required=True,
    )
    workspace_commands.add_parser(
        "inventory",
        help="report aggregate Agentic and historical workflow counts",
    )
    prompt_backfill = workspace_commands.add_parser(
        "backfill-prompts", help="link legacy jobs to immutable prompt versions"
    )
    prompt_backfill_mode = prompt_backfill.add_mutually_exclusive_group(required=True)
    prompt_backfill_mode.add_argument("--dry-run", action="store_true")
    prompt_backfill_mode.add_argument("--apply", action="store_true")
    prompt_backfill.add_argument("--limit", type=int, default=1000)
    prompt_backfill.add_argument("--batch-size", type=int, default=100)

    rerun_latest = workspace_commands.add_parser(
        "rerun-latest",
        help="queue the newest immutable prompt version without exposing its text",
    )
    rerun_latest.add_argument("session_id")
    rerun_latest.add_argument("--wait", action="store_true")
    rerun_latest.add_argument("--timeout-seconds", type=int, default=3600)

    rerun_version = workspace_commands.add_parser(
        "rerun-version",
        help="queue a specific immutable prompt version without exposing its text",
    )
    rerun_version.add_argument("session_id")
    rerun_version.add_argument("prompt_version_id")
    rerun_version.add_argument("--wait", action="store_true")
    rerun_version.add_argument("--timeout-seconds", type=int, default=3600)

    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
