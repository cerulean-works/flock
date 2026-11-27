<script lang="ts">
	/**
	 * Keys to press, as keycaps: "G then R" is two caps with a muted "then" between, "⌘ K" two
	 * caps side by side. Hints, the shortcut sheet and the command menu all write keys with it.
	 */
	interface Props {
		/** Space-separated keys; `then` between the steps of a sequence. "G R" reads as G then R. */
		keys: string;
		/** `sm` in a hint or a list row, `md` in the shortcut sheet. */
		size?: 'sm' | 'md';
		class?: string;
	}

	let { keys, size = 'sm', class: className }: Props = $props();

	const parts = $derived.by(() => {
		const ks = keys.trim().split(/\s+/);
		// A bare sequence of single letters ("G R") is a sequence: say so.
		if (!ks.includes('then') && ks.length === 2 && ks.every((k) => /^[A-Z,]$/.test(k)))
			return [ks[0], 'then', ks[1]];
		return ks;
	});
</script>

<span class={['inline-flex flex-none items-center gap-1', className]}>
	{#each parts as k, i (i)}
		{#if k === 'then'}
			<span class="text-[11px] text-muted-foreground">then</span>
		{:else}
			<kbd
				class={[
					'grid place-items-center border border-border px-1 font-sans text-[11px] leading-none text-muted-foreground',
					size === 'sm' ? 'h-4 min-w-4 rounded-[4px]' : 'h-5 min-w-5 rounded'
				]}>{k}</kbd
			>
		{/if}
	{/each}
</span>
