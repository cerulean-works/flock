<script lang="ts">
	import CopyButton from './CopyButton.svelte';

	let { code }: { code: string } = $props();
</script>

<!-- min-w-0: in a grid, a long line scrolls rather than widening the column. -->
<div class="relative min-w-0">
	<pre
		class="overflow-x-auto rounded-lg bg-muted py-3 pr-12 pl-4 font-mono text-[13px] leading-relaxed"><code
			>{code}</code
		></pre>
	<CopyButton text={code} class="absolute top-2 right-2" />
</div>
