import { test, expect } from '@playwright/test';

const password = process.env.QA_PASSWORD;
if (!password) throw new Error('QA_PASSWORD is required for the local auth smoke test');

async function createSession(page, title: string) {
  await page.locator('#session-new').click();
  await expect(page.locator('#session-dialog')).toBeVisible();
  await expect(page.locator('#session-title')).toBeFocused();
  await page.locator('#session-title').fill(title);
  await page.locator('#session-submit').click();
  await expect(page.locator('#session-dialog')).toBeHidden();
  await expect(page.locator('#session-select')).toHaveValue(/[a-f0-9]{32}/);
  await expect(page).toHaveURL(/\?session=[a-f0-9]{32}/);
}

test.describe('remote MVP password sessions', () => {
  test.describe.configure({ mode: 'serial' });

  test('desktop login, CSRF request, reusable session, delete, and logout', async ({ page, context }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/');

    await expect(page.locator('#login-view')).toBeVisible();
    await expect(page.locator('#app-view')).toBeHidden();
    await expect(page.locator('#password')).toBeFocused();
    await expect(page.locator('input[name="token"], #mvp-token')).toHaveCount(0);

    await page.locator('#password').fill('wrong password');
    await page.locator('#password').press('Enter');
    await expect(page.locator('#login-error')).toHaveText('La contraseña no es válida.');
    await expect(page.locator('#password')).toHaveAttribute('aria-describedby', 'login-error');

    await page.locator('#password').fill(password);
    await page.locator('#password').press('Enter');
    await expect(page.locator('#app-view')).toBeVisible();
    await expect(page.locator('#login-view')).toBeHidden();

    const sessionTitle = `Browser workspace ${Date.now()}`;
    await createSession(page, sessionTitle);
    await expect(page.locator('#workspace-title')).toHaveText(sessionTitle);
    await expect(page.locator('#source-state')).toHaveText('Pendiente');
    await expect(page.locator('#submit')).toBeDisabled();
    await expect(page.locator('#video')).toBeFocused();
    await page.locator('.settings-disclosure summary').click();
    await expect(page.locator('#edit-mode')).toHaveValue('agentic');
    await expect(page.locator('#asset-policy')).toBeEnabled();
    await expect(page.locator('#max-generated-assets')).toBeEnabled();
    await expect(page.locator('#stock-policy')).toBeEnabled();
    await expect(page.locator('#max-stock-assets')).toBeDisabled();
    await page.locator('#stock-policy').selectOption('auto');
    await expect(page.locator('#max-stock-assets')).toBeEnabled();
    await page.locator('#edit-mode').selectOption('legacy');
    await expect(page.locator('#asset-policy')).toBeDisabled();
    await expect(page.locator('#stock-policy')).toBeDisabled();
    await page.locator('#edit-mode').selectOption('agentic');

    const cookies = await context.cookies();
    const sessionCookie = cookies.find((cookie) => cookie.name === 'openstoryline_session');
    const csrf = cookies.find((cookie) => cookie.name === 'openstoryline_csrf');
    expect(sessionCookie).toMatchObject({ httpOnly: true, sameSite: 'Lax' });
    expect(csrf).toMatchObject({ httpOnly: false, sameSite: 'Strict' });

    expect(await page.evaluate(() => ({
      local: Object.keys(localStorage),
      session: Object.keys(sessionStorage),
    }))).toEqual({ local: [], session: [] });

    const selectedSession = await page.locator('#session-select').inputValue();
    const protectedStatus = await page.evaluate(async (sessionId) => {
      const csrfCookie = document.cookie
        .split('; ')
        .find((item) => item.startsWith('openstoryline_csrf='))
        ?.split('=', 2)[1];
      const response = await fetch(`/api/mvp/sessions/${sessionId}/prompt-versions`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': decodeURIComponent(csrfCookie || ''),
        },
        body: JSON.stringify({ prompt: '' }),
      });
      return response.status;
    }, selectedSession);
    expect(protectedStatus).toBe(422);

    await page.reload();
    await expect(page.locator('#app-view')).toBeVisible();
    await expect(page.locator('#session-select')).toHaveValue(selectedSession);
    await expect(page.locator('#workspace-title')).toHaveText(sessionTitle);

    await expect(page.locator('#session-delete')).toBeEnabled();
    await page.locator('#session-delete').click();
    await expect(page.locator('#session-delete-dialog')).toBeVisible();
    await expect(page.locator('#session-delete-description')).toContainText(
      'El video fuente y todos sus resultados se eliminarán permanentemente',
    );
    await expect(page.locator('#session-delete-confirm')).toBeFocused();
    await page.locator('#session-delete-confirm').click();
    await expect(page.locator('#session-delete-dialog')).toBeHidden();
    await expect(
      page.locator('#session-select option', { hasText: sessionTitle }),
    ).toHaveCount(0);
    const deletedSessionResponse = await page.request.get(`/api/mvp/sessions/${selectedSession}`);
    expect(deletedSessionResponse.status()).toBe(404);

    await page.locator('#logout').click();
    await expect(page.locator('#login-view')).toBeVisible();
    await expect(page.locator('#password')).toBeFocused();
    const protectedResponse = await page.request.get('/api/mvp/jobs/invalid');
    expect(protectedResponse.status()).toBe(401);
  });

  test('mobile workspace remains usable without horizontal overflow', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');

    await expect(page.locator('#login-view')).toBeVisible();
    const loginButton = await page.locator('#login-submit').boundingBox();
    expect(loginButton).toBeTruthy();
    expect(loginButton!.height).toBeGreaterThanOrEqual(44);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

    await page.locator('#password').fill(password);
    await page.locator('#password').press('Enter');
    await expect(page.locator('#app-view')).toBeVisible();
    await expect(page.locator('#session-new')).toBeVisible();
    await createSession(page, `Mobile workspace ${Date.now()}`);
    await expect(page.locator('#source-card')).toBeVisible();
    await expect(page.locator('#job-form')).toBeVisible();
    await expect(page.locator('#submit')).toBeDisabled();
    await expect(page.locator('#logout')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });
});
