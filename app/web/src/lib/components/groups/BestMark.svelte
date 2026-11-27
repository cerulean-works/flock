<script lang="ts">
	import TrophyIcon from '@lucide/svelte/icons/trophy';
	import { hint } from '#lib/hint.svelte.ts';
	import type { BestOrder } from '#lib/groups.ts';

	/**
	 * The mark on the header of the column Best by reads (D217): a trophy alone, so it is never
	 * read as the sort's arrow. Which way is best is in its hint and the header menu's ✓.
	 */

	interface Props {
		order: BestOrder | null;
		/** Whose setting it is (the group's own or the project's), for the hint. */
		source?: string;
	}

	let { order, source }: Props = $props();

	const text = $derived(
		`Best is the ${order === 'desc' ? 'highest' : 'lowest'}${source ? ` (${source})` : ''}`
	);
</script>

<span
	class="inline-flex flex-none items-center text-foreground"
	role="img"
	aria-label={text}
	{@attach hint(text)}
>
	<TrophyIcon class="size-3" />
</span>
