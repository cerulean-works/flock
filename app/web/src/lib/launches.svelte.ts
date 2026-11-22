import { datasetCall } from './datasets.ts';
import type { EvalLaunch } from './types.ts';

/** The dashboard's endpoint for the org's eval launches (026): the list, `catalogue` or one. */
export const launchesApi = (org: string, op = '') =>
	`/api/evals/${encodeURIComponent(org)}/launches${op ? `/${op}` : ''}`;

/** How often running launches are asked about, in milliseconds. */
const POLL = 5000;
/** How long a launch that ended keeps its row, in seconds. */
const KEEP = 86_400;

/** A project's eval launches, followed while any runs. */
export class LaunchList {
	launches = $state<EvalLaunch[]>([]);
	#org: string;
	#project: string;
	#timer: ReturnType<typeof setTimeout> | null = null;
	#stopped = true;

	constructor(org: string, project: string) {
		this.#org = org;
		this.#project = project;
	}

	/** The launches to show: running ones, and ones that ended in the last day. */
	get shown(): EvalLaunch[] {
		const now = Date.now() / 1000;
		return this.launches.filter(
			(l) => l.state === 'running' || now - (l.finished_at ?? l.started_at) < KEEP
		);
	}

	/** Start following the list; returns what stops it. */
	watch(): () => void {
		this.#stopped = false;
		void this.refresh();
		return () => {
			this.#stopped = true;
			if (this.#timer) clearTimeout(this.#timer);
			this.#timer = null;
		};
	}

	async refresh() {
		if (this.#timer) clearTimeout(this.#timer);
		this.#timer = null;
		let failed = false;
		try {
			// Filtered by Flock before its limit, so other projects' launches never crowd these out.
			const url = `${launchesApi(this.#org)}?project=${encodeURIComponent(this.#project)}`;
			const { launches } = await datasetCall<{ launches: EvalLaunch[] }>(url);
			this.launches = launches;
		} catch {
			failed = true; // asked again on the next round, even before any launch is known
		}
		this.#schedule(failed);
	}

	/** A launch just started here, shown before the next round. */
	add(job: EvalLaunch) {
		this.launches = [job, ...this.launches.filter((l) => l.id !== job.id)];
		this.#schedule();
	}

	async cancel(id: number) {
		const job = await datasetCall<EvalLaunch>(launchesApi(this.#org, String(id)), {
			method: 'DELETE'
		});
		this.launches = this.launches.map((l) => (l.id === id ? job : l));
	}

	#schedule(retry = false) {
		if (this.#stopped || this.#timer) return;
		if (!retry && !this.launches.some((l) => l.state === 'running')) return;
		this.#timer = setTimeout(() => {
			this.#timer = null;
			void this.refresh();
		}, POLL);
	}
}
