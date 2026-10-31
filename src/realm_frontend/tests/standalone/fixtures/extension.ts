// An installed extension's page, opened as a member in a portal host frame.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, type FrameLocator, type Locator, type Page } from '@playwright/test';
import type { Ed25519KeyIdentity } from '@dfinity/identity';
import { exposeSigner, openInPortal, reloadIfInterrupted, type StandaloneRealm } from './realm';

/** Where the page's controls are: the extension's mount point, or the iframe a sandboxed extension runs in. The
 * realm's own dialogs are not in it: they are in the realm's frame. */
export type ExtensionPage = Locator | FrameLocator;

// The Playwright tests run from src/realm_frontend.
const EXTENSIONS_DIR = join(process.cwd(), '..', '..', 'extensions', 'extensions');

/** The version a repo extension is published at: the manifest's. */
export function extensionVersion(id: string): string {
	return JSON.parse(readFileSync(join(EXTENSIONS_DIR, id, 'manifest.json'), 'utf8')).version;
}

function isSandboxed(id: string): boolean {
	return (
		JSON.parse(readFileSync(join(EXTENSIONS_DIR, id, 'manifest.json'), 'utf8')).runtime ===
		'sandboxed'
	);
}

/**
 * Opens an extension's page as `identity` in `page`; `close` stops the portal host page. An extension that
 * sends its visitors to a page of the realm (`path`, as notifications does) has the whole page as its own.
 */
export async function openExtension(
	page: Page,
	realm: StandaloneRealm,
	identity: Ed25519KeyIdentity,
	id: string,
	path = `/extensions/${id}`
): Promise<{ app: ExtensionPage; frame: FrameLocator; close: () => Promise<void> }> {
	await exposeSigner(page, identity);
	const { frame, close } = await openInPortal(page, realm, path);
	if (!path.startsWith('/extensions/')) return { app: frame, frame, close };
	const mount = frame.locator(`[data-extension-id="${id}"]`);
	await expect
		.poll(async () => !(await reloadIfInterrupted(page, frame)) && (await mount.count()) > 0, {
			message: `${id} is mounted`,
			timeout: 120_000,
			intervals: [1_000]
		})
		.toBe(true);
	return { app: isSandboxed(id) ? mount.frameLocator('iframe') : mount, frame, close };
}
