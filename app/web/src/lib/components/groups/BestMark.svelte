<script lang="ts">
	import TrophyIcon from '@lucide/svelte/icons/trophy';
	import { hint } from '#lib/hint.svelte.ts';
	import type { BestOrder } from '#lib/groups.ts';

	/**
	 * The mark on the header of the column Best by reads (D217): a trophy and which way is best,
	 * ↓ lowest or ↑ highest. Changed from the header's menu; this only says so.
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
	class="inline-flex flex-none items-center gap-px text-foreground"
	role="img"
	aria-label={text}
	{@attach hint(text)}
>
	<TrophyIcon class="size-3" />
	<span class="text-[11px] leading-none" aria-hidden="true">{order === 'desc' ? '↑' : '↓'}</span>
</span>
