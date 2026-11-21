/* eslint-disable svelte/prefer-svelte-reactivity -- the sets here are replaced, never changed in place */
import { invalidateAll } from '$app/navigation';
import { datasetCall, datasetsApi } from './datasets.ts';
import type { DatasetJob } from './types.ts';

/** How often running jobs are asked about, in milliseconds. */
const POLL = 1500;
/** How long a job that ended keeps its row, in seconds. */
const KEEP = 86_400;
const HIDDEN = 'flock-dataset-jobs-hidden';

function hidden(): Set<number> {
	try {
		return new Set(JSON.parse(localStorage.getItem(HIDDEN) ?? '[]') as number[]);
	} catch {
		return new Set();
	}
}

/**
 * The org's blends and datasets being made independent (D176), followed while any runs, as
 * `HFImportList` follows imports. When one finishes the page reads its data again.
 */
export class DatasetJobList {
	jobs = $state<DatasetJob[]>([]);
	#hidden = $state(new Set<number>());
	#org: string;
	#timer: ReturnType<typeof setTimeout> | null = null;
	#stopped = true;

	constructor(org: string) {
		this.#org = org;
	}

	get shown(): DatasetJob[] {
		const now = Date.now() / 1000;
		return this.jobs.filter(
			(j) =>
				j.state === 'running' ||
				(!this.#hidden.has(j.id) && now - (j.finished_at ?? j.started_at) < KEEP)
		);
	}

	watch(): () => void {
		this.#stopped = false;
		this.#hidden = hidden();
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
		let found: DatasetJob[];
		try {
			found = (await datasetCall<{ jobs: DatasetJob[] }>(datasetsApi(this.#org, 'jobs'))).jobs;
		} catch {
			found = this.jobs;
		}
		const before = new Map(this.jobs.map((j) => [j.id, j.state]));
		this.jobs = found;
		if (found.some((j) => j.state === 'finished' && before.get(j.id) === 'running')) {
			await invalidateAll();
		}
		this.#schedule();
	}

	add(job: DatasetJob) {
		this.jobs = [job, ...this.jobs.filter((j) => j.id !== job.id)];
		this.#schedule();
	}

	async cancel(id: number) {
		const job = await datasetCall<DatasetJob>(datasetsApi(this.#org, `jobs/${id}`), {
			method: 'DELETE'
		});
		this.jobs = this.jobs.map((j) => (j.id === id ? job : j));
		this.#schedule();
	}

	hide(id: number) {
		this.#hidden = new Set([...this.#hidden, id]);
		try {
			localStorage.setItem(HIDDEN, JSON.stringify([...this.#hidden].slice(-200)));
		} catch {
			// Hidden for this page only.
		}
	}

	#schedule() {
		if (this.#stopped || this.#timer) return;
		if (!this.jobs.some((j) => j.state === 'running')) return;
		this.#timer = setTimeout(() => {
			this.#timer = null;
			void this.refresh();
		}, POLL);
	}
}
