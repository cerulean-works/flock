/**
 * Two runs' summaries and configs side by side, for the compare page: every key either has, with
 * whether the two agree and, for two numbers, how far apart they are.
 */
import { flatten } from '#lib/data/run.ts';

export interface PairRow {
	key: string;
	a: unknown;
	b: unknown;
	/** Both have it, and it's the same. */
	same: boolean;
	/** A − B, when both are numbers. */
	delta: number | null;
	/** How far apart as a share of the larger: 0 the same, 1 one of them zero or opposite. */
	spread: number;
}

/** Same value: numbers and strings as they are, lists and objects by their JSON. */
export function sameValue(x: unknown, y: unknown): boolean {
	if (x === y) return true;
	if (x == null || y == null) return false;
	if (typeof x === 'object' || typeof y === 'object')
		return JSON.stringify(x) === JSON.stringify(y);
	return false;
}

const MISSING = Symbol('missing');

/**
 * The keys of two nested records (config or summary) paired up, A to Z; a key one run lacks is
 * `undefined` on that side. `skip` leaves keys out (wandb's `_step`, `_runtime`, ...).
 */
export function pairRows(
	a: Record<string, unknown>,
	b: Record<string, unknown>,
	skip: (key: string) => boolean = () => false
): PairRow[] {
	const left = new Map(flatten(a));
	const right = new Map(flatten(b));
	const keys = [...new Set([...left.keys(), ...right.keys()])].filter((k) => !skip(k)).sort();
	return keys.map((key) => {
		const x = left.has(key) ? left.get(key) : MISSING;
		const y = right.has(key) ? right.get(key) : MISSING;
		const av = x === MISSING ? undefined : x;
		const bv = y === MISSING ? undefined : y;
		const numbers = typeof av === 'number' && typeof bv === 'number';
		const delta = numbers ? av - bv : null;
		const top = numbers ? Math.max(Math.abs(av), Math.abs(bv)) : 0;
		const same = x !== MISSING && y !== MISSING && sameValue(av, bv);
		return {
			key,
			a: av,
			b: bv,
			same,
			delta,
			spread: same ? 0 : numbers ? (top ? Math.min(1, Math.abs(av - bv) / top) : 0) : 1
		};
	});
}

/** Most different first: by spread, then by the size of the gap, then A to Z. */
export function byDifference(rows: readonly PairRow[]): PairRow[] {
	return rows.toSorted(
		(x, y) =>
			y.spread - x.spread ||
			Math.abs(y.delta ?? 0) - Math.abs(x.delta ?? 0) ||
			x.key.localeCompare(y.key)
	);
}
