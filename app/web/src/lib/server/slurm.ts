import {
	FLOCK_SLURM_ACCESS_ID,
	FLOCK_SLURM_ACCESS_SECRET,
	FLOCK_SLURM_PUBLIC_URL,
	FLOCK_SLURM_URL
} from '$app/env/private';

import type { ClusterGlance } from '#lib/home/cluster.ts';

interface Utilization {
	series: {
		capacity: number | null;
		current: number | null;
		points: { t: string; allocated: number | null }[];
	}[];
}

/** Whether Flock is told where the dashboard is: Home then says when it can't reach it. */
export const slurmConfigured = () => !!FLOCK_SLURM_URL;

/** Home must not wait on another service: past this, the tile is left out. */
const TIMEOUT_MS = 1500;

/**
 * A glance at the Slurm dashboard's caches (its read-only API; nothing runs on the clusters for
 * this). Null when it isn't configured, or doesn't answer in time.
 */
export async function clusterGlance(): Promise<ClusterGlance | null> {
	if (!FLOCK_SLURM_URL) return null;
	const headers: Record<string, string> = {};
	if (FLOCK_SLURM_ACCESS_ID && FLOCK_SLURM_ACCESS_SECRET) {
		headers['CF-Access-Client-Id'] = FLOCK_SLURM_ACCESS_ID;
		headers['CF-Access-Client-Secret'] = FLOCK_SLURM_ACCESS_SECRET;
	}
	const get = async <T>(path: string) => {
		const res = await fetch(`${FLOCK_SLURM_URL}${path}`, {
			headers,
			signal: AbortSignal.timeout(TIMEOUT_MS)
		});
		if (!res.ok) throw new Error(`slurm dashboard answered ${res.status}`);
		return (await res.json()) as T;
	};
	try {
		const [use, jobs] = await Promise.all([
			get<Utilization>('/api/utilization?cluster=all'),
			get<{ data: { jobs: { state: string }[] } }>('/api/jobs?cluster=all')
		]);
		// The classes summed, bucket by bucket (they share the dashboard's buckets).
		const byTime = new Map<string, number | null>();
		for (const s of use.series)
			for (const p of s.points) {
				const was = byTime.get(p.t);
				byTime.set(p.t, p.allocated == null ? (was ?? null) : (was ?? 0) + p.allocated);
			}
		const capacities = use.series.map((s) => s.capacity);
		let running = 0;
		let pending = 0;
		for (const j of jobs.data.jobs) {
			if (j.state === 'RUNNING') running++;
			else if (j.state === 'PENDING') pending++;
		}
		return {
			href: FLOCK_SLURM_PUBLIC_URL ?? FLOCK_SLURM_URL,
			allocated: use.series.reduce((n, s) => n + (s.current ?? 0), 0),
			capacity: capacities.every((c) => c != null)
				? capacities.reduce<number>((n, c) => n + c!, 0)
				: null,
			points: [...byTime]
				.map(([t, y]) => ({ x: Date.parse(t) / 1000, y }))
				.sort((a, b) => a.x - b.x),
			running,
			pending
		};
	} catch {
		return null;
	}
}
