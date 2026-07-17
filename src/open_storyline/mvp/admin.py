from __future__ import annotations

import argparse
import asyncio
import json

from open_storyline.mvp.database import Database
from open_storyline.mvp.jobs import JobStore, JobStoreError


async def _import_legacy(arguments: argparse.Namespace) -> int:
    database = Database.from_env()
    store = JobStore(arguments.root, database)
    try:
        report = await store.import_legacy_jobs(
            arguments.root,
            dry_run=arguments.dry_run,
            batch_size=arguments.batch_size,
            session_title=arguments.session_title,
        )
    except JobStoreError as exc:
        print(json.dumps({"ok": False, "code": exc.code}, sort_keys=True))
        return 1
    finally:
        await database.dispose()
    print(json.dumps({"ok": True, "dry_run": arguments.dry_run, **report}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenStoryline MVP administration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    importer = subparsers.add_parser(
        "import-legacy-jobs",
        help="import filesystem job snapshots into PostgreSQL",
    )
    importer.add_argument("--root", required=True)
    mode = importer.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    importer.add_argument("--batch-size", type=int, default=100)
    importer.add_argument("--session-title", default="Imported legacy jobs")
    arguments = parser.parse_args()
    if arguments.command == "import-legacy-jobs":
        return asyncio.run(_import_legacy(arguments))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
