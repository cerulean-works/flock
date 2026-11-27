<script lang="ts">
	import type { Snippet } from 'svelte';

	let { label, children }: { label?: string; children: Snippet } = $props();
</script>

<!-- A group of Property rows in a properties pane; groups stand apart by a rule. -->
<section
	class="border-divider not-first-of-type:mt-3 not-first-of-type:border-t not-first-of-type:pt-3"
	aria-label={label}
>
	{@render children()}
</section>
