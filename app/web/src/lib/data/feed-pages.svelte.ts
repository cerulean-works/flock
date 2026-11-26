import type { FeedItem } from '#lib/types.ts';
import type { FeedPager } from './feed.svelte.ts';

/**
 * The feed shown a page at a time, one row per run: a run's newest event (started, then
 * finished or failed) stands for it, with its latest state. New projects keep their own rows.
 * Paging past what's loaded fetches more (the feed is cursor-paged).
 */
export class FeedPages {
	#pager: FeedPager;
	#size: number;
	#only: ((item: FeedItem) => boolean) | undefined;
	index = $state(0);

	constructor(pager: FeedPager, size: number, only?: (item: FeedItem) => boolean) {
		this.#pager = pager;
		this.#size = size;
		this.#only = only;
	}

	rows = $derived.by(() => {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- local to this computation
		const seen = new Set<string>();
		const out: FeedItem[] = [];
		for (const item of this.#pager.items) {
			if (this.#only && !this.#only(item)) continue;
			const key = item.run ? `${item.org}/${item.project}/${item.run.id}` : item.id;
			if (seen.has(key)) continue;
			seen.add(key);
			out.push(item);
		}
		return out;
	});

	get shown() {
		return this.rows.slice(this.index * this.#size, (this.index + 1) * this.#size);
	}

	get from() {
		return this.index * this.#size + 1;
	}

	/** All of them once the feed has none left to load; "many" until then. */
	get total() {
		return this.#pager.next ? 'many' : String(this.rows.length);
	}

	/**
	 * Loads until the page in view is full (one run's events merge into one row, so a fetched
	 * page of events can come out short), or there's nothing left.
	 */
	fill = async () => {
		while (
			this.shown.length < this.#size &&
			this.#pager.next &&
			!this.#pager.loading &&
			!this.#pager.failed
		)
			await this.#pager.more();
	};

	get hasPrev() {
		return this.index > 0;
	}

	get hasNext() {
		return this.rows.length > (this.index + 1) * this.#size || !!this.#pager.next;
	}

	prev = () => {
		if (this.index > 0) this.index -= 1;
	};

	next = async () => {
		const want = (this.index + 2) * this.#size;
		while (this.rows.length < want && this.#pager.next && !this.#pager.failed)
			await this.#pager.more();
		if (this.rows.length > (this.index + 1) * this.#size) this.index += 1;
	};
}
