// Where a demo realm sends its visitors for the legal texts. A realm has no portal address of its own, and the demo's
// privacy page and terms (with their "Demo environment" section) live on the demo portal, so every demo realm links
// there, whichever host it was reached from.
export const DEMO_LEGAL_LINKS = [
	{ key: 'demo_data_banner.privacy', href: 'https://demo.gos.earth/privacy' },
	{ key: 'demo_data_banner.terms', href: 'https://demo.gos.earth/terms' },
	{ key: 'demo_data_banner.report', href: 'mailto:contact@realmsgos.org' }
] as const;
