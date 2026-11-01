<script lang="ts">
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
     reads on the muted row that marks the version shown. -->
<svelte:element
	this={as}
	class={[
		'inline-flex h-6 max-w-full min-w-0 items-center gap-1 rounded-full bg-primary/10 text-xs text-primary ring-1 ring-primary/25 ring-inset dark:bg-primary/15',
		onremove ? 'pr-1 pl-2' : 'px-2'
	]}
>
	<span class="truncate" title={tag}>{tag}</span>
	{#if onremove}
		<button
			class="grid size-4 flex-none place-items-center rounded-full text-primary/75 hover:bg-primary/15 hover:text-primary"
			aria-label="Remove the tag {tag}"
			onclick={onremove}
		>
			<XIcon class="size-3" />
		</button>
	{/if}
</svelte:element>
