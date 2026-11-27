<script lang="ts">
	import BlendIcon from '@lucide/svelte/icons/blend';
	import GitForkIcon from '@lucide/svelte/icons/git-fork';
	import { hint } from '#lib/hint.svelte.ts';

	/**
	 * Where a dataset came from, as an icon after its name: a blend or a fork, the icons their
	 * actions use. The word is in its hint and for screen readers.
	 */
	let { origin }: { origin: 'fork' | 'blend' } = $props();

	const label = $derived(origin === 'blend' ? 'Blend' : 'Fork');
</script>

<span
	class="grid size-4 flex-none place-items-center text-muted-foreground [&_svg]:size-3.5"
	{@attach hint(label)}
>
	{#if origin === 'blend'}<BlendIcon aria-hidden="true" />{:else}<GitForkIcon
			aria-hidden="true"
		/>{/if}
	<span class="sr-only">{label}</span>
</span>
