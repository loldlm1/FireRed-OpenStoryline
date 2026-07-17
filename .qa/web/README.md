# Remote MVP Browser QA

This Playwright harness tests the password/session flow against an already
running local remote-MVP server. Never point it at production or provide a real
production password.

## Install

```bash
npm install
npx playwright install chromium
```

Use `npx playwright install --with-deps chromium` only when the environment is missing system browser dependencies; that command may require sudo. If a run fails with a missing browser executable after `npm install`, install the matching browser revision with `npx playwright install chromium`.

## Run

```bash
BASE_URL=http://127.0.0.1:18000 \
QA_PASSWORD='local test password' \
QA_FAIL_ON_CONSOLE=1 \
npm run test:compact
```

The focused suite covers the fresh login state, generic invalid-password
feedback, cookie/CSRF behavior, browser-storage absence, logout, keyboard
focus, and desktop/mobile layout. `tests/smoke.spec.ts` provides the generic
page-error check.

Multiple paths can be added to the generic smoke test:

```bash
BASE_URL=http://127.0.0.1:18000 QA_PATHS=/,/health npm run test:compact
```

Cross-browser only when needed:

```bash
BASE_URL=http://127.0.0.1:3000 QA_BROWSERS=all npm run test:compact
```

Useful environment variables:

- `BASE_URL`: app URL.
- `QA_PASSWORD`: required local test password configured by the running app.
- `QA_PATHS`: comma-separated paths to smoke test.
- `QA_BROWSERS`: `chromium` or `all`.
- `QA_FAIL_ON_CONSOLE`: set `1` to fail on console errors.
- `QA_TRACE`: default `retain-on-failure`.
- `QA_VIDEO`: set `1` to retain video on failure.
- `QA_WORKERS`: parallel worker count.
