import { test, expect } from '@playwright/test';

const paths = (process.env.QA_PATHS || '/')
  .split(',')
  .map((path) => path.trim())
  .filter(Boolean);

const failOnConsole = process.env.QA_FAIL_ON_CONSOLE === '1';
const forbiddenText = new RegExp(
  process.env.QA_FORBIDDEN_TEXT ||
    'Application error|Internal Server Error|Stack trace|Unhandled Runtime Error|Cannot GET',
  'i',
);

test.describe('generic web smoke', () => {
  for (const path of paths) {
    test(`${path} renders without fatal browser errors`, async ({ page }) => {
      const pageErrors: string[] = [];
      const consoleErrors: string[] = [];

      page.on('pageerror', (error) => pageErrors.push(error.message.slice(0, 500)));
      page.on('console', (message) => {
        if (message.type() === 'error') consoleErrors.push(message.text().slice(0, 500));
      });

      const response = await page.goto(path, { waitUntil: 'domcontentloaded' });
      expect(response, `No response returned for ${path}`).toBeTruthy();
      expect(response!.status(), `HTTP status for ${path}`).toBeLessThan(500);

      await expect(page.locator('body')).toBeVisible();
      await expect(page.locator('body')).not.toHaveText(forbiddenText);

      expect(pageErrors, `Browser page errors for ${path}`).toEqual([]);
      if (failOnConsole) {
        expect(consoleErrors, `Console errors for ${path}`).toEqual([]);
      }
    });
  }
});
