/** The browser title of a page: its name, then the realm's ("Voting · Vilanova"). Either part may be missing. */
export function pageTitle(label: string | null | undefined, realm: string | null | undefined): string {
	const parts = [label, realm].map((part) => (part ?? '').trim()).filter(Boolean);
	return parts.length ? parts.join(' · ') : 'Realm';
}
