import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import os from 'os';

const PROOFS_DIR = path.resolve(__dirname, '../../docs/proofs');

function getSessionToken(): string {
  const tokenPath = path.join(os.homedir(), '.local/share/nox/session_token');
  if (fs.existsSync(tokenPath)) {
    return fs.readFileSync(tokenPath, 'utf-8').trim();
  }
  return '';
}

test.describe('LIVE PROOF C: Single Run Briefing Button, Mid-Run Reload & Re-attach', () => {
  let token: string;
  const submittedRunRequests: string[] = [];

  test.beforeAll(() => {
    token = getSessionToken();
    if (!fs.existsSync(PROOFS_DIR)) {
      fs.mkdirSync(PROOFS_DIR, { recursive: true });
    }
  });

  test('Single Run briefing button exhibits progress, re-attaches on reload, and completes cleanly', async ({ page }) => {
    // Track requests to /api/briefings/run to ensure only 1 task is triggered
    page.on('request', (req) => {
      if (req.url().includes('/api/briefings/run') && req.method() === 'POST') {
        submittedRunRequests.push(req.url());
      }
    });

    // 1. Navigate to briefings page
    await page.goto(`http://127.0.0.1:7777/?t=${token}`);
    await page.click('button:has-text("Briefings")');
    await expect(page.locator('text=Briefings Archive').first()).toBeVisible({ timeout: 10000 });

    const runBtn = page.locator('button[data-testid="run-briefing-button"]').first();
    await expect(runBtn).toBeVisible({ timeout: 10000 });

    // If a run is already active from earlier, wait for it to clear first
    if (await runBtn.isDisabled()) {
      await expect(runBtn).toBeEnabled({ timeout: 45000 });
    }

    // Capture initial idle state screenshot
    await page.screenshot({ path: path.join(PROOFS_DIR, 'proof_c_1_idle.png'), fullPage: false });
    await expect(runBtn).toHaveText(/Run briefing/);
    expect(await runBtn.isDisabled()).toBe(false);

    // 2. Click "Run briefing"
    submittedRunRequests.length = 0;
    await runBtn.click();

    // 3. Assert button transitions to running state: disabled, spinner visible, active topic/stage, elapsed time
    await expect(runBtn).toBeDisabled({ timeout: 5000 });
    await expect(runBtn.locator('svg.animate-spin')).toBeVisible();

    // Give it 2 seconds to accumulate elapsed time and stage name
    await page.waitForTimeout(2000);
    const runningText = await runBtn.innerText();
    expect(runningText).toMatch(/\(\d+s\)/); // e.g. "Topic: ... (2s)"

    // Capture running state screenshot
    await page.screenshot({ path: path.join(PROOFS_DIR, 'proof_c_2_running.png'), fullPage: false });

    // 4. Navigate away to Chat and back, then reload mid-run
    await page.click('button:has-text("Chat")');
    await page.waitForTimeout(500);
    await page.click('button:has-text("Briefings")');

    const reattachedBtn = page.locator('button[data-testid="run-briefing-button"]').first();
    await expect(reattachedBtn).toBeVisible({ timeout: 5000 });

    // Assert UI re-attached: button is still disabled and shows spinner or active stage
    await expect(reattachedBtn).toBeDisabled({ timeout: 5000 });
    await expect(reattachedBtn.locator('svg.animate-spin')).toBeVisible();
    await page.screenshot({ path: path.join(PROOFS_DIR, 'proof_c_3_reattached.png'), fullPage: false });

    // Also test reload mid-run
    await page.reload();
    await page.waitForLoadState('domcontentloaded');
    await page.click('button:has-text("Briefings")');
    await expect(reattachedBtn).toBeVisible({ timeout: 5000 });
    await expect(reattachedBtn).toBeDisabled({ timeout: 5000 });

    // Assert only 1 run request was ever sent
    expect(submittedRunRequests.length).toBe(1);

    // 5. Wait for run to complete cleanly (button re-enables with "Run briefing")
    await expect(reattachedBtn).toBeEnabled({ timeout: 50000 });
    await expect(reattachedBtn).toHaveText(/Run briefing/);
    await expect(reattachedBtn.locator('svg.animate-spin')).not.toBeVisible();

    // Capture completion screenshot
    await page.screenshot({ path: path.join(PROOFS_DIR, 'proof_c_4_completed.png'), fullPage: false });

    // Assert archive list or current briefing has items
    await expect(page.locator('text=Briefings Archive').first()).toBeVisible();
  });
});
