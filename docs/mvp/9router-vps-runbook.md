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

## Provider Ownership

| Layer | Model | Connection |
| --- | --- | --- |
| Text and vision | `cx/gpt-5.6-sol` | Codex OAuth |
| Image generation | `cx/gpt-5.5-image` | Codex OAuth |
| Speech-to-text | `mistral/voxtral-mini-2602` | Mistral API key |

There are no fallbacks. FireRed stores only the 9Router endpoint URL/key.
Provider access/refresh tokens and the Mistral key remain in 9Router.

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

Run the strict redacted gate from the repository:

```bash
set -a
source .env.kamal
set +a
python scripts/qa_ninerouter.py --strict-models
```

Use `--live-inference --stt-audio /tmp/non-private-speech.wav --timeout 240`
only for an authorized synthetic canary. The command keeps image bytes in
memory and reports no raw model response.

For the container route, set `NINEROUTER_PROBE_IMAGE` to an image already on
the VPS that contains `curl` or `wget`, then add `--container-host-probe`.
The script uses `docker run --rm --pull=never`; it does not pull an image,
restart 9Router, or change networking.

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

## Incident Triage

- `auth`: verify the FireRed endpoint key reference and the router's endpoint
  key policy. Rotate the endpoint key only in a planned window, update the
  Kamal secret source, and rerun missing/invalid/valid auth probes.
- `catalog_mismatch`: do not substitute another model. For Codex, verify the
  exact text/image catalog and reconnect OAuth interactively only if the
  selected connection has expired. For Mistral, confirm the key record is
  active and that the pinned STT adapter is installed.
- `rate_limited`: stop new QA jobs and wait for the provider reset. Mistral
  Free-mode capacity is not an SLA and a dash in the monthly field is not
  treated as unlimited usage.
- `contract_invalid`: preserve only the summary JSON. Text/vision must return a
  JSON object, image must decode as PNG/JPEG/WebP, and STT must contain
  non-empty finite segments with `end > start`.
- `transport`: compare host health, authenticated catalogs, SSH, remote Docker,
  and the disposable container route. Do not change port `20128`, UFW, the
  `admin` owner, or the manual launch command during active inference.

The current `0.5.35` package has no Mistral STT registry entry. The offline
patch is pinned to upstream commit `bc252ea80298d4879dc6b3c69585af1610d2c76f`
under `patches/9router/`. Applying it requires a separate maintenance window,
a source build, the root-only database backup, a recorded original package,
and an approved restart. Never hand-edit the compiled global package.

Because the service currently writes to its launch terminal, inspect only
sanitized recent terminal output while it is running. If a future maintenance
window introduces systemd, use `journalctl` with output redaction and preserve
the manual launch command as the rollback until the new supervisor is proven.

## Incident Rollback

1. Stop accepting new QA jobs and record the current process/image state.
2. Do not kill or restart the live process during an active Codex session.
3. Preserve the root-only backup and any failure evidence.
4. Restore a database copy only during a separate approved maintenance window
   after checking compatibility.
5. Verify `/api/health`, authenticated `/v1/models`, and log redaction.
