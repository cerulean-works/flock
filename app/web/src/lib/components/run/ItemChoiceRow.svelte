<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import CheckIcon from '@lucide/svelte/icons/check';
	import InfoHint from '#lib/components/InfoHint.svelte';
	import type { EvalItem, ItemTokens, TokenHeat } from '#lib/types.ts';

	/**
	 * One choice of an opened eval item, a row in the items table's own columns (027): the choice
	 * as scored, the gold mark, and each run's share bar and numbers for it, then its tokens as
	 * that run cut them, tinted by `heat` (030).
	 */
	interface Props {
		item: EvalItem;
		/** The choice's index. */
		choice: number;
		/** Each run's column heading, in order. */
		sides: string[];
		/** Whether the table has a Δ column, left empty here. */
		delta: boolean;
		/** Each run's per-token values over the item's choices, once Flock sends them. */
		tokens?: ItemTokens | null;
		heat?: TokenHeat;
		/** Why each run has no tokens, if it has none, said on the first choice's row. */
		notes?: (string | null)[];
	}

	let {
		item,
		choice,
		sides,
		delta,
		tokens = null,
		heat = 'surprisal',
		notes = []
	}: Props = $props();

	// A token's tint, its color token and strength from 0 to 1. The scales are fixed, so one doc
	// reads like the next: 8 nats of surprisal, rank 1,024 and 6 nats of entropy are full. Two runs
	// are compared where they cut the choice into the same tokens, full at 3 nats apart.
	function tint(side: number, i: number): [string, number] {
		const mine = tokens?.runs[side]?.choices?.[choice];
		const theirs = tokens?.runs[1 - side]?.choices?.[choice];
		if (!mine) return ['--primary', 0];
		if (heat === 'surprisal') return ['--primary', Math.min(-mine.logprob[i] / 8, 1)];
		if (heat === 'rank') return ['--primary', Math.min(Math.log2(mine.rank[i]) / 10, 1)];
		if (heat === 'entropy') return ['--primary', Math.min(mine.entropy[i] / 6, 1)];
		// Equal pieces are the same tokens only when none is empty: an empty piece ends inside a
		// character, and two tokenizers can split that character at different bytes.
		const same =
			mine.text &&
			theirs?.text &&
			!mine.text.includes('') &&
			mine.text.join('\0') === theirs.text.join('\0');
		if (!same || !theirs) return ['--primary', 0];
		const gap = mine.logprob[i] - theirs.logprob[i];
		return [gap >= 0 ? '--state-finished' : '--destructive', Math.min(Math.abs(gap) / 3, 1)];
	}

	const percent = (v: number) => `${(100 * v).toFixed(1)}%`;
</script>

<!-- The item's last choice closes it with a rule. -->
<tr class={['align-top', choice === item.choices.length - 1 && 'border-b border-divider']}>
	<td></td>
	<th scope="row" class="px-3 py-1 text-left font-normal">
		<!-- Text as scored, whitespace included. A choice with its own context
			     (Winogrande) reads after it. -->
		<span class="whitespace-pre-wrap"
			>{#if item.contexts}<span class="text-muted-foreground">{item.contexts[choice]}</span
				>{/if}{item.choices[choice]}</span
		>
	</th>
	<td class="px-3 py-1">
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
		{@const own = tokens?.runs[side]?.choices?.[choice]}
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
		<td class="px-3 py-1" aria-label="{sides[side]}{picked ? ' (its pick)' : ''}: {said}">
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
			{#if choice === 0 && notes[side]}
				<!-- No tokens for this run: why, behind an icon. -->
				<span class="mt-1 flex text-muted-foreground">
					<InfoHint label="Why {sides[side]} has no tokens">{notes[side]}</InfoHint>
				</span>
			{/if}
			{#if own}
				<!-- The tokens as this run cut the choice; a dot where its tokenizer couldn't name one.
				     Hover one for its numbers. -->
				<span class="mt-1 flex flex-wrap gap-px font-mono text-[11px]">
					{#each own.logprob as logprob, i (i)}
						{@const [tone, strength] = tint(side, i)}
						<span
							class="rounded-sm px-0.5 whitespace-pre"
							style:background-color="color-mix(in srgb, var({tone}) {Math.round(60 * strength)}%,
							transparent)"
							{@attach hint(
								`log p ${logprob.toFixed(2)} · rank ${own.rank[i]} · entropy ${own.entropy[
									i
								].toFixed(2)}`
							)}>{own.text?.[i].replaceAll('\n', '↵') ?? '·'}</span
						>
					{/each}
				</span>
			{/if}
		</td>
	{/each}
	{#if delta}<td></td>{/if}
	<td></td>
</tr>
