import { test, expect } from '@playwright/test';

const password = process.env.QA_PASSWORD;
if (!password) throw new Error('QA_PASSWORD is required for the local auth smoke test');

test.describe('remote MVP password sessions', () => {
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
      const response = await fetch('/api/mvp/jobs', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRF-Token': decodeURIComponent(csrfCookie || '') },
      });
      return response.status;
    });
    expect(protectedStatus).toBe(422);

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
    await expect(page.locator('#logout')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });
});
