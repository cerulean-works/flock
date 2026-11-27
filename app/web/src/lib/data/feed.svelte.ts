import { invalidate } from '$app/navigation';
import type { FeedItem, FeedPage } from '#lib/types.ts';

/** What feed pages depend on: polling invalidates it. */
export const FEED = 'flock:feed';

/**
 * The runs table's State filter (`?state=`): runs that ended so, as the feed kinds that say it.
 * A run still going has no kind of its own, so there's no "Running" here.
 */
export const RUN_STATES = [
	{ value: 'finished', label: 'Finished', kinds: 'run_finished' },
	{ value: 'failed', label: 'Failed or crashed', kinds: 'run_failed,run_crashed' }
] as const;

/** The org runs list's column filters, each a comma list in the URL: `?owner=me,12&state=failed`. */
export const RUN_FILTERS = ['owner', 'project', 'state', 'group', 'job_type', 'gpu'] as const;
export type RunFilters = Record<(typeof RUN_FILTERS)[number], string[]>;

/** A `?state=` the table offers, else null for every run. */
export const runStateKinds = (param: string | null) =>
	RUN_STATES.find((s) => s.value === param) ?? null;

/** The feed isn't pushed: re-run the page's load this often while the tab is visible. */
export function pollFeed(ms = 30_000) {
	const id = setInterval(() => {
		if (document.visibilityState === 'visible') invalidate(FEED);
	}, ms);
	return () => clearInterval(id);
}

/**
 * A feed's first page (from the page load, refreshed by polling) followed by the pages "Show
 * more" fetched. Once more is shown, the first page as it was then is kept too: new items push
 * the newest page's tail out, and the next page's cursor continues from where it ended.
 */
export class FeedPager {
	#first: () => FeedPage;
	#query: Record<string, string>;
	#kept = $state<FeedItem[]>([]);
	#more = $state<FeedItem[]>([]);
	/** Undefined until a page past the first is fetched. */
	#cursor = $state<string | null | undefined>(undefined);
	loading = $state(false);
	failed = $state(false);

	constructor(first: () => FeedPage, query: Record<string, string>) {
		this.#first = first;
		this.#query = query;
	}

	#items = $derived.by(() => {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- local to this computation
		const seen = new Set<string>();
		return [...this.#first().items, ...this.#kept, ...this.#more]
			.filter((item) => !seen.has(item.id) && !!seen.add(item.id))
			.sort((a, b) => b.at - a.at);
	});

	get items() {
		return this.#items;
	}

	get next() {
		return this.#cursor === undefined ? this.#first().next : this.#cursor;
	}

	async more() {
		const cursor = this.next;
		if (!cursor || this.loading) return;
		this.loading = true;
		this.failed = false;
		try {
			const search = new URLSearchParams({ ...this.#query, cursor }); // eslint-disable-line svelte/prefer-svelte-reactivity
			const res = await fetch(`/api/feed?${search}`);
			if (!res.ok) throw new Error(`feed responded ${res.status}`);
			const page = (await res.json()) as FeedPage;
			if (this.#cursor === undefined) this.#kept = this.#first().items;
			this.#more = [...this.#more, ...page.items];
			this.#cursor = page.next;
		} catch {
			this.failed = true;
		} finally {
			this.loading = false;
		}
	}
}
