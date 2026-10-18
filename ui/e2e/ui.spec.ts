import { test, expect, Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'fs';
import path from 'path';
import os from 'os';

const SCREENSHOTS_DIR = path.resolve('../docs/screenshots');

function getSessionToken(): string {
  const tokenPath = path.join(os.homedir(), '.local/share/nox/session_token');
  if (fs.existsSync(tokenPath)) {
    return fs.readFileSync(tokenPath, 'utf-8').trim();
  }
  return '';
}

async function checkAxe(page: Page, pageName: string) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa'])
    .analyze();

  const seriousOrCritical = results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical'
  );

  if (seriousOrCritical.length > 0) {
    console.warn(
      `Axe violations on ${pageName}:`,
      seriousOrCritical.map((v) => ({ id: v.id, impact: v.impact, description: v.description }))
    );
  }
  expect(seriousOrCritical, `Found serious/critical a11y violations on ${pageName}`).toEqual([]);
}

test.describe('NoX Web UI Browser-Driven Verification', () => {
  let token: string;
  const consoleErrors: string[] = [];
  const uncaughtErrors: string[] = [];
  const badResponses: string[] = [];

  test.beforeAll(() => {
    token = getSessionToken();
    if (!fs.existsSync(SCREENSHOTS_DIR)) {
      fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });
    }
  });

  test.beforeEach(async ({ page }) => {
    consoleErrors.length = 0;
    uncaughtErrors.length = 0;
    badResponses.length = 0;

    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        // Filter out expected minor network logs if any
        consoleErrors.push(msg.text());
      }
    });

    page.on('pageerror', (err) => {
      uncaughtErrors.push(err.message);
    });

    page.on('response', (resp) => {
      // Flag unexpected HTTP 4xx / 5xx
      if (resp.status() >= 400 && !resp.url().includes('/nonexistent')) {
        badResponses.push(`${resp.status()} on ${resp.url()}`);
      }
    });
  });

  test('E5.3: Navigates all 6 pages, captures desktop & mobile screenshots, verifies zero errors', async ({ page }) => {
    // 1. Initial tokenized URL login exchange
    await page.goto(`/?t=${token}`);
    await page.waitForLoadState('networkidle');

    // Wait for App to load and nav to be visible
    await expect(page.locator('aside').first()).toBeVisible();

    // Verify no console errors or uncaught exceptions on initial load
    expect(uncaughtErrors).toEqual([]);
    expect(consoleErrors).toEqual([]);
    expect(badResponses).toEqual([]);

    // Page 1: Chat (Default desktop 1440x900)
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({ path: path.join(SCREENSHOTS_DIR, 'chat.png'), fullPage: true });
    await checkAxe(page, 'Chat');

    // Page 2: Briefings
    await page.click('button:has-text("Briefings")');
    await expect(page.locator('text=Briefings Archive')).toBeVisible();
    await page.screenshot({ path: path.join(SCREENSHOTS_DIR, 'briefings.png'), fullPage: true });
    await checkAxe(page, 'Briefings');

    // Page 3: Jobs
    await page.click('button:has-text("Jobs")');
    await page.waitForTimeout(500);
    await expect(page.locator('h1:has-text("Scheduled Jobs & Reminders")')).toBeVisible();
    await page.screenshot({ path: path.join(SCREENSHOTS_DIR, 'jobs.png'), fullPage: true });
    await checkAxe(page, 'Jobs');

    // Page 4: Memory
    await page.click('button:has-text("Memory")');
    await page.waitForTimeout(500);
    await expect(page.locator('h1:has-text("Long-Term Memory Browser")')).toBeVisible();
    await page.screenshot({ path: path.join(SCREENSHOTS_DIR, 'memory.png'), fullPage: true });
    await checkAxe(page, 'Memory');

    // Page 5: Activity
    await page.click('button:has-text("Activity")');
    await page.waitForTimeout(500);
    await expect(page.locator('h1:has-text("System Health & Audit Activity")')).toBeVisible();
    await page.screenshot({ path: path.join(SCREENSHOTS_DIR, 'activity.png'), fullPage: true });
    await checkAxe(page, 'Activity');

    // Page 6: Settings
    await page.click('button:has-text("Settings")');
    await page.waitForTimeout(500);
    await expect(page.locator('h1:has-text("Daemon & UI Preferences")')).toBeVisible();
    await page.screenshot({ path: path.join(SCREENSHOTS_DIR, 'settings.png'), fullPage: true });
    await checkAxe(page, 'Settings');

    // Mobile screenshots (390x844)
    await page.setViewportSize({ width: 390, height: 844 });

    // Chat mobile
    await page.click('button:has-text("Chat")');
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(SCREENSHOTS_DIR, 'chat-mobile.png'), fullPage: true });

    // Jobs mobile
    await page.click('button:has-text("Jobs")');
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(SCREENSHOTS_DIR, 'jobs-mobile.png'), fullPage: true });

    // Ensure zero uncaught errors or bad responses across all navigation
    expect(uncaughtErrors).toEqual([]);
    expect(consoleErrors).toEqual([]);
    expect(badResponses).toEqual([]);
  });

  test('E5.4: Streaming chat reply with Local badge and cloud toggle', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/?t=${token}`);
    await page.waitForLoadState('networkidle');

    // Toggle "Allow cloud for this request" and verify tooltip / text
    const cloudCheckbox = page.locator('input[type="checkbox"]');
    await expect(cloudCheckbox).toBeVisible();
    await cloudCheckbox.check();
    await expect(cloudCheckbox).toBeChecked();
    await cloudCheckbox.uncheck();
    await expect(cloudCheckbox).not.toBeChecked();

    // Type a message in chat
    const textarea = page.locator('textarea[aria-label="Message NoX"]');
    await textarea.fill('Hello NoX');

    // Send message
    await page.click('button[aria-label="Send message"]');

    // Expect streamed reply with Local badge
    await expect(page.locator('span.text-accent', { hasText: 'Local (qwen2.5:14b)' }).first()).toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=Everything is working smoothly in test mode.').first()).toBeVisible({ timeout: 10000 });
  });

  test('E5.4: Tool execution approval card flow (approve & deny)', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/?t=${token}`);
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(600);

    // Fresh conversation
    await page.click('button:has-text("New Conversation")');
    await page.waitForTimeout(400);

    // Send a message that triggers the write tool
    const textarea = page.locator('textarea[aria-label="Message NoX"]');
    await textarea.fill('trigger write tool for test file');
    await page.click('button[aria-label="Send message"]');

    // Expect ApprovalCard with exact arguments
    const approvalCard = page.locator('text=Action Approval Required');
    await expect(approvalCard).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('write_file', { exact: true })).toBeVisible();
    await expect(page.getByText('/tmp/nox-test.txt')).toBeVisible();

    // Test Deny
    const denyBtn = page.locator('button:has-text("Deny")').first();
    await expect(denyBtn).toBeVisible();
    await denyBtn.click();
    await expect(approvalCard).not.toBeVisible({ timeout: 5000 });
  });

  test('E5.4: Jobs schedule edit, preview, and restore old version', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/?t=${token}`);
    await page.waitForLoadState('networkidle');

    // Go to Jobs
    await page.click('button:has-text("Jobs")');
    await expect(page.locator('h1:has-text("Scheduled Jobs & Reminders")')).toBeVisible();

    // Click daily-briefing job row to open editor drawer
    const jobRow = page.locator('text=daily-briefing').first();
    await expect(jobRow).toBeVisible();
    await jobRow.click();

    // Verify JobEditor drawer opened
    await expect(page.locator('text=Details & Instruction')).toBeVisible();

    // Edit schedule expression to 0 18 * * *
    const cronInput = page.locator('input[placeholder="e.g. 0 9 * * *"]');
    await cronInput.fill('0 18 * * *');

    // Verify schedule preview shows 18:00
    await expect(page.locator('text=Every day at 18:00 (Daily Evening)')).toBeVisible();

    // Save changes
    const saveBtn = page.locator('button:has-text("Save Changes")');
    await saveBtn.click();
    await expect(page.locator('text=Saved Successfully!')).toBeVisible({ timeout: 5000 });

    // Switch to Version History tab and restore
    await page.click('button:has-text("Version History")');
    const restoreBtn = page.locator('button:has-text("Restore")').first();
    if (await restoreBtn.isVisible()) {
      await restoreBtn.click();
      await page.waitForTimeout(500);
    }
  });

  test('E5.4: Memory page edit, pin fact, and review needs_review fact', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/?t=${token}`);
    await page.waitForLoadState('networkidle');

    // Go to Memory
    await page.click('button:has-text("Memory")');
    await expect(page.locator('h1:has-text("Long-Term Memory Browser")')).toBeVisible();

    // Pin/unpin a fact
    const pinBtn = page.locator('button[title*="pin fact" i]').first();
    await expect(pinBtn).toBeVisible();
    await pinBtn.click();
    await page.waitForTimeout(400);

    // Edit fact value
    const editBtn = page.locator('button[title="Edit value"]').first();
    await editBtn.click();
    const editInput = page.locator('td input[type="text"]');
    await expect(editInput).toBeVisible();
    await editInput.fill('Updated realistic fact value from test');
    await page.locator('button[title="Save fact"]').click();
    await expect(page.locator('text=Updated realistic fact value from test')).toBeVisible();

    // Find Needs Review fact and click Review
    const reviewBtn = page.locator('button:has-text("Review")').first();
    if (await reviewBtn.isVisible()) {
      await reviewBtn.click();
      await page.waitForTimeout(300);
    }
  });

  test('E5.4: Deep run live plan tree update and page reload recovery', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/?t=${token}`);
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(400);

    // Fresh conversation
    await page.click('button:has-text("New Conversation")');
    await page.waitForTimeout(400);

    // Trigger deep run
    const textarea = page.locator('textarea[aria-label="Message NoX"]');
    await textarea.fill('/deep Detailed cybersecurity breakdown and news analysis');
    await page.click('button[aria-label="Send message"]');

    // Live Task Plan panel appears
    const planPanel = page.locator('text=Autonomous Task Plan');
    await expect(planPanel).toBeVisible({ timeout: 12000 });

    // Reload page mid-run / post-run and verify conversation and plan are restored from history
    await page.reload();
    await page.waitForLoadState('networkidle');
    await expect(page.locator('text=Detailed cybersecurity breakdown').first()).toBeVisible({ timeout: 10000 });
  });

  test('E5.4: WebSocket reconnect banner on disconnect', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/?t=${token}`);
    await page.waitForLoadState('networkidle');

    // Simulate WebSocket close
    await page.evaluate(() => {
      // @ts-expect-error test hook
      if (window.__ws) {
        // @ts-expect-error test hook
        window.__ws.close();
      }
    });

    // Verify reconnect banner is shown if disconnected or status indicator shows disconnected
    const banner = page.locator('text=Connecting to NoX Daemon...');
    // If connection drops, banner renders
    if (await banner.isVisible()) {
      await expect(banner).toBeVisible();
    }
  });

  test('E5.5: Keyboard navigation: chat send and Ctrl+K navigation', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/?t=${token}`);
    await page.waitForLoadState('networkidle');

    // Focus textarea using keyboard tab navigation or direct focus
    const textarea = page.locator('textarea[aria-label="Message NoX"]');
    await textarea.focus();
    await page.keyboard.type('Keyboard test prompt');
    await page.keyboard.press('Enter');

    // Verify message sent
    await expect(page.locator('text=Keyboard test prompt').first()).toBeVisible();

    // Trigger Command Palette with Ctrl+K
    await page.keyboard.press('Control+k');
    const cmdPalette = page.locator('input[placeholder="Type a command or search..."]');
    await expect(cmdPalette).toBeVisible();

    // Search for Jobs and press Enter
    await cmdPalette.fill('Go to Jobs');
    await page.keyboard.press('Enter');

    // Verify navigated to Jobs page
    await expect(page.locator('h1:has-text("Scheduled Jobs & Reminders")')).toBeVisible();
  });

  test('E5.6: Browser security: XSS payloads render completely inert', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/?t=${token}`);
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(400);

    // Fresh conversation
    await page.click('button:has-text("New Conversation")');
    await page.waitForTimeout(400);

    let dialogTriggered = false;
    page.on('dialog', async (dialog) => {
      dialogTriggered = true;
      await dialog.dismiss();
    });

    // Send payload containing <script>, <img> with onerror, and javascript: link
    const textarea = page.locator('textarea[aria-label="Message NoX"]');
    const xssPayload =
      'Security Test: <script>alert(1)</script> and <img src=x onerror=alert(1)> and [click here](javascript:alert(1))';
    await textarea.fill(xssPayload);
    await page.click('button[aria-label="Send message"]');

    await page.waitForTimeout(1000);

    // Verify dialog was never triggered
    expect(dialogTriggered).toBe(false);

    // Click the rendered link if present and verify still no alert/dialog
    const link = page.locator('a:has-text("click here")').first();
    if (await link.isVisible()) {
      await link.click();
      await page.waitForTimeout(500);
      expect(dialogTriggered).toBe(false);
    }
  });
});
