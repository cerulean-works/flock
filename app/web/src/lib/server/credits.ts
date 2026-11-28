import { FLOCK_CREDITS_PROJECT, FLOCK_CREDITS_TOKEN, FLOCK_CREDITS_URL } from '$app/env/private';

import type { Credits } from '#lib/home/credits.ts';

/** Whether Flock is told where the credit dashboard is: then it stands in for Flock's budget. */
export const creditsConfigured = () => !!FLOCK_CREDITS_URL;

/**
 * The dashboard's first answer after a while can be slow (a cold connection through its tunnel,
 * its own snapshot warming up): Home streams the tile in rather than wait, so this can be
 * generous. Past it, the tile says the dashboard didn't answer.
 */
const TIMEOUT_MS = 10_000;
/** A summary this fresh is served as is; the wallet moves slowly. */
const FRESH_MS = 60_000;
/** When the dashboard doesn't answer, a summary this old still stands in for it. */
const STALE_MS = 6 * 3_600_000;

/** The last good summary per query, and the fetch in flight, shared by every request. */
const cache = new Map<string, { at: number; value: Credits }>();
const inflight = new Map<string, Promise<Credits | null>>();

/**
 * The NCHC wallet and one month's spend (this month by default), from the credit dashboard's
 * snapshot. Null when it isn't configured, or doesn't answer and nothing recent is cached.
 */
export async function creditsSummary(
	month?: string | null,
	/** How long this caller waits; the fetch itself runs on, to fill the cache for the next. */
	waitMs = 1500
): Promise<Credits | null> {
	if (!FLOCK_CREDITS_URL) return null;
	const params = new URLSearchParams();
	if (FLOCK_CREDITS_PROJECT) params.set('project', FLOCK_CREDITS_PROJECT);
	if (month && /^\d{4}-\d{2}$/.test(month)) params.set('month', month);
	const key = params.toString();
	const hit = cache.get(key);
	if (hit && Date.now() - hit.at < FRESH_MS) return hit.value;
	let pending = inflight.get(key);
	if (!pending) {
		pending = fetchSummary(params)
			.then((value) => {
				if (value) cache.set(key, { at: Date.now(), value });
				return value;
			})
			.finally(() => inflight.delete(key));
		inflight.set(key, pending);
	}
	const value = await Promise.race([
		pending,
		new Promise<null>((done) => setTimeout(() => done(null), waitMs))
	]);
	if (value) return value;
	return hit && Date.now() - hit.at < STALE_MS ? hit.value : null;
}

async function fetchSummary(params: URLSearchParams): Promise<Credits | null> {
	try {
		const res = await fetch(`${FLOCK_CREDITS_URL}/api/summary${params.size ? `?${params}` : ''}`, {
			headers: FLOCK_CREDITS_TOKEN ? { Authorization: `Bearer ${FLOCK_CREDITS_TOKEN}` } : {},
			signal: AbortSignal.timeout(TIMEOUT_MS)
		});
		return res.ok ? ((await res.json()) as Credits) : null;
	} catch {
		return null;
	}
}
