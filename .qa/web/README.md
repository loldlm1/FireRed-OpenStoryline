# Remote MVP Browser QA

This Playwright harness tests an already running local remote-MVP server. Never
point it at production or provide a real production password.

## Install

```bash
npm install
npx playwright install chromium
```

Use `npx playwright install --with-deps chromium` only when system browser
dependencies are missing; it may require sudo.

## Focused Local Gate

The default local URL is `http://127.0.0.1:8000`, matching
`.env.mvp.example`. `BASE_URL` still overrides it for an intentional custom
port. Run project-native Python tests before Playwright, then select one focused
Chromium command:

```bash
QA_FAIL_ON_CONSOLE=1 npm run test:smoke

QA_PASSWORD='local test password' npm run test:auth:desktop

QA_PASSWORD='local test password' npm run test:auth:mobile
```

All three commands use Chromium, compact reporters, and one worker. Run the
generic smoke plus at most one directly affected auth/layout test by default;
do not run both auth commands when the change does not require them.

Collection-only checks do not require a server:

```bash
npm run test:smoke -- --list
QA_PASSWORD='local-list-only' npm run test:auth:desktop -- --list
QA_PASSWORD='local-list-only' npm run test:auth:mobile -- --list
```

`tests/smoke.spec.ts` checks one or more paths for fatal HTTP, page, console,
and body errors. Add paths without broadening the browser matrix:

```bash
QA_PATHS=/,/health QA_FAIL_ON_CONSOLE=1 npm run test:smoke
```

## Broader Runs

`npm run test:compact` and the Firefox/WebKit scripts remain available for an
explicit broader request. They are not the routine validation path in this
resource-constrained environment. A deliberate cross-browser run can use:

```bash
BASE_URL=http://127.0.0.1:8000 QA_BROWSERS=all npm run test:compact
```

Useful environment variables:

- `BASE_URL`: local app URL; defaults to `http://127.0.0.1:8000`.
- `QA_PASSWORD`: required only by the auth/session spec.
- `QA_PATHS`: comma-separated paths for the generic smoke test.
- `QA_BROWSERS`: `chromium` or an explicitly requested broader selection.
- `QA_FAIL_ON_CONSOLE`: set `1` to fail on console errors.
- `QA_TRACE`: defaults to `retain-on-failure`.
- `QA_VIDEO`: set `1` to retain video on failure.
- `QA_WORKERS`: worker count; defaults to `1`.

Focused runs keep screenshots and traces only on failure; video remains off
unless `QA_VIDEO=1`. Compact JSON/JUnit results stay under
`.qa/web/artifacts/`. Enable heavier HTML/video artifacts only for a targeted
failure, and report artifact paths instead of dumping reports or DOM snapshots.
