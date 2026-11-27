<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import XIcon from '@lucide/svelte/icons/x';

	interface Props {
		tag: string;
		/** Shows a remove button when given. */
		onremove?: () => void;
		/** `li` inside a tag list, `span` elsewhere. */
		as?: 'li' | 'span';
	}

	let { tag, onremove, as = 'span' }: Props = $props();
</script>

<!-- A tinted pill in reading blue (design.md's state badges), with an inset ring so it still
     reads on the muted row that marks the version shown. Its × shows on hover (D5), always on
     a touch screen. -->
<svelte:element
	this={as}
	class={[
		'group/tag inline-flex h-6 max-w-full min-w-0 items-center gap-1 rounded-full bg-primary/10 text-xs text-primary ring-1 ring-primary/25 ring-inset dark:bg-primary/15',
		onremove ? 'pr-1 pl-2' : 'px-2'
	]}
>
	<span class="truncate" {@attach hint(tag)}>{tag}</span>
	{#if onremove}
		<button
			class="grid size-4 flex-none place-items-center rounded-full text-primary/75 opacity-0 group-hover/tag:opacity-100 hover:bg-primary/15 hover:text-primary focus-visible:opacity-100 pointer-coarse:opacity-100"
			aria-label="Remove the tag {tag}"
			onclick={onremove}
		>
			<XIcon class="size-3" />
		</button>
	{/if}
</svelte:element>
