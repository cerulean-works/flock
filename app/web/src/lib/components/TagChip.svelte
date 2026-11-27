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

	/** The tag's dot: one of the chart hues, the same for a tag everywhere. */
	const DOTS = ['bg-chart-1', 'bg-chart-2', 'bg-chart-3', 'bg-chart-4', 'bg-chart-5'];
	const dot = $derived(
		DOTS[[...tag].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % DOTS.length]
	);
</script>

<!-- A neutral pill with a small colour dot (Linear's labels), with an inset ring so it still
     reads on the muted row that marks the version shown. Its × takes no room until hover or
     focus (D5), and shows always on a touch screen. -->
<svelte:element
	this={as}
	class={[
		'group/tag inline-flex h-6 max-w-full min-w-0 items-center gap-1 rounded-full bg-muted text-xs text-foreground ring-1 ring-border ring-inset',
		onremove ? 'px-2 group-focus-within/tag:pr-1 group-hover/tag:pr-1 pointer-coarse:pr-1' : 'px-2'
	]}
>
	<span class={['size-1.5 flex-none rounded-full', dot]} aria-hidden="true"></span>
	<span class="truncate" {@attach hint(tag)}>{tag}</span>
	{#if onremove}
		<button
			class="-ml-1 grid h-4 w-0 flex-none place-items-center overflow-hidden rounded-full text-muted-foreground group-focus-within/tag:ml-0 group-focus-within/tag:w-4 group-hover/tag:ml-0 group-hover/tag:w-4 hover:bg-foreground/10 hover:text-foreground pointer-coarse:ml-0 pointer-coarse:w-4"
			aria-label="Remove the tag {tag}"
			onclick={onremove}
		>
			<XIcon class="size-3" />
		</button>
	{/if}
</svelte:element>
