/**
 * Smoke tests for the marketplace_frontend SPA.
 *
 * These tests don't require a running marketplace_backend canister to
 * pass — they only verify that:
 *   - the page renders with the expected layout (nav, headings, footer);
 *   - the kind/metric toggles on Top Charts respond to clicks;
 *   - the upload page correctly gates on Internet Identity sign-in;
 *   - the my-purchases / developer pages also require sign-in.
 *
 * If a backend is reachable the Top Charts grid will populate; if not,
 * the spec still passes because we never assert presence of items.
 *
 * Set PLAYWRIGHT_BASE_URL to the deployed marketplace_frontend URL
 * before running, e.g.:
 *
 *   PLAYWRIGHT_BASE_URL=http://uzt4z-lp777-77774-qaabq-cai.localhost:4943 \
 *     npx playwright test
 */

import { expect, test } from '@playwright/test';

test.describe('marketplace_frontend smoke', () => {
  test('home door has no env/version chips and keeps the catalog', async ({ page }) => {
    await page.goto('/');

    await expect(page).toHaveTitle('Realms GOS - The Governance Operating System');
    await expect(page.getByRole('heading', { name: 'The Governance Operating System' })).toBeVisible();
    await expect(page.locator('.landing-wordmark img[src="/images/logo_horizontal.svg"]')).toBeVisible();
    await expect(page.locator('.landing-bg canvas')).toBeVisible();
    await expect(page.locator('.brand img[src="/images/logo_sphere_only.svg"]')).toHaveCount(1);
    await expect(page.locator('.scroll-cue')).toBeVisible();
    await expect(page.locator('.scroll-flag, .scroll-pennant')).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'Realms GOS' })).toHaveCount(0);
    await expect(page.getByText(/Decentralized governance realms/i)).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'What is it?' })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'What is this?' })).toHaveAttribute('href', '/about');
    await expect(page.getByRole('button', { name: 'Sign in' }).first()).toBeVisible();
    await expect(page.getByText(/Built on the Internet Computer with/i)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Browse marketplace' })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Launch your realm' })).toHaveCount(0);
    await expect(page.getByText(/test environment/i)).toHaveCount(0);
    await expect(page.getByText(/realms gos main/i)).toHaveCount(0);
    await expect(page.locator('.landing-badges, .badge-env, .badge-version')).toHaveCount(0);

    await expect(page.getByRole('link', { name: /^About$/ })).toHaveCount(0);
    await expect(page.locator('section.trust')).toHaveCount(0);
    await expect(page.getByText(/100%\s*on-chain/i)).toHaveCount(0);
    await expect(page.getByRole('heading', { name: /Enhance the experience/i })).toBeVisible();
    await expect(page.getByRole('tab', { name: /Extensions/i })).toBeVisible();
  });

  test('about route recycles Realms GOS landing sections', async ({ page }) => {
    await page.goto('/about');

    await expect(page.getByRole('heading', { name: 'The Governance Operating System' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'What is Realms?' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Core Principles' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'How It Works' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'For People' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'For Institutions' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Get Started' })).toBeVisible();
    // The hero lost its CTA in 108c8a6d and its last demo link (AboutExplainer) in
    // 4f603e71; the demo link now sits under Get Started.
    await expect(page.getByRole('link', { name: /Try the Demo/i })).toBeVisible();
    await expect(page.locator('.about-hero .cta.secondary, .about-hero .mark')).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Request Invitation Code' })).toBeVisible();
    await expect(page.getByText(/test environment/i)).toHaveCount(0);
    await expect(page.getByText(/realms gos main/i)).toHaveCount(0);
    await expect(page.locator('iframe')).toHaveCount(0);
  });

  test('home catalog below the landing hero still toggles', async ({ page }) => {
    await page.goto('/');

    const catalog = page.locator('#catalog');
    await catalog.scrollIntoViewIfNeeded();
    await expect(page.getByRole('link', { name: /Realms Marketplace/i })).toBeVisible();
    await expect(page.getByRole('link', { name: /^About$/ })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: /Enhance the experience/i })).toBeVisible();

    await expect(page.getByRole('tab', { name: /Extensions/i })).toBeVisible();
    await expect(page.getByRole('tab', { name: /Codices/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Most Downloaded/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Most Liked/i })).toBeVisible();

    const verifiedOnly = page.locator('.verified-toggle input[type="checkbox"]');
    await expect(verifiedOnly).toBeChecked();

    await page.getByRole('button', { name: /Most Liked/i }).click();
    await page.getByRole('tab', { name: /Codices/i }).click();

    const footerCard = page.locator('.footer-card');
    await expect(footerCard).toBeVisible();
    // Scoped to the footer: the home page has its own "Built on the Internet Computer" credit.
    await expect(footerCard.getByText(/built on the/i)).toBeVisible();
    await expect(footerCard).toHaveCSS('border-top-width', '0px');
    await expect(footerCard).toHaveCSS('border-left-width', '0px');
  });

  test('extensions page renders search input, sort dropdown, and verified-only filter', async ({ page }) => {
    await page.goto('/extensions');
    await expect(page.getByRole('heading', { name: 'Extensions' })).toBeVisible();
    await expect(page.getByPlaceholder('Search extensions…')).toBeVisible();
    await expect(page.getByText(/Verified only/i)).toBeVisible();
    await expect(page.locator('.verified-toggle input[type="checkbox"]')).toBeChecked();
    // Sort dropdown with the three options the plan calls out.
    const sort = page.locator('select');
    await expect(sort).toBeVisible();
    const optionTexts = await sort.locator('option').allInnerTexts();
    expect(optionTexts).toEqual(expect.arrayContaining(['Newest', 'Most installs', 'Most likes']));
    // Switching sort should not throw.
    await sort.selectOption('installs');
    await sort.selectOption('likes');
  });

  test('codices page renders search input + sort dropdown', async ({ page }) => {
    await page.goto('/codices');
    await expect(page.getByRole('heading', { name: 'Codices' })).toBeVisible();
    await expect(page.getByPlaceholder('Search codices…')).toBeVisible();
    const sort = page.locator('select');
    await expect(sort).toBeVisible();
    const optionTexts = await sort.locator('option').allInnerTexts();
    expect(optionTexts).toEqual(expect.arrayContaining(['Newest', 'Most installs', 'Most likes']));
  });

  test('assistants page renders search input, domain + sort dropdowns', async ({ page }) => {
    await page.goto('/assistants');
    await expect(page.getByRole('heading', { name: 'AI Assistants' })).toBeVisible();
    await expect(page.getByPlaceholder('Search assistants…')).toBeVisible();
    // Domain + sort selects both present.
    const selects = page.locator('select');
    await expect(selects).toHaveCount(2);
  });

  test('home catalog has Assistants kind toggle', async ({ page }) => {
    await page.goto('/');
    await page.locator('#catalog').scrollIntoViewIfNeeded();
    await expect(page.getByRole('tab', { name: /^Assistants$/ })).toBeVisible();
    await page.getByRole('tab', { name: /^Assistants$/ }).click();
  });

  test('upload page gates on sign-in', async ({ page }) => {
    await page.goto('/upload');
    await expect(page.getByRole('heading', { name: 'Upload' })).toBeVisible();
    await expect(page.getByText(/Sign in required/i)).toBeVisible();
    await expect(page.getByText(/Publishing needs an active developer license/i)).toBeVisible();
  });

  test('my-purchases page gates on sign-in', async ({ page }) => {
    await page.goto('/my-purchases');
    await expect(page.getByRole('heading', { name: 'My Purchases' })).toBeVisible();
    await expect(page.getByText(/Sign in to view your purchases/i)).toBeVisible();
  });

  test('developer page gates on sign-in', async ({ page }) => {
    await page.goto('/developer');
    await expect(page.getByRole('heading', { name: 'Developer' })).toBeVisible();
    await expect(page.getByText(/Sign in required/i)).toBeVisible();
  });

  test('extension detail renders Overview/Files tabs (when listing exists)', async ({ page }) => {
    // Browse extensions and follow the first card if any exist; otherwise
    // skip — this makes the test backend-aware without hard-coding ids.
    await page.goto('/extensions');
    // Give the SPA a moment to fetch listings before deciding.
    await page.waitForTimeout(2500);
    const firstLink = page.locator('a.card').first();
    if ((await firstLink.count()) === 0) {
      test.skip(true, 'No extensions listed in this environment');
    }
    await firstLink.click();
    await expect(page.getByRole('tab', { name: 'Overview' })).toBeVisible();
    await expect(page.getByRole('tab', { name: /^Files/ })).toBeVisible();
    await page.getByRole('tab', { name: /^Files/ }).click();
    await expect(page.getByRole('tab', { name: /^Files/ })).toHaveAttribute('aria-selected', 'true');
  });
});
