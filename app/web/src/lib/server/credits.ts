import { FLOCK_CREDITS_PROJECT, FLOCK_CREDITS_TOKEN, FLOCK_CREDITS_URL } from '$app/env/private';

import type { Credits } from '#lib/home/credits.ts';

/** Whether Flock is told where the credit dashboard is: then it stands in for Flock's budget. */
export const creditsConfigured = () => !!FLOCK_CREDITS_URL;

/** Home must not wait on another service: past this, it says the dashboard didn't answer. */
const TIMEOUT_MS = 1500;

/**
 * The NCHC wallet and one month's spend (this month by default), from the credit dashboard's
 * snapshot. Null when it isn't configured, or doesn't answer in time.
 */
export async function creditsSummary(month?: string | null): Promise<Credits | null> {
	if (!FLOCK_CREDITS_URL) return null;
	const params = new URLSearchParams();
	if (FLOCK_CREDITS_PROJECT) params.set('project', FLOCK_CREDITS_PROJECT);
	if (month && /^\d{4}-\d{2}$/.test(month)) params.set('month', month);
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
