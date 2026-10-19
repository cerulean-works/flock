<script lang="ts">
	import CheckIcon from '@lucide/svelte/icons/check';
	import CopyIcon from '@lucide/svelte/icons/copy';
	import { Button } from '#lib/components/ui/button/index.js';

	let { code }: { code: string } = $props();

	let copied = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;

	async function copy() {
		await navigator.clipboard.writeText(code);
		copied = true;
		clearTimeout(timer);
		timer = setTimeout(() => (copied = false), 1500);
	}
</script>

<!-- min-w-0: in a grid, a long line scrolls rather than widening the column. -->
<div class="relative min-w-0">
	<pre
		class="overflow-x-auto rounded-lg bg-muted py-3 pr-12 pl-4 font-mono text-[13px] leading-relaxed"><code
			>{code}</code
		></pre>
	<Button
		variant="ghost"
		size="icon-sm"
		class="absolute top-2 right-2 text-muted-foreground"
		aria-label={copied ? 'Copied' : 'Copy'}
		title={copied ? 'Copied' : 'Copy'}
		onclick={copy}
	>
		{#if copied}<CheckIcon />{:else}<CopyIcon />{/if}
	</Button>
</div>
