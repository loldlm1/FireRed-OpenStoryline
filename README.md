# FireRed-OpenStoryline

[简体中文](README_zh.md)

FireRed-OpenStoryline is a remote, conversational video-editing service for
turning one uploaded source video into validated social clips. The model plans
and reviews editorial choices; deterministic Python and FFmpeg code validates
inputs, enforces capabilities, renders outputs, and protects promotion.

The deprecated full-local CLI/MCP application has been removed. The supported
runtime is the password-authenticated FastAPI MVP in `mvp_fastapi.py`, deployed
with `Dockerfile.remote` and Kamal.

## Runtime

- 9Router provides planning, frame understanding, and generated images.
- Direct Mistral Voxtral provides timestamped speech-to-text.
- PostgreSQL is authoritative for sessions, jobs, events, artifacts, reviews,
  retention state, and audit evidence.
- CPU FFmpeg performs bounded deterministic media processing.
- The optional pinned FFMPEGA sidecar executes only typed, allowlisted effects.
- Job media and work files remain isolated under `outputs/mvp_jobs/<job_id>`.

The product currently retains a workspace compatibility path while the
Agentic-only migration is completed. It is part of the remote MVP, not the
removed local application.

## Documentation

- [Architecture](docs/mvp/architecture.md)
- [Spanish operator guide](docs/mvp/guia-es.md)
- [API keys and provider checks](docs/mvp/api-keys.md)
- [9Router VPS runbook](docs/mvp/9router-vps-runbook.md)
- [Audit and database operations](docs/mvp/audit-and-database.md)
- [Agent engineering guide](docs/agent-engineering.md)
- [Implementation history](docs/mvp/implementation-history.md)

## Local Development

Requirements: Python 3.11+, PostgreSQL, FFmpeg, and FFprobe.

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements-remote.txt
```

Copy the committed environment templates into private local configuration and
provide the required database, password, security, 9Router, and Mistral values.
Do not commit resolved secrets.

```bash
PYTHONPATH=src .venv/bin/python -m alembic upgrade head
PYTHONPATH=src .venv/bin/uvicorn mvp_fastapi:app --host 127.0.0.1 --port 8000
```

The browser service is available at `http://127.0.0.1:8000`. Health endpoints
are public at `/health` and `/up`; routes under `/api/mvp` require the browser
session and CSRF contract described in the architecture guide.

## Verification

Run the deterministic suite without live provider calls:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=src .venv/bin/python -c "import mvp_fastapi; import open_storyline.mvp.pipeline; print('mvp_only_ok')"
bash -n bin/kamal-mvp scripts/mvp-postgres-init.sh \
  scripts/mvp-postgres-backup.sh scripts/mvp-postgres-restore-check.sh \
  .kamal/hooks/pre-deploy .kamal/hooks/post-deploy
docker build -f Dockerfile.remote .
```

Database-backed test classes skip when `TEST_DATABASE_URL` is unset. Live
provider checks and deployments can incur cost or mutate external state; run
them only as explicit release operations.

## Deployment

Production uses `config/deploy.yml`, `Dockerfile.remote`, and `bin/kamal-mvp`.
The Kamal workflow validates provider capabilities, database readiness,
persistent volumes, health checks, and rollback boundaries. Review
[the architecture](docs/mvp/architecture.md) and
[audit/database guide](docs/mvp/audit-and-database.md) before a release.

## License

Apache License 2.0. See [LICENSE](LICENSE).
