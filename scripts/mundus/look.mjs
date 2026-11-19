// look.mjs: open pages of a local mundus in headless Chromium, save a screenshot of each, and print what a person
// would see: the page's visible text, any error banner, failed requests and console errors. `realms mundus check`.
//
//   node scripts/mundus/look.mjs NAME=URL [NAME=URL ...]     screenshots go to $MUNDUS_SHOTS/NAME.png
//
// Playwright is the one realms-gos's e2e pins (REALMS_DIR's node_modules).
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(path.join(process.env.REALMS_DIR, 'package.json'));
const { chromium } = require('@playwright/test');
const shots = process.env.MUNDUS_SHOTS;

const browser = await chromium.launch();
for (const arg of process.argv.slice(2)) {
  const [name, url] = [arg.slice(0, arg.indexOf('=')), arg.slice(arg.indexOf('=') + 1)];
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [], failed = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text().slice(0, 200)));
  page.on('pageerror', (e) => errors.push(`pageerror: ${String(e).slice(0, 200)}`));
  page.on('response', (r) => r.status() >= 400 && failed.push(`${r.status()} ${r.url().slice(0, 140)}`));
  let status = 0;
  try {
    status = (await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 }))?.status() ?? 0;
  } catch (e) {
    errors.push(`goto: ${String(e).slice(0, 200)}`);
  }
  await page.waitForTimeout(4000);
  await page.screenshot({ path: path.join(shots, `${name}.png`), fullPage: false });
  // A realm's pages are extensions, drawn in an iframe: read the frames' text too.
  const texts = [];
  for (const frame of page.frames()) {
    texts.push(await frame.locator('body').innerText().catch(() => ''));
  }
  const text = texts.join(' ').replace(/\s+/g, ' ').trim();
  console.log(JSON.stringify({ name, url: page.url(), status, title: await page.title(), text: text.slice(0, 600),
    console_errors: [...new Set(errors)].slice(0, 6), failed_requests: [...new Set(failed)].slice(0, 6) }, null, 1));
  await page.close();
}
await browser.close();
