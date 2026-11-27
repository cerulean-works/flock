<script lang="ts">
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';

	interface Props {
		/** The muted label at the left. */
		label: string;
		/** Monospace, for keys, digests and other machine names. */
		mono?: boolean;
		/** The value under the label at the pane's full width: a list, a long path. */
		stack?: boolean;
		class?: string;
		children: Snippet;
	}

	let { label, mono = false, stack = false, class: className, children }: Props = $props();
</script>

<!-- One row of a properties pane: a muted label, the value beside it, both 13px. A long value
     wraps under itself, never under the label. -->
<dl
	class={cn(
		'flex py-1.5 text-[13px]',
		stack ? 'flex-col gap-1.5' : 'min-h-8 items-baseline gap-3',
		className
	)}
>
	<dt class={['flex-none text-muted-foreground', !stack && 'w-24']}>{label}</dt>
	<dd class={['min-w-0 flex-1 break-words tabular-nums', mono && 'font-mono text-[12px]']}>
		{@render children()}
	</dd>
</dl>
