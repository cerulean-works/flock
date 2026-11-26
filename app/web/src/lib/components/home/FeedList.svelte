<script lang="ts">
	import InlinePager from '#lib/components/InlinePager.svelte';
	import { FeedPages } from '#lib/data/feed-pages.svelte.ts';
	import { FeedPager } from '#lib/data/feed.svelte.ts';
	import type { FeedPage } from '#lib/types.ts';
	import FeedEntry from './FeedEntry.svelte';

	interface Props {
		/** The newest page, from the page load (polling refreshes it). */
		first: () => FeedPage;
		/** The feed query the page loaded with; later pages add a cursor. */
		query: Record<string, string>;
		now: number;
		empty: string;
	}

	let { first, query, now, empty }: Props = $props();

	// svelte-ignore state_referenced_locally
	const pager = new FeedPager(first, query);
	const pages = new FeedPages(pager, 25);
</script>

<ul class="border-t border-divider">
	{#each pages.shown as item (item.id)}
		<FeedEntry {item} {now} />
	{:else}
		<li class="border-b border-divider py-6 text-[13px] text-muted-foreground">{empty}</li>
	{/each}
</ul>
{#if pages.hasPrev || pages.hasNext}
	<div class="mt-3">
		<InlinePager
			label="Activity pages"
			from={pages.from}
			to={pages.from + pages.shown.length - 1}
			total={pages.total}
			onprev={pages.hasPrev ? pages.prev : undefined}
			onnext={pages.hasNext ? pages.next : undefined}
			pending={pager.loading}
			failed={pager.failed}
		/>
	</div>
{/if}
