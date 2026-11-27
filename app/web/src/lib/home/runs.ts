import type { FeedKind, FeedPage } from '#lib/types.ts';
import { gpus } from '#lib/utils.ts';

/** A run as Home lists it: its latest event, one row a run. */
export interface HomeRun {
	org: string;
	project: string;
	id: string;
	name: string;
	state: string;
	/** When its latest event happened: started, or ended. */
	at: number;
	kind: FeedKind;
	runtime: number;
	gpus: string | null;
	job: string | null;
	/** Who logged it; null for runs from before accounts. */
	owner: { id: number; name: string; avatar_url: string | null } | null;
}

/** The feed's items as runs, newest event first, each run once. */
export function runsOf(page: FeedPage | null): HomeRun[] {
	const seen = new Set<string>();
	const out: HomeRun[] = [];
	for (const item of [...(page?.items ?? [])].sort((a, b) => b.at - a.at)) {
		const r = item.run;
		if (!r) continue;
		const key = `${item.project}/${r.id}`;
		if (seen.has(key)) continue;
		seen.add(key);
		out.push({
			org: item.org,
			project: item.project,
			id: r.id,
			name: r.display_name ?? r.id,
			state: r.state,
			at: item.at,
			kind: item.kind,
			runtime: r.runtime,
			gpus: gpus(r.gpu_type, r.gpu_count),
			job: r.job_type,
			owner: item.actor && {
				id: item.actor.id,
				name: item.actor.name,
				avatar_url: item.actor.avatar_url
			}
		});
	}
	return out;
}

/** Live first (newest event first), then the rest as they ended. */
export function liveFirst(runs: HomeRun[]): HomeRun[] {
	const live = (r: HomeRun) => (r.state === 'running' || r.state === 'preempting' ? 0 : 1);
	return [...runs].sort((a, b) => live(a) - live(b) || b.at - a.at);
}
