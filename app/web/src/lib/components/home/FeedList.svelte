<script lang="ts">
	import { Button } from '#lib/components/ui/button/index.js';
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
</script>

<ul class="mt-4 border-t border-divider">
	{#each pager.items as item (item.id)}
		<FeedEntry {item} {now} />
	{:else}
		<li class="border-b border-divider py-6 text-muted-foreground">{empty}</li>
	{/each}
</ul>
{#if pager.next}
	<div class="mt-4 flex items-center gap-3">
		<Button variant="outline" onclick={() => pager.more()} disabled={pager.loading}>
			{pager.loading ? 'Loading…' : 'Show more'}
		</Button>
		{#if pager.failed}<span class="text-[13px] text-destructive">Couldn’t load more</span>{/if}
	</div>
{/if}
