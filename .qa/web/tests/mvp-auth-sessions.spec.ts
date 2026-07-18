import { test, expect } from '@playwright/test';

const password = process.env.QA_PASSWORD;
if (!password) throw new Error('QA_PASSWORD is required for the local auth smoke test');

test.describe('remote MVP password sessions', () => {
  test.describe.configure({ mode: 'serial' });

  test('desktop login, CSRF request, and logout', async ({ page, context }) => {
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
    await expect(page.locator('#edit-mode')).toHaveValue('legacy');
    await expect(page.locator('#asset-policy')).toBeDisabled();
    await expect(page.locator('#asset-policy-help')).toContainText(
      'No se genera una imagen por defecto',
    );
    await page.locator('#edit-mode').selectOption('agentic');
    await expect(page.locator('#asset-policy')).toBeEnabled();
    await expect(page.locator('#max-generated-assets')).toBeEnabled();
    await page.locator('#asset-policy').selectOption('off');
    await expect(page.locator('#max-generated-assets')).toBeDisabled();
    await page.locator('#edit-mode').selectOption('legacy');

    const sessionTitle = `Browser session ${Date.now()}`;
    await page.locator('#session-title').fill(sessionTitle);
    await page.locator('#session-submit').click();
    await expect(page.locator('#session-select')).toHaveValue(/[a-f0-9]{32}/);
    await expect(page.locator('#session-summary')).toContainText(sessionTitle);
    await expect(page.locator('#video')).toBeFocused();

    const cookies = await context.cookies();
    const session = cookies.find((cookie) => cookie.name === 'openstoryline_session');
    const csrf = cookies.find((cookie) => cookie.name === 'openstoryline_csrf');
    expect(session).toMatchObject({ httpOnly: true, sameSite: 'Lax' });
    expect(csrf).toMatchObject({ httpOnly: false, sameSite: 'Strict' });

    const browserStorage = await page.evaluate(() => ({
      local: Object.keys(localStorage),
      session: Object.keys(sessionStorage),
    }));
    expect(browserStorage).toEqual({ local: [], session: [] });

    const protectedStatus = await page.evaluate(async () => {
      const csrfCookie = document.cookie
        .split('; ')
        .find((item) => item.startsWith('openstoryline_csrf='))
        ?.split('=', 2)[1];
      const sessionId = new URL(window.location.href).searchParams.get('session');
      const response = await fetch(`/api/mvp/sessions/${sessionId}/jobs`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRF-Token': decodeURIComponent(csrfCookie || '') },
      });
      return response.status;
    });
    expect(protectedStatus).toBe(422);

    for (const name of ['first.mp4', 'second.mp4']) {
      await page.locator('#video').setInputFiles({
        name,
        mimeType: 'video/mp4',
        buffer: Buffer.from('synthetic-video'),
      });
      await page.locator('#prompt').fill(`Create one short from ${name}`);
      await page.locator('#submit').click();
      await expect(page.locator('#recent-jobs .recent-job')).toHaveCount(
        name === 'first.mp4' ? 1 : 2,
      );
      await expect(page.locator('#submit')).toBeEnabled({ timeout: 15_000 });
    }

    const selectedSession = await page.locator('#session-select').inputValue();
    await page.reload();
    await expect(page.locator('#app-view')).toBeVisible();
    await expect(page.locator('#session-select')).toHaveValue(selectedSession);
    await expect(page.locator('#recent-jobs .recent-job')).toHaveCount(2);

    const deletedJobId = await page.evaluate(async (sessionId) => {
      const response = await fetch(`/api/mvp/sessions/${sessionId}`, {
        credentials: 'same-origin',
      });
      const session = await response.json();
      return session.jobs[0].id as string;
    }, selectedSession);
    await expect(page.locator('#session-delete')).toBeEnabled();
    await page.locator('#session-delete').click();
    await expect(page.locator('#session-delete-dialog')).toBeVisible();
    await expect(page.locator('#session-delete-description')).toContainText(
      'Sus videos se eliminarán permanentemente ahora',
    );
    await expect(page.locator('#session-delete-confirm')).toBeFocused();
    await page.locator('#session-delete-confirm').click();
    await expect(page.locator('#session-delete-dialog')).toBeHidden();
    await expect(page.locator('#session-notice')).toContainText(
      'La auditoría se conserva hasta',
    );
    await expect(page.locator('#status')).toContainText('Sus videos ya no están disponibles');
    await expect(
      page.locator('#session-select option', { hasText: sessionTitle }),
    ).toHaveCount(0);
    const deletedJobResponse = await page.request.get(`/api/mvp/jobs/${deletedJobId}`);
    expect(deletedJobResponse.status()).toBe(404);

    await page.locator('#logout').click();
    await expect(page.locator('#login-view')).toBeVisible();
    await expect(page.locator('#password')).toBeFocused();
    const protectedResponse = await page.request.get('/api/mvp/jobs/invalid');
    expect(protectedResponse.status()).toBe(401);
  });

  test('mobile login remains usable without horizontal overflow', async ({ page }) => {
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
    await expect(page.locator('#job-form')).toBeVisible();
    await expect(page.locator('#edit-mode')).toBeVisible();
    await expect(page.locator('#asset-policy-help')).toBeVisible();
    await expect(page.locator('#asset-policy')).toBeDisabled();
    await page.locator('#edit-mode').selectOption('agentic');
    await expect(page.locator('#asset-policy')).toBeEnabled();
    await expect(page.locator('#max-generated-assets')).toBeEnabled();
    await page.locator('#asset-policy').selectOption('off');
    await expect(page.locator('#max-generated-assets')).toBeDisabled();
    await expect(page.locator('#logout')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });
});
