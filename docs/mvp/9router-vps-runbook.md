# 9Router VPS Runbook

This runbook applies to the personal QA VPS at `82.39.186.26`. It keeps
provider credentials inside 9Router and documents the existing manual gateway
process. During an active Codex inference session, do not restart 9Router,
change its `admin` user, change port `20128`, replace its launch command, or
change its UFW exposure.

## Existing Service

The current process is launched manually as `admin` with the installed Node
path and `9router -n -l`. Inspect it without restarting:

```bash
ps -eo user=,pid=,ppid=,args= | grep '[9]router'
ss -ltnp | grep ':20128'
curl -fsS http://127.0.0.1:20128/api/health
```

Any future systemd or supervisor migration requires a separate maintenance
window. Never add provider keys, bearer tokens, prompts, or transcripts to
process arguments or captured logs.

## Database Backup And Restore

Backups are root-only under `/var/backups/9router/`. Create a consistent SQLite
backup before changing permissions, packages, or service behavior. Keep the
WAL/SHM files with the source backup when diagnosing an incident.

```bash
install -d -o root -g root -m 700 /var/backups/9router
stat -c '%U:%G %a %n' /home/admin/.9router/db/data.sqlite
```

Restore testing must use a disposable copy and a read-only integrity check. Do
not replace the live database during a normal QA run:

```bash
python3 -c 'import sqlite3; c=sqlite3.connect("file:/path/to/copy.sqlite?mode=ro", uri=True); print(c.execute("PRAGMA integrity_check").fetchone()[0])'
```

The expected integrity result is `ok`. A live restore requires stopping 9Router,
preserving the current directory, and recording the rollback point first.

## Access Paths

The application container can use `http://host.docker.internal:20128` with the
host-gateway mapping from `config/deploy.yml`. Do not change the live route
during inference. The host and an operator laptop can use an SSH tunnel when
needed:

```bash
ssh -N -L 32028:127.0.0.1:20128 root@82.39.186.26
NINEROUTER_URL=http://127.0.0.1:32028 python scripts/qa_ninerouter.py
```

The `/v1` endpoints still require `NINEROUTER_KEY`. The dashboard/API port is
not intended to be a public unauthenticated service.

## Firewall And Health

Review before and after UFW changes:

```bash
ufw status numbered
ss -ltnp | grep ':20128'
curl -fsS http://127.0.0.1:20128/api/health
```

The current public HTTP workflow and UFW rules are intentionally preserved for
the active inference session. Keep the UFW snapshot with the evidence; any
future restriction requires an explicit maintenance window and a tested
replacement access path.

## Incident Rollback

1. Stop accepting new QA jobs and record the current process/image state.
2. Do not kill or restart the live process during an active Codex session.
3. Preserve the root-only backup and any failure evidence.
4. Restore a database copy only during a separate approved maintenance window
   after checking compatibility.
5. Verify `/api/health`, authenticated `/v1/models`, and log redaction.
