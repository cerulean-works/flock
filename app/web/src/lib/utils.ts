import type { MetricFormat } from './types.ts';

export { cn } from 'cn';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type WithoutChild<T> = T extends { child?: any } ? Omit<T, 'child'> : T;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type WithoutChildren<T> = T extends { children?: any } ? Omit<T, 'children'> : T;
export type WithoutChildrenOrChild<T> = WithoutChildren<WithoutChild<T>>;
export type WithElementRef<T, U extends HTMLElement = HTMLElement> = T & { ref?: U | null };

// ----------------------------------------------------------------------------------------------
// Collections

/** The integers in [a, b). */
export const range = (a: number, b: number) =>
	Array.from({ length: Math.max(0, b - a) }, (_, i) => a + i);

/** Element-wise identity of two lists. */
export const sameItems = (a: readonly unknown[], b: readonly unknown[]) =>
	a.length === b.length && a.every((v, i) => v === b[i]);

export function toggle<T>(set: Set<T>, value: T) {
	if (set.has(value)) set.delete(value);
	else set.add(value);
}

// ----------------------------------------------------------------------------------------------
// Forms

/** A trimmed text field; missing fields are empty. */
export const formText = (form: FormData, name: string) => String(form.get(name) ?? '').trim();

/** Tags typed as a list (`a, b c`): split on commas and whitespace, without duplicates. */
export const parseTags = (s: string) => [...new Set(s.split(/[\s,]+/).filter(Boolean))];

/** Why a project name is invalid (Flock's rule, which is wandb's), or null if it is valid. */
export function projectNameError(name: string): string | null {
	if (!name.trim()) return 'Enter a name';
	if (name.length > 128) return 'Use at most 128 characters';
	if (name !== name.trim()) return 'Remove the surrounding spaces';
	// eslint-disable-next-line no-control-regex
	if (/[/\\#?%:\u0000-\u001f\u007f]/.test(name)) return 'Names can’t contain / \\ # ? % or :';
	return null;
}

/** The dashboard's own top-level pages: an organization of the same name couldn't be opened. */
const RESERVED = [
	'account',
	'admin',
	'api',
	'auth',
	'favicon.ico',
	'invite',
	'login',
	'logout',
	'orgs',
	'reset',
	'setup'
];

/**
 * Why an organization ID is invalid (a project name's rule, and not a dashboard page). The ID is
 * the org's name in URLs and the wandb SDK's `entity`; it can't change once the org exists.
 */
export function orgIdError(id: string): string | null {
	if (!id.trim()) return 'Enter an ID';
	if (RESERVED.includes(id.toLowerCase()) || id.startsWith('_')) return `“${id}” is reserved`;
	return projectNameError(id);
}

/** Why an organization's display name is invalid: any text, up to 128 characters. */
export function orgDisplayNameError(name: string): string | null {
	if (!name.trim()) return 'Enter a display name';
	if (name.length > 128) return 'Use at most 128 characters';
	// eslint-disable-next-line no-control-regex
	if (/[\u0000-\u001f\u007f]/.test(name)) return 'Remove the control characters';
	return null;
}

/**
 * An ID suggested for a display name: lowercase ASCII letters and digits, words joined by
 * dashes ("Cerulean Labs" -> "cerulean-labs"). Empty when nothing in it is usable.
 */
export function slugify(name: string, max = 64): string {
	return name
		.normalize('NFKD')
		.replace(/[\u0300-\u036f]/g, '')
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, '-')
		.slice(0, max)
		.replace(/^-+|-+$/g, '');
}

/** The image types Flock takes for avatars (it checks the bytes; SVG is refused). */
export const AVATAR_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];
export const AVATAR_MAX_BYTES = 1024 * 1024;

/** Why a picked file can't be an avatar, or null if it can. */
export function avatarFileError(file: unknown): string | null {
	if (!(file instanceof Blob) || !file.size) return 'Choose an image';
	if (!AVATAR_TYPES.includes(file.type)) return 'Use a PNG, JPEG, GIF or WebP image';
	if (file.size > AVATAR_MAX_BYTES) return 'Use an image of at most 1 MB';
	return null;
}

