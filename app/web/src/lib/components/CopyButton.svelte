<script lang="ts">
	import CheckIcon from '@lucide/svelte/icons/check';
	import CopyIcon from '@lucide/svelte/icons/copy';
	import type { ClassValue } from 'svelte/elements';
	import { Button } from '#lib/components/ui/button/index.js';

	interface Props {
		/** What goes on the clipboard. */
		text: string;
		/** The button's name, e.g. "Copy ID". */
		label?: string;
		class?: ClassValue;
		/** Once the text is on the clipboard. */
		oncopied?: () => void;
	}

	let { text, label = 'Copy', class: className, oncopied }: Props = $props();

	let copied = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;

	async function copy() {
		await navigator.clipboard.writeText(text);
		copied = true;
		clearTimeout(timer);
		timer = setTimeout(() => (copied = false), 1500);
		oncopied?.();
	}
</script>

<Button
	variant="ghost"
	size="icon-sm"
	class={['text-muted-foreground', className]}
	aria-label={copied ? 'Copied' : label}
	title={copied ? 'Copied' : label}
	onclick={copy}
>
	{#if copied}<CheckIcon />{:else}<CopyIcon />{/if}
</Button>
