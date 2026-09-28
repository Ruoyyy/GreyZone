// Copyright 2026 shopware AG
// SPDX-License-Identifier: MIT

/**
 * End-to-end verification of the in-browser demo against the local Node server.
 *
 * One browser tab, one serial flow (the boot is expensive and both agents share the tab):
 *
 *   1. cold boot → storefront rendered inside the frame, DemoOverlay launcher visible,
 *      both agents ready; boot marks recorded
 *   2. overlay → shopping demo: real chat turn (search → add a variant to the cart), the
 *      same cart on Shopware's own cart page, checkout handoff into the in-browser checkout
 *   3. overlay (administration) → merchant demo: dashboard from Admin MCP, a price change
 *      staged by chat, approved, verified on the storefront; a second one dismissed
 *
 * Screenshots land in e2e/test-results/screenshots/, timings in docs/timings.local.json
 * (both git-ignored). Model-driven steps are skipped when the proxy has no key and
 * DEMO_E2E_NO_CHAT=1 forces the skip.
 */
import { expect, test, type FrameLocator, type Locator, type Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const E2E_DIR = dirname(fileURLToPath(import.meta.url));
const SCREENSHOT_DIR = resolve(E2E_DIR, 'test-results', 'screenshots');
const TIMINGS_FILE = resolve(E2E_DIR, '..', 'docs', 'timings.local.json');

/** Cold boot on an empty cache downloads ~150 MB and seeds MariaDB; warm boots take ~10 s. */
const BOOT_TIMEOUT_MS = 6 * 60_000;
/** One agent turn = several Claude calls through the proxy plus PHP WASM tool calls. */
const TURN_TIMEOUT_MS = 4 * 60_000;
/** Shopware page loads inside PHP WASM (storefront, cart, checkout, admin). */
const SHOP_PAGE_TIMEOUT_MS = 90_000;

const OLIVE_OIL_PRODUCT_NUMBER = 'CA-OIL';
const OLIVE_OIL_NAME = 'Extra Virgin Olive Oil 500 ml';
const APPLIED_PRICE_EUR = '11,90';
const DISMISSED_PRICE_EUR = '12,50';

type Marks = Record<string, number>;

declare global {
  interface Window {
    __demoTimings?: { start: number; marks: Marks };
    __demo?: {
      state: { view: string; shopReady: boolean; hostReady: boolean; timings: Marks; agents: Record<string, string> };
      playground: { sql(statement: string): Promise<{ rows?: Record<string, unknown>[] }> };
      openInFrame(path: string): void;
      setView(view: 'shop' | 'shopping' | 'merchant'): void;
    };
  }
}

const shot = async (page: Page, name: string) => {
  mkdirSync(SCREENSHOT_DIR, { recursive: true });
  await page.screenshot({ path: resolve(SCREENSHOT_DIR, `${name}.png`), fullPage: false });
};

const shopFrame = (page: Page): FrameLocator => page.frameLocator('iframe[title="Shopware (in-browser)"]');

const chatAvailable = async (page: Page): Promise<boolean> => {
  if (process.env.DEMO_E2E_NO_CHAT === '1') return false;
  const status = await page.request.get('/api/anthropic/status');
  if (!status.ok()) return false;
  const body = (await status.json()) as { configured?: boolean };
  return body.configured === true;
};

/** Sends one chat message in the currently visible demo view and waits for the turn to finish. */
async function chatTurn(page: Page, textboxName: string, message: string) {
  const input = page.getByRole('textbox', { name: textboxName });
  await expect(input).toBeVisible({ timeout: 30_000 });
  await input.fill(message);
  await input.press('Enter');
  // The Send button is disabled while a turn streams; wait until it accepted the message …
  await expect(page.getByText(message, { exact: true }).first()).toBeVisible({ timeout: 30_000 });
}

/**
 * Waits until the composer accepts input again ("Working…" placeholder gone), i.e. the turn's
 * state has been saved: the blueprint session store persists a turn's provenance (staged
 * change ids) when the turn ends, so the card's Approve/Dismiss must not race the stream.
 */
async function turnFinished(page: Page, textboxName: string) {
  await expect(page.getByRole('textbox', { name: textboxName })).not.toHaveAttribute('placeholder', 'Working…', {
    timeout: TURN_TIMEOUT_MS,
  });
}

/** Runs an expectation about a model turn; on failure prints the visible transcript of the view. */
async function expectingTurn<T>(view: Locator, run: () => Promise<T>): Promise<T> {
  try {
    return await run();
  } catch (error) {
    console.log('--- transcript of the failed turn ---\n' + (await view.innerText()).slice(-2500) + '\n--- end transcript ---');
    throw error;
  }
}

const readMarks = (page: Page) => page.evaluate(() => ({ ...(window.__demoTimings?.marks ?? {}), ...(window.__demo?.state.timings ?? {}) }));

const productGrossPrice = (page: Page, productNumber: string) =>
  page.evaluate(async (number) => {
    const result = await window.__demo!.playground.sql(
      `SELECT price FROM product WHERE product_number = '${number}'`
    );
    const row = result.rows?.[0];
    if (!row) return null;
    const prices = JSON.parse(String(row.price)) as Record<string, { gross: number }>;
    return Object.values(prices)[0]?.gross ?? null;
  }, productNumber);

test.describe.configure({ mode: 'serial' });

test.describe('Shopware × Claude Commerce Agents — in the browser', () => {
  let page: Page;
  const marks: Record<string, Marks | number | string> = {};

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
  });

  test.afterAll(async () => {
    mkdirSync(dirname(TIMINGS_FILE), { recursive: true });
    writeFileSync(TIMINGS_FILE, JSON.stringify({ recordedAt: new Date().toISOString(), ...marks }, null, 2) + '\n');
    await page?.close();
  });

  test('boots Shopware + both agents; overlay visible in the storefront', async () => {
    test.setTimeout(BOOT_TIMEOUT_MS + 60_000);
    const started = Date.now();
    await page.goto('/');

    // Cross-origin isolation must hold, otherwise MariaDB WASM (SharedArrayBuffer) cannot start.
    expect(await page.evaluate(() => crossOriginIsolated)).toBe(true);

    await expect(page.getByRole('heading', { name: /Booting a complete Shopware/ })).toBeVisible();
    await shot(page, '01-boot-screen');

    // Storefront rendered inside the frame …
    const frame = shopFrame(page);
    await expect(frame.locator('#commerce-agents-demo')).toBeAttached({ timeout: BOOT_TIMEOUT_MS });
    await expect(frame.locator('#commerce-agents-demo-launcher')).toBeVisible({ timeout: SHOP_PAGE_TIMEOUT_MS });

    // … and both agents ready (the view tabs unlock when the host reports them).
    await expect(page.getByRole('tab', { name: 'Shopping assistant' })).toBeEnabled({ timeout: BOOT_TIMEOUT_MS });
    await expect(page.getByRole('tab', { name: 'Merchant portal' })).toBeEnabled({ timeout: 60_000 });
    await expect(frame.locator('#commerce-agents-demo')).toHaveClass(/ca-demo--ready/, { timeout: 60_000 });

    const bootMarks = await readMarks(page);
    marks.boot = bootMarks;
    marks.wallClockToAgentsReadyMs = Date.now() - started;
    marks.serverMode = process.env.DEMO_E2E_MODE === 'static' ? 'static' : 'dev';
    expect(bootMarks['engine-ready']).toBeGreaterThan(0);
    expect(bootMarks['storefront-rendered']).toBeGreaterThan(0);
    expect(bootMarks['agents-ready']).toBeGreaterThan(0);
    console.log(`boot marks (ms since navigation): ${JSON.stringify(bootMarks)}`);

    // Open the overlay panel and capture it.
    await frame.locator('#commerce-agents-demo-launcher').click();
    await expect(frame.locator('#commerce-agents-demo-panel')).toBeVisible();
    await expect(frame.locator('[data-agent="shopping"]')).toHaveText('ready');
    await expect(frame.locator('[data-agent="merchant"]')).toHaveText('ready');
    await shot(page, '02-storefront-overlay');
  });

  test('overlay → shopping demo: search, add a variant, same cart in Shopware, checkout handoff', async () => {
    const frame = shopFrame(page);
    await frame.locator('#commerce-agents-demo-open-shopping').click();

    await expect(page.getByRole('tab', { name: 'Shopping assistant' })).toHaveAttribute('aria-selected', 'true');
    const shoppingView = page.locator('[data-demo-view="shopping"]');
    await expect(shoppingView).toBeVisible();
    // Catalog from the in-browser Store API.
    await expect(shoppingView.getByRole('button', { name: 'Add Variant product to cart', exact: true })).toBeVisible({ timeout: 60_000 });
    const cartToggle = shoppingView.getByRole('button', { name: /^Open cart, \d+ items?$/ });
    await expect(cartToggle).toHaveAccessibleName('Open cart, 0 items');
    await shot(page, '03-shopping-assistant');

    test.skip(!(await chatAvailable(page)), 'no Anthropic key behind /api/anthropic (set ANTHROPIC_API_KEY in the repo .env)');

    // Unambiguous on purpose (size + colour): the agent otherwise asks which colour — correctly.
    await chatTurn(
      page,
      'Message the shopping assistant',
      'Search the shop for "Variant product" and add one of the size M, White variant to my cart. No need to ask back.'
    );
    const turnStart = Date.now();
    await expectingTurn(shoppingView, () => expect(cartToggle).toHaveAccessibleName('Open cart, 1 item', { timeout: TURN_TIMEOUT_MS }));
    marks.shoppingTurnMs = Date.now() - turnStart;
    await turnFinished(page, 'Message the shopping assistant');
    await cartToggle.click();
    await expect(shoppingView.getByRole('heading', { name: 'Cart · 1 item' })).toBeVisible();
    await expect(shoppingView.getByRole('listitem').filter({ hasText: 'Variant product' })).toBeVisible();
    await shot(page, '04-shopping-cart-after-turn');

    // The same cart on Shopware's own cart page (shared context token).
    await page.getByRole('tab', { name: 'Storefront' }).click();
    await page.getByRole('button', { name: 'Cart', exact: true }).click();
    await expect(frame.getByRole('heading', { name: /Shopping cart/ })).toBeVisible({ timeout: SHOP_PAGE_TIMEOUT_MS });
    await expect(frame.locator('.cart-main-header-item-counter')).toHaveText(/1 item/);
    await expect(frame.getByRole('link', { name: 'Variant product' }).first()).toBeVisible();
    await expect(frame.getByText(/Size:\s*M/).first()).toBeVisible();
    await shot(page, '05-shopware-cart-page');

    // Checkout handoff: the agent's one-time link continues in the in-browser Shopware checkout.
    await page.getByRole('tab', { name: 'Shopping assistant' }).click();
    const checkoutLink = shoppingView.locator('a[data-checkout-link]');
    await expect(checkoutLink).toBeVisible();
    await checkoutLink.click();
    await expect(page.getByRole('tab', { name: 'Storefront' })).toHaveAttribute('aria-selected', 'true', { timeout: 30_000 });
    await expect
      .poll(() => page.evaluate(() => document.querySelector<HTMLIFrameElement>('iframe[title="Shopware (in-browser)"]')?.contentWindow?.location.pathname ?? ''), {
        timeout: SHOP_PAGE_TIMEOUT_MS,
      })
      .toMatch(/^\/checkout\//);
    await expect(frame.getByRole('heading', { name: /Shipping information|Complete order|Checkout/ }).first()).toBeVisible({ timeout: SHOP_PAGE_TIMEOUT_MS });
    await shot(page, '06-shopware-checkout-handoff');
  });

  test('overlay (administration) → merchant demo: dashboard, staged price change approved, second one dismissed', async () => {
    // The overlay is in the administration too.
    await page.evaluate(() => {
      window.__demo!.setView('shop');
      window.__demo!.openInFrame('/admin');
    });
    const frame = shopFrame(page);
    await expect(frame.locator('#commerce-agents-demo-open-merchant')).toBeAttached({ timeout: SHOP_PAGE_TIMEOUT_MS });
    await expect(frame.locator('#commerce-agents-demo-launcher')).toBeVisible({ timeout: SHOP_PAGE_TIMEOUT_MS });
    await frame.locator('#commerce-agents-demo-launcher').click();
    await shot(page, '07-admin-overlay');
    await frame.locator('#commerce-agents-demo-open-merchant').click();

    await expect(page.getByRole('tab', { name: 'Merchant portal' })).toHaveAttribute('aria-selected', 'true');
    const merchantView = page.locator('[data-demo-view="merchant"]');
    await expect(merchantView).toBeVisible();
    // Dashboard from Admin MCP (orders, stock, sales).
    await expect(merchantView.getByRole('heading', { name: 'Needs you today' })).toBeVisible({ timeout: 120_000 });
    await expect(merchantView.getByRole('heading', { name: 'Recent orders' })).toBeVisible();
    await expect(merchantView.getByRole('listitem').filter({ hasText: /^\d{5}/ }).first()).toBeVisible();
    await shot(page, '08-merchant-dashboard');

    test.skip(!(await chatAvailable(page)), 'no Anthropic key behind /api/anthropic (set ANTHROPIC_API_KEY in the repo .env)');

    const priceBefore = await productGrossPrice(page, OLIVE_OIL_PRODUCT_NUMBER);
    expect(priceBefore).not.toBeNull();

    // Stage → approve.
    await chatTurn(page, 'Message the merchant assistant', `Change the price of the ${OLIVE_OIL_NAME} to ${APPLIED_PRICE_EUR} EUR.`);
    let turnStart = Date.now();
    const approve = merchantView.getByRole('button', { name: 'Approve' });
    await expectingTurn(merchantView, () => expect(approve).toBeVisible({ timeout: TURN_TIMEOUT_MS }));
    marks.merchantStageTurnMs = Date.now() - turnStart;
    await expect(merchantView.getByText('Awaiting approval')).toBeVisible();
    await expect(merchantView.getByText(/server dry-run OK/)).toBeVisible();
    await turnFinished(page, 'Message the merchant assistant');
    await shot(page, '09-merchant-staged-change');
    await approve.click();
    await expect(merchantView.getByText(/^applied:/)).toBeVisible({ timeout: 60_000 });
    await expect(merchantView.getByText('Approved', { exact: true })).toBeVisible();
    await shot(page, '10-merchant-approved');
    await expect.poll(() => productGrossPrice(page, OLIVE_OIL_PRODUCT_NUMBER), { timeout: 30_000 }).toBe(11.9);

    // Visible on the storefront (a fresh PHP request against the WASM DB).
    await page.evaluate(() => {
      window.__demo!.setView('shop');
      window.__demo!.openInFrame('/search?search=Olive');
    });
    await expect(frame.getByText(OLIVE_OIL_NAME).first()).toBeVisible({ timeout: SHOP_PAGE_TIMEOUT_MS });
    await expect(frame.getByText(`€${APPLIED_PRICE_EUR.replace(',', '.')}`).first()).toBeVisible();
    await shot(page, '11-storefront-new-price');

    // Stage → dismiss: nothing changes.
    await page.getByRole('tab', { name: 'Merchant portal' }).click();
    await chatTurn(page, 'Message the merchant assistant', `Set the price of the ${OLIVE_OIL_NAME} to ${DISMISSED_PRICE_EUR} EUR.`);
    turnStart = Date.now();
    const dismiss = merchantView.getByRole('button', { name: 'Dismiss' });
    await expectingTurn(merchantView, () => expect(dismiss).toBeVisible({ timeout: TURN_TIMEOUT_MS }));
    marks.merchantDismissTurnMs = Date.now() - turnStart;
    await turnFinished(page, 'Message the merchant assistant');
    await dismiss.click();
    await expect(merchantView.getByText('Dismissed', { exact: true })).toBeVisible({ timeout: 30_000 });
    await expect(merchantView.getByText(/Dismissed by/)).toBeVisible();
    expect(await productGrossPrice(page, OLIVE_OIL_PRODUCT_NUMBER)).toBe(11.9);
    await shot(page, '12-merchant-dismissed');
  });
});
