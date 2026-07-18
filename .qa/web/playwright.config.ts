import { defineConfig, devices } from '@playwright/test';

const compact = process.env.QA_COMPACT !== '0';
const browsersEnv = process.env.QA_BROWSERS || 'chromium';
const workers = Number(process.env.QA_WORKERS || 1);

function projectFor(name: string) {
  const browser = name.trim().toLowerCase();
  switch (browser) {
    case 'chromium':
      return { name: 'chromium', use: { ...devices['Desktop Chrome'] } };
    case 'chrome':
    case 'google-chrome':
      return { name: 'chrome', use: { ...devices['Desktop Chrome'], channel: 'chrome' } };
    case 'msedge':
    case 'edge':
      return { name: 'msedge', use: { ...devices['Desktop Edge'], channel: 'msedge' } };
    case 'firefox':
      return { name: 'firefox', use: { ...devices['Desktop Firefox'] } };
    case 'webkit':
    case 'safari':
      return { name: 'webkit', use: { ...devices['Desktop Safari'] } };
    default:
      throw new Error(`Unsupported QA_BROWSERS entry: ${name}. Use chromium, firefox, webkit, chrome, msedge, or all.`);
  }
}

const allBrowserNames = ['chromium', 'firefox', 'webkit'];
const selectedBrowserNames = browsersEnv.trim().toLowerCase() === 'all'
  ? allBrowserNames
  : browsersEnv.split(',').map((s) => s.trim()).filter(Boolean);

export default defineConfig({
  testDir: './tests',
  timeout: Number(process.env.QA_TIMEOUT || 30_000),
  expect: { timeout: Number(process.env.QA_EXPECT_TIMEOUT || 7_500) },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: Number(process.env.QA_RETRIES || (process.env.CI ? 1 : 0)),
  workers,
  reporter: compact
    ? [
        ['dot'],
        ['json', { outputFile: 'artifacts/results.json' }],
        ['junit', { outputFile: 'artifacts/results.xml' }],
      ]
    : [
        ['list'],
        ['html', { outputFolder: 'artifacts/html-report', open: 'never' }],
      ],
  use: {
    baseURL: process.env.BASE_URL || 'http://127.0.0.1:8000',
    actionTimeout: Number(process.env.QA_ACTION_TIMEOUT || 10_000),
    navigationTimeout: Number(process.env.QA_NAVIGATION_TIMEOUT || 15_000),
    screenshot: 'only-on-failure',
    trace: process.env.QA_TRACE || 'retain-on-failure',
    video: process.env.QA_VIDEO === '1' ? 'retain-on-failure' : 'off',
  },
  outputDir: 'artifacts/test-output',
  projects: selectedBrowserNames.map(projectFor),
});