/** "3 runs", "1 run". */
export const count = (n: number, one: string, many = `${one}s`) =>
	`${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;

// ----------------------------------------------------------------------------------------------
// Bytes and files

type FromBase64 = (s: string) => Uint8Array<ArrayBuffer>;

/** Native base64 decoding where available: `Uint8Array.fromBase64` in browsers, `Buffer` in Node. */
export function base64ToBytes(s: string): Uint8Array<ArrayBuffer> {
	const native = (Uint8Array as unknown as { fromBase64?: FromBase64 }).fromBase64;
	if (native) return native(s);
	if (typeof Buffer !== 'undefined') {
		const b = Buffer.from(s, 'base64');
		return new Uint8Array(
			b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)
		) as Uint8Array<ArrayBuffer>;
	}
	const bin = atob(s);
	const out = new Uint8Array(bin.length);
	for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
	return out;
}

/** Hands a blob to the browser as a download. */
export function saveBlob(blob: Blob, filename: string) {
	const url = URL.createObjectURL(blob);
	const a = document.createElement('a');
	a.href = url;
	a.download = filename;
	a.click();
	setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const fileSafe = (s: string) => s.replace(/[^a-z0-9._-]+/gi, '_');

// ----------------------------------------------------------------------------------------------
// Formatting
//
// Every formatter is locale-independent and pure, so server-rendered HTML and the hydrated
// client always produce identical strings.

const group = (s: string) => s.replace(/\B(?=(\d{3})+(?!\d))/g, ',');

function expo(v: number, digits: number) {
	return v
		.toExponential(digits)
		.replace(/\.?0+e/, 'e')
		.replace('e+', 'e');
}

/** General-purpose number for table cells and readouts. */
export function formatNumber(v: number): string {
	if (Number.isNaN(v)) return '–';
	if (!Number.isFinite(v)) return v > 0 ? '∞' : '-∞';
	if (v === 0) return '0';
	const a = Math.abs(v);
	if (Number.isInteger(v) && a < 1e15) return group(String(v));
	if (a >= 1e9 || a < 1e-4) return expo(v, 3);
	const s = String(+v.toPrecision(5));
	const dot = s.indexOf('.');
	return dot === -1 ? group(s) : group(s.slice(0, dot)) + s.slice(dot);
}

const SI = [
	{ v: 1e12, s: 'T' },
	{ v: 1e9, s: 'B' },
	{ v: 1e6, s: 'M' },
	{ v: 1e3, s: 'k' }
];

function si(v: number, digits = 3) {
	const a = Math.abs(v);
	for (const u of SI) if (a >= u.v) return `${+(v / u.v).toPrecision(digits)}${u.s}`;
	return String(+v.toPrecision(digits));
}

/** A metric value for chart tooltips: precise but compact. */
export function formatMetric(v: number, format: MetricFormat): string {
	if (!Number.isFinite(v)) return '–';
	switch (format) {
		case 'pct':
			return `${+v.toFixed(2)}%`;
		case 'exp':
			return v === 0 ? '0' : expo(v, 3);
		case 'si':
			return si(v, 4);
		case 'int':
			return group(String(Math.round(v)));
		default:
			return formatNumber(v);
	}
}

/** Axis tick label; `step` is the tick spacing and decides the precision. */
export function formatTick(v: number, step: number, format: MetricFormat): string {
	if (v === 0) return '0';
	const a = Math.abs(v);
	if (format === 'exp' || a < 1e-3 || (a >= 1e9 && format !== 'si')) return expo(v, 1);
	if (format === 'si' || a >= 1e4) return si(v, 3);
	const decimals = Math.max(0, Math.min(6, -Math.floor(Math.log10(step) + 1e-9)));
	const s = v.toFixed(decimals);
	return format === 'pct' ? `${s}%` : s;
}

export function formatStep(step: number): string {
	return group(String(Math.round(step)));
}

export function formatAxisStep(step: number): string {
	return step >= 1e4 ? si(step, 3) : group(String(Math.round(step)));
}

/** An amount in an org's currency: cents only on small amounts (`$0`, `$2.50`, `$1,440`). */
export function money(v: number, currency: string): string {
	const digits = Math.abs(v) >= 100 || Number.isInteger(v) ? 0 : 2;
	try {
		return new Intl.NumberFormat('en-US', {
			style: 'currency',
			currency,
			minimumFractionDigits: digits,
			maximumFractionDigits: digits
		}).format(v);
	} catch {
		return `${currency} ${v.toFixed(digits)}`; // not an ISO currency code
	}
}

/** GPU hours: one decimal under 100, whole hours above. */
export const hours = (h: number) =>
	h >= 100 ? group(String(Math.round(h))) : String(+h.toFixed(1));

/** `8 × H200`, `H200`, or null when the GPUs aren't known (or there are none). */
export function gpus(type: string | null, count: number | null): string | null {
	if (!count) return null;
	const name = type ?? 'GPU';
	return count === 1 ? name : `${count} × ${name}`;
}

const MONTH_NAMES = [
	'January',
	'February',
	'March',
	'April',
	'May',
	'June',
	'July',
	'August',
	'September',
	'October',
	'November',
	'December'
];

/** `2026-10` as `October 2026`. */
export function monthName(month: string): string {
	const [y, m] = month.split('-').map(Number);
	return `${MONTH_NAMES[m - 1] ?? month} ${y}`;
}

/** The UTC month (`YYYY-MM`) `delta` months from `month`. */
export function shiftMonth(month: string, delta: number): string {
	const [y, m] = month.split('-').map(Number);
	const d = new Date(Date.UTC(y, m - 1 + delta, 1));
	return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

/** The UTC month of a time in seconds since the epoch. */
export function monthOf(ts: number): string {
	const d = new Date(ts * 1000);
	return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

/** `3h ago`; both times in seconds since the epoch (Flock's unit). */
export function relativeTime(ts: number, now: number): string {
	if (!Number.isFinite(ts)) return '–';
	const s = Math.max(0, now - ts);
	if (s < 60) return 'just now';
	if (s < 3600) return `${Math.floor(s / 60)}m ago`;
	if (s < 86_400) return `${Math.floor(s / 3600)}h ago`;
	if (s < 7 * 86_400) return `${Math.floor(s / 86_400)}d ago`;
	if (s < 30 * 86_400) return `${Math.floor(s / (7 * 86_400))}w ago`;
	return `${Math.floor(s / (30 * 86_400))}mo ago`;
}

export function duration(sec: number): string {
	if (!Number.isFinite(sec)) return '–';
	const s = Math.round(sec);
	const d = Math.floor(s / 86_400);
	const h = Math.floor((s % 86_400) / 3600);
	const m = Math.floor((s % 3600) / 60);
	if (d) return `${d}d ${h}h`;
	if (h) return `${h}h ${String(m).padStart(2, '0')}m`;
	if (m) return `${m}m ${String(s % 60).padStart(2, '0')}s`;
	return `${s}s`;
}

const UNITS: [string, number][] = [
	['d', 86_400],
	['h', 3600],
	['m', 60],
	['s', 1]
];

/** Axis label for a time in seconds (`1h 30m`); `step` is the tick spacing. */
export function formatRuntime(sec: number, step = 1): string {
	if (!Number.isFinite(sec)) return '–';
	if (sec === 0) return '0';
	if (step < 1) return `${sec.toFixed(Math.min(3, -Math.floor(Math.log10(step) + 1e-9)))}s`;
	let s = Math.round(Math.abs(sec));
	const parts: string[] = [];
	for (const [unit, n] of UNITS) {
		if (s < n) continue;
		parts.push(`${Math.floor(s / n)}${unit}`);
		s %= n;
	}
	return (sec < 0 ? '-' : '') + parts.join(' ');
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** `Sep 05 '26 10:37`, in UTC; `ts` in seconds since the epoch. */
export function dateTime(ts: number): string {
	if (!Number.isFinite(ts)) return '–';
	const d = new Date(ts * 1000);
	const p = (n: number) => String(n).padStart(2, '0');
	return `${MONTHS[d.getUTCMonth()]} ${p(d.getUTCDate())} '${p(d.getUTCFullYear() % 100)} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

// Wall-clock axes read in the viewer's time zone. They are only drawn in the browser (an x axis
// other than steps is a choice made there), so server and client never disagree about them.

const local = (ts: number) => {
	const d = new Date(ts * 1000);
	const p = (n: number) => String(n).padStart(2, '0');
	return {
		day: `${MONTHS[d.getMonth()]} ${p(d.getDate())}`,
		time: `${p(d.getHours())}:${p(d.getMinutes())}`,
		seconds: p(d.getSeconds()),
		midnight: !d.getHours() && !d.getMinutes() && !d.getSeconds()
	};
};

/** Axis label for a wall time in epoch seconds: the date at midnights and for days apart. */
export function formatWallTick(ts: number, step: number): string {
	if (!Number.isFinite(ts)) return '–';
	const t = local(ts);
	if (step >= 86_400 || t.midnight) return t.day;
	return step < 60 ? `${t.time}:${t.seconds}` : t.time;
}

/** A wall time in epoch seconds for readouts: `Oct 20 14:03:05`. */
export function formatWallTime(ts: number): string {
	if (!Number.isFinite(ts)) return '–';
	const t = local(ts);
	return `${t.day} ${t.time}:${t.seconds}`;
}

/** A JSON table cell: strings as-is, lists joined, media values by type. */
export function formatValue(v: unknown): string {
	if (v == null || v === '') return '–';
	if (typeof v === 'number') return formatNumber(v);
	if (typeof v === 'string') return v;
	if (typeof v === 'boolean') return String(v);
	if (Array.isArray(v)) return v.length ? v.map(formatValue).join(' · ') : '–';
	if (typeof v === 'object' && '_type' in v) return `[${String((v as { _type: unknown })._type)}]`;
	return JSON.stringify(v);
}

/** A file size: `885 B`, `12.4 KB`, `3.1 MB`. */
export function bytes(n: number): string {
	if (n < 1024) return `${n} B`;
	const units = ['KB', 'MB', 'GB', 'TB'];
	let v = n / 1024;
	let i = 0;
	while (v >= 1024 && i < units.length - 1) {
		v /= 1024;
		i++;
	}
	return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}
