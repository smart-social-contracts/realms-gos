/**
 * The demo notice (CLFO requirement 1): on every page outside production, with Privacy, Terms and Report links;
 * the footer links the legal pages everywhere, production's own on production.
 * It may collapse; it must never disappear for good. The spec runs on localhost, which is not production, except
 * the one test that serves the build under production's host name.
 */

import { expect, test, type Page } from '@playwright/test';

const notice = (page: Page) => page.getByRole('complementary', { name: 'Demo with simulated data.' });
const BODY = /València here is a simulated city/;

test.describe('demo notice', () => {
  for (const path of ['/', '/extensions', '/codices', '/about', '/upload']) {
    test(`is on ${path} with its legal links`, async ({ page }) => {
      await page.goto(path);
      const n = notice(page);
      await expect(n).toBeVisible();
      await expect(n.getByText(BODY)).toBeVisible();
      await expect(n.getByRole('link', { name: 'Privacy' })).toHaveAttribute('href', 'https://demo.gos.earth/privacy');
      await expect(n.getByRole('link', { name: 'Terms' })).toHaveAttribute('href', 'https://demo.gos.earth/terms');
      await expect(n.getByRole('link', { name: 'Report content' })).toHaveAttribute(
        'href',
        'mailto:contact@realmsgos.org'
      );
    });
  }

  test('the footer links Privacy and Terms', async ({ page }) => {
    await page.goto('/extensions');
    const footer = page.locator('.footer-card');
    await expect(footer.getByRole('link', { name: 'Privacy' })).toHaveAttribute('href', 'https://demo.gos.earth/privacy');
    await expect(footer.getByRole('link', { name: 'Terms' })).toHaveAttribute('href', 'https://demo.gos.earth/terms');
  });

  test('collapses, and comes back collapsed after a reload and on the next page', async ({ page }) => {
    await page.goto('/extensions');
    const n = notice(page);
    // Its only control is the collapse toggle: there is nothing that closes it.
    await expect(n.getByRole('button')).toHaveCount(1);

    await n.getByRole('button', { name: 'Hide details' }).click();
    await expect(n.getByText(BODY)).toHaveCount(0);
    await expect(n).toBeVisible();
    await expect(n.getByRole('link', { name: 'Privacy' })).toBeVisible();
    await expect(n.getByRole('link', { name: 'Terms' })).toBeVisible();
    await expect(n.getByRole('link', { name: 'Report content' })).toBeVisible();

    await page.reload();
    await expect(notice(page)).toBeVisible();
    await expect(notice(page).getByRole('button', { name: 'Show details' })).toHaveAttribute('aria-expanded', 'false');

    await page.goto('/codices');
    await expect(notice(page)).toBeVisible();
    await notice(page).getByRole('button', { name: 'Show details' }).click();
    await expect(notice(page).getByText(BODY)).toBeVisible();
  });

  test('no stored value hides it', async ({ page }) => {
    await page.goto('/extensions');
    // Whatever a visitor's storage holds — junk in the collapse key, or the keys with which the realm's and the
    // portal's test-mode banners dismiss themselves for good — the notice still renders.
    await page.evaluate(() => {
      localStorage.setItem('marketplaceDemoNoticeCollapsed', 'dismissed');
      localStorage.setItem('demo_banner_dismissed', 'true');
      localStorage.setItem('gos_test_mode_banner_dismissed', 'true');
    });
    await page.reload();
    await expect(notice(page)).toBeVisible();
    await expect(notice(page).getByRole('link', { name: 'Report content' })).toBeVisible();
  });

  test("is not on production, whose footer links production's legal pages", async ({ page, baseURL }) => {
    // The same build, served under production's host name: the host is all that tells production apart.
    await page.route('http://realmsgos.org/**', async (route) => {
      const url = new URL(route.request().url());
      const response = await route.fetch({ url: new URL(url.pathname + url.search, baseURL).toString() });
      await route.fulfill({ response });
    });
    await page.goto('http://realmsgos.org/extensions');
    const footer = page.locator('.footer-card');
    // The links and the notice are decided in the same onMount, so once the links are there the notice is settled.
    await expect(footer.getByRole('link', { name: 'Privacy' })).toHaveAttribute('href', 'https://gos.earth/privacy');
    await expect(footer.getByRole('link', { name: 'Terms' })).toHaveAttribute('href', 'https://gos.earth/terms');
    await expect(page.locator('.demo-notice')).toHaveCount(0);
  });

  test('is in Spanish for Spanish visitors', async ({ page }) => {
    await page.goto('/extensions?locale=es');
    const n = page.getByRole('complementary', { name: 'Demo con datos simulados.' });
    await expect(n).toBeVisible();
    await expect(n.getByText(/Aquí València es una ciudad simulada/)).toBeVisible();
    await expect(n.getByRole('link', { name: 'Privacidad' })).toBeVisible();
    await expect(n.getByRole('link', { name: 'Condiciones' })).toBeVisible();
    await expect(n.getByRole('link', { name: 'Denunciar contenido' })).toBeVisible();
  });
});
