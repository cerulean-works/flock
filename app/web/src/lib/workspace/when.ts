/** A date typed into the palette as a date field holds it: `YYYY-MM-DDTHH:mm`, local time. */
const field = (d: Date) => {
	const p = (n: number) => String(n).padStart(2, '0');
	return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};

const UNITS: Record<string, number> = { m: 60e3, h: 3600e3, d: 86400e3, w: 7 * 86400e3 };

/**
 * A typed date: "7d", "24h", "2w", "30m" (that long ago), "today", "yesterday", "2026-11-20"
 * or "2026-11-20 14:30". Null when it reads as none of them.
 */
export function parseWhen(text: string, now = new Date()): string | null {
	const t = text.trim().toLowerCase();
	if (!t) return null;
	const ago = /^(\d+(?:\.\d+)?)\s*([mhdw])$/.exec(t);
	if (ago) return field(new Date(now.getTime() - Number(ago[1]) * UNITS[ago[2]]));
	if (t === 'today' || t === 'yesterday') {
		const d = new Date(now);
		d.setHours(0, 0, 0, 0);
		if (t === 'yesterday') d.setDate(d.getDate() - 1);
		return field(d);
	}
	const date = /^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ t](\d{1,2}):(\d{2}))?$/.exec(t);
	if (date) {
		const [, y, mo, d, h = '0', mi = '0'] = date;
		const at = new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi));
		return Number.isNaN(at.getTime()) ? null : field(at);
	}
	return null;
}
