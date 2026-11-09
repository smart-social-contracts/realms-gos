// jsdom has no matchMedia, which flowbite-svelte reads when its components load.
if (typeof window !== 'undefined' && !window.matchMedia) {
	window.matchMedia = ((query: string) => ({
		matches: false,
		media: query,
		addEventListener() {},
		removeEventListener() {}
	})) as unknown as typeof window.matchMedia;
}
