// Numbered screenshots in the test's output folder, also attached to the HTML report.
import { test, type Page, type TestInfo } from '@playwright/test';

const taken = new WeakMap<TestInfo, number>();

// The realm scrolls inside the portal's iframe, so the viewport grows to its content for the shot.
export async function snap(page: Page, label: string) {
	const info = test.info();
	const number = (taken.get(info) ?? 0) + 1;
	taken.set(info, number);
	const name = `${String(number).padStart(2, '0')}-${label}.png`;
	const viewport = page.viewportSize()!;
	const heights = await Promise.all(
		page
			.frames()
			.map((f) =>
				f
					.evaluate(() =>
						Math.max(...[...document.querySelectorAll('*')].map((e) => e.scrollHeight))
					)
					.catch(() => 0)
			)
	);
	const height = Math.min(Math.max(viewport.height, ...heights), 8000);
	await page.setViewportSize({ width: viewport.width, height });
	await page.waitForTimeout(300);
	const body = await page.screenshot({ path: info.outputPath(name), fullPage: true });
	await page.setViewportSize(viewport);
	await info.attach(name, { body, contentType: 'image/png' });
}
