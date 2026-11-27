import { projectUrl } from '#lib/data/cache.ts';
import type { RunHistory } from '#lib/types.ts';

export interface Curve {
	x: number[];
	y: (number | null)[];
}

/** Points a tile draws: enough for its shape, little to send. */
const POINTS = 160;

/**
 * A run's curves for Home's tiles: `keys` only, or every metric (to find its loss). Read again
 * on every call: live runs grow, and Home re-reads them on its poll.
 */
export async function runCurves(
	org: string,
	project: string,
	run: string,
	keys?: string[],
	signal?: AbortSignal
): Promise<Record<string, Curve>> {
	const search = new URLSearchParams({ max_points: String(POINTS) });
	if (keys?.length) search.set('keys', keys.join(','));
	const res = await fetch(
		`${projectUrl(org, project)}/runs/${encodeURIComponent(run)}/history?${search}`,
		{ signal }
	);
	if (!res.ok) throw new Error(`Flock answered ${res.status}`);
	return ((await res.json()) as RunHistory).series;
}

/** The last finite value, and the change over the last stretch of the curve (a fifth of it). */
export function reading(c: Curve): { last: number | null; delta: number | null } {
	const ys = c.y.filter((v): v is number => v != null && Number.isFinite(v));
	if (!ys.length) return { last: null, delta: null };
	const last = ys[ys.length - 1];
	const back = ys[Math.max(0, Math.floor(ys.length * 0.8) - 1)];
	return { last, delta: ys.length > 4 ? last - back : null };
}
