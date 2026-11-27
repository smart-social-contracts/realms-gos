/**
 * The demo notice (CLFO requirement 1): on every page outside production, with Privacy, Terms and Report links.
 * It may collapse; it must never disappear for good. The spec runs on localhost, which is not production.
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
