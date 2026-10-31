// An installed extension's page, opened as a member in a portal host frame.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, type FrameLocator, type Locator, type Page } from '@playwright/test';
import type { Ed25519KeyIdentity } from '@dfinity/identity';
import { exposeSigner, openInPortal, type StandaloneRealm } from './realm';

/** Where the page's controls are: the extension's mount point, or the iframe a sandboxed extension runs in. */
export type ExtensionPage = Locator | FrameLocator;

// The Playwright tests run from src/realm_frontend.
const EXTENSIONS_DIR = join(process.cwd(), '..', '..', 'extensions', 'extensions');

/** The version a repo extension is published at: the manifest's. */
export function extensionVersion(id: string): string {
	return JSON.parse(readFileSync(join(EXTENSIONS_DIR, id, 'manifest.json'), 'utf8')).version;
}

function isSandboxed(id: string): boolean {
	return JSON.parse(readFileSync(join(EXTENSIONS_DIR, id, 'manifest.json'), 'utf8')).runtime === 'sandboxed';
}

/** Opens `/extensions/<id>` as `identity` in `page`; `close` stops the portal host page. */
export async function openExtension(
	page: Page,
	realm: StandaloneRealm,
	identity: Ed25519KeyIdentity,
	id: string
): Promise<{ app: ExtensionPage; close: () => Promise<void> }> {
	await exposeSigner(page, identity);
	const { frame, close } = await openInPortal(page, realm, `/extensions/${id}`);
	const mount = frame.locator(`[data-extension-id="${id}"]`);
	await expect(mount, `${id} is mounted`).toBeAttached({ timeout: 120_000 });
	return { app: isSandboxed(id) ? mount.frameLocator('iframe') : mount, close };
}
