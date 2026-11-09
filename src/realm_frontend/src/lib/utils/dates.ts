/** A time in epoch seconds as the reader's locale writes it; empty for 0, which the realm uses for "never". */
export function fmtTime(ts: number): string {
	return ts ? new Date(ts * 1000).toLocaleString() : '';
}

/** A time as the value of a datetime-local input. */
export function toLocalInput(date: Date): string {
	const pad = (n: number) => String(n).padStart(2, '0');
	return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
