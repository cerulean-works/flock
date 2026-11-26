import type { ProjectEvent } from '#lib/data/live.ts';

/**
 * Live updates for the group pages (D228), from the project's event stream (D129), which the
 * workspace's `LiveConnection` reads too. These pages hold no curves to grow: an event only
 * means their values or counts may have changed (a member's summary, a run created, deleted or
 * moved away), so they are read again, at most once every `wait` ms and not while a menu or
 * popover is open. A new matching run is offered by the next read's counts, never added (D216).
 *
 * The stream watches `runs` without replaying their histories (no line counts): only their
 * `run` events (state, summary) are wanted. Created and deleted runs reach every subscriber.
 */

const RETRY_MS = [500, 1000, 2000, 5000];

const OPEN_LAYER =
	'[data-slot="popover-content"], [data-slot="dropdown-menu-content"], [data-slot="select-content"]';

export class GroupsLive {
	connection = $state<'connecting' | 'live' | 'offline'>('connecting');
	#source: EventSource | null = null;
	#retries = 0;
	#retry: ReturnType<typeof setTimeout> | undefined;
	#reload: ReturnType<typeof setTimeout> | undefined;
	#url = '';
	#onchange: () => void;
	#onproject: (event: ProjectEvent) => void;
	#wait: number;

	constructor(onchange: () => void, onproject: (event: ProjectEvent) => void, wait = 3000) {
		this.#onchange = onchange;
		this.#onproject = onproject;
		this.#wait = wait;
	}

	/** Watch the project, and the `runs` whose values the page shows. */
	connect(entity: string, project: string, runs: readonly string[]) {
		this.close();
		const base = `/api/projects/${encodeURIComponent(entity)}/${encodeURIComponent(project)}`;
		this.#url = `${base}/live?runs=${encodeURIComponent(runs.join(','))}`;
		this.#open();
	}

	close() {
		clearTimeout(this.#retry);
		clearTimeout(this.#reload);
		this.#reload = undefined;
		this.#source?.close();
		this.#source = null;
	}

	#open() {
		const source = new EventSource(this.#url);
		this.#source = source;
		this.connection = 'connecting';
		source.addEventListener('hello', () => {
			this.#retries = 0;
			this.connection = 'live';
		});
		for (const name of ['run', 'created', 'deleted'])
			source.addEventListener(name, () => this.#changed());
		source.addEventListener('project', (e) => {
			this.close();
			this.#onproject(JSON.parse((e as MessageEvent).data));
		});
		source.onerror = () => {
			source.close();
			if (this.#source !== source) return;
			this.#source = null;
			this.connection = 'offline';
			const delay = RETRY_MS[Math.min(this.#retries++, RETRY_MS.length - 1)];
			this.#retry = setTimeout(() => this.#open(), delay);
		};
	}

	#changed() {
		if (this.#reload) return;
		const reload = () => {
			if (document.querySelector(OPEN_LAYER)) {
				this.#reload = setTimeout(reload, this.#wait);
				return;
			}
			this.#reload = undefined;
			this.#onchange();
		};
		this.#reload = setTimeout(reload, this.#wait);
	}
}
