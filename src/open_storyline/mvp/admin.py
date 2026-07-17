from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import asyncio
import json
import sys

from open_storyline.config import default_config_path, load_settings
from open_storyline.mvp.audit import AuditService, parse_since
from open_storyline.mvp.database import Database, DatabaseConfigurationError
from open_storyline.mvp.jobs import JobStore, JobStoreError


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
            print("No matching jobs.")
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
    config = load_settings(default_config_path())
    return Path(config.project.outputs_dir) / "mvp_jobs"


def _build_services(database: Database, root: str | Path | None = None) -> tuple[JobStore, AuditService]:
    store = JobStore(root or _default_root(), database)
    audit = AuditService(store)
    store.attach_audit(audit)
    return store, audit


async def _import_legacy(arguments: argparse.Namespace, database: Database) -> dict[str, Any]:
    store, _audit = _build_services(database, arguments.root)
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
    store, audit = _build_services(database)
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
    if arguments.audit_command == "events":
        return await store.events(arguments.job_id, limit=arguments.limit)
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
    raise JobStoreError("AUDIT_COMMAND_INVALID", "audit command is invalid")


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

    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
