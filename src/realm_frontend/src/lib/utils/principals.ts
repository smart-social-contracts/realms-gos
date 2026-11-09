/** A principal as a short label: the start and the end of it. */
export const shortPrincipal = (principal: string) =>
	`${principal.slice(0, 8)}…${principal.slice(-6)}`;
