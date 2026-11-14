/* eslint-disable svelte/prefer-svelte-reactivity -- the sets and maps here are replaced, never changed in place */
import { invalidateAll } from '$app/navigation';
import { hfApi, hfCall } from './hf.ts';
import type { HFImport } from './types.ts';

/** How often running imports are asked about, in milliseconds. */
const POLL = 2000;
/** How long an import that ended keeps its row, in seconds. */
const KEEP = 86_400;
const HIDDEN = 'flock-hf-hidden';

function hidden(): Set<number> {
	try {
		return new Set(JSON.parse(localStorage.getItem(HIDDEN) ?? '[]') as number[]);
	} catch {
		return new Set();
	}
}

/**
 * The org's Hugging Face imports, followed while any runs. When one finishes the page reads its
 * data again, so the new dataset or model shows up. Rows of imports that ended can be hidden,
 * which this browser remembers.
 */
export class HFImportList {
	imports = $state<HFImport[]>([]);
	#hidden = $state(new Set<number>());
	#org: string;
	#timer: ReturnType<typeof setTimeout> | null = null;
	#stopped = true;

	constructor(org: string) {
		this.#org = org;
	}

	/** The imports to show: running ones, and ones that ended in the last day and weren't hidden. */
	get shown(): HFImport[] {
		const now = Date.now() / 1000;
		return this.imports.filter(
			(i) =>
				i.state === 'running' ||
				(!this.#hidden.has(i.id) && now - (i.finished_at ?? i.started_at) < KEEP)
		);
	}

	/** Start following the list; returns what stops it. */
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
		let found: HFImport[];
		try {
			found = (await hfCall<{ imports: HFImport[] }>(hfApi(this.#org))).imports;
		} catch {
			found = this.imports; // asked again on the next round
		}
		const before = new Map(this.imports.map((i) => [i.id, i.state]));
		this.imports = found;
		if (found.some((i) => i.state === 'finished' && before.get(i.id) === 'running')) {
			await invalidateAll();
		}
		this.#schedule();
	}

	/** An import just started here, shown before the next round. */
	add(job: HFImport) {
		this.imports = [job, ...this.imports.filter((i) => i.id !== job.id)];
		this.#schedule();
	}

	async cancel(id: number) {
		const job = await hfCall<HFImport>(hfApi(this.#org, String(id)), { method: 'DELETE' });
		this.imports = this.imports.map((i) => (i.id === id ? job : i));
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
		if (!this.imports.some((i) => i.state === 'running')) return;
		this.#timer = setTimeout(() => {
			this.#timer = null;
			void this.refresh();
		}, POLL);
	}
}
