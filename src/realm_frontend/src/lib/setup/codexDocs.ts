export const CODEX_REPO_BASE =
	'https://github.com/smart-social-contracts/realms-codices/tree/main/codices';

export const REALMS_RAW_BASE =
	'https://raw.githubusercontent.com/smart-social-contracts/realms-gos/main';

/** Demo-realm folders that hold the official default logo and background. */
const CODEX_DEMO_DIR: Record<string, string> = {
	agora: 'examples/demo/realm2',
	dominion: 'examples/demo/realm1',
	syntropia: 'examples/demo/realm3'
};

export function defaultCodexBranding(
	codexId: string
): { logo: string; background: string } | null {
	const dir = CODEX_DEMO_DIR[codexId];
	if (!dir) return null;
	return {
		logo: `${REALMS_RAW_BASE}/${dir}/logo.png`,
		background: `${REALMS_RAW_BASE}/${dir}/background.png`
	};
}

const DEFAULT_TIMEOUT_MS = 4_000;
const descriptionCache = new Map<string, string>();

export function shortDescriptionUrl(codexId: string): string {
	return `https://raw.githubusercontent.com/smart-social-contracts/realms-codices/main/codices/${codexId}/SHORT_DESCRIPTION.md`;
}

export function repositoryUrl(codexId: string): string {
	return `${CODEX_REPO_BASE}/${codexId}`;
}

export function stripMarkdownTitle(text: string): string {
	return text.replace(/^#.*\n\n/, '').trim().replace(/\s*\n+\s*/g, ' ');
}

export function __resetCodexDocsCache(): void {
	descriptionCache.clear();
}

export async function fetchCodexDescription(
	codexId: string,
	fallback: string,
	opts?: { fetchImpl?: typeof fetch; timeoutMs?: number }
): Promise<string> {
	const cached = descriptionCache.get(codexId);
	if (cached !== undefined) return cached;

	const fetchImpl = opts?.fetchImpl ?? fetch;
	const timeoutMs = opts?.timeoutMs ?? DEFAULT_TIMEOUT_MS;

	try {
		const controller = new AbortController();
		const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
		const res = await fetchImpl(shortDescriptionUrl(codexId), { signal: controller.signal });
		clearTimeout(timeoutId);

		if (!res.ok) {
			console.debug('codex description fetch failed:', codexId, res.status);
			return fallback;
		}

		const description = stripMarkdownTitle(await res.text());
		if (!description) return fallback;
		descriptionCache.set(codexId, description);
		return description;
	} catch (error) {
		console.debug('codex description fetch error:', codexId, error);
		return fallback;
	}
}
