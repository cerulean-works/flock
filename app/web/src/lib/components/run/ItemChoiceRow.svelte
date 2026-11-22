<script lang="ts">
	import CheckIcon from '@lucide/svelte/icons/check';
	import type { EvalItem } from '#lib/types.ts';

	/**
	 * One choice of an opened eval item, a row in the items table's own columns (027): the choice
	 * as scored, the gold mark, and each run's share bar and numbers for it. Its own component so
	 * per-choice detail (per-token values, later) has one place to grow.
	 */
	interface Props {
		item: EvalItem;
		/** The choice's index. */
		choice: number;
		/** Each run's column heading, in order. */
		sides: string[];
		/** Whether the table has a Δ column, left empty here. */
		delta: boolean;
	}

	let { item, choice, sides, delta }: Props = $props();

	const percent = (v: number) => `${(100 * v).toFixed(1)}%`;
</script>

<!-- The item's last choice closes it with a rule. -->
<tr class={['align-top', choice === item.choices.length - 1 && 'border-b border-divider']}>
	<td></td>
	<th scope="row" class="px-2 py-1 text-left font-normal">
		<!-- Text as scored, whitespace included. A choice with its own context
			     (Winogrande) reads after it. -->
		<span class="whitespace-pre-wrap"
			>{#if item.contexts}<span class="text-muted-foreground">{item.contexts[choice]}</span
				>{/if}{item.choices[choice]}</span
		>
	</th>
	<td class="px-2 py-1">
		{#if choice === item.gold}
			<span
				class="inline-flex items-center gap-0.5 rounded bg-secondary px-1 text-[11px] text-muted-foreground"
			>
				<CheckIcon class="size-3" aria-hidden="true" />gold
			</span>
		{/if}
	</td>
	{#each item.runs as answer, side (side)}
		{@const picked = answer.pick === choice}
		<!-- The run's numbers for this choice, short enough for its column. -->
		{@const said = [
			answer.share ? `share ${percent(answer.share[choice])}` : null,
			`prob ${percent(answer.prob[choice])}`,
			`logprob ${answer.logprob[choice].toFixed(2)}`
		]
			.filter(Boolean)
			.join(' · ')}
		<!-- The run's pick: its bar solid and its numbers dark, the others lighter.
			     Right or wrong is the row's own icon, above. -->
		<td class="px-2 py-1" aria-label="{sides[side]}{picked ? ' (its pick)' : ''}: {said}">
			<!-- The share as a meter (reading blue, design.md): near even under a
				     length-normalized metric, as it is. `acc_greedy` has none. -->
			{#if answer.share}
				<span class="block h-1.5 rounded-full bg-muted" aria-hidden="true">
					<span
						class={['block h-full rounded-full', picked ? 'bg-primary' : 'bg-primary/35']}
						style:width={percent(answer.share[choice])}
					></span>
				</span>
			{/if}
			<span
				class={['mt-1 block text-[11px]', picked ? 'text-foreground' : 'text-muted-foreground']}
				aria-hidden="true">{said}</span
			>
		</td>
	{/each}
	{#if delta}<td></td>{/if}
	<td></td>
</tr>
