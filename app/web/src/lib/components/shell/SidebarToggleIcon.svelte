<script lang="ts">
	import { onMount, untrack } from 'svelte';

	/** Whether the sidebar is open: the chevron points the way a click takes it. */
	let { open }: { open: boolean } = $props();

	// It arrives showing the state just left, then turns to this one: the click lands visibly.
	let shown = $state(untrack(() => !open));
	let ready = false;
	onMount(() => {
		const id = requestAnimationFrame(() => {
			ready = true;
			shown = open;
		});
		return () => cancelAnimationFrame(id);
	});
	$effect(() => {
		const now = open;
		if (ready) shown = now;
	});
</script>

<!-- Lucide's panel-left, with its divider and chevron moving: on hover the divider leans the
     way the sidebar would go (a Linear detail). -->
<svg
	xmlns="http://www.w3.org/2000/svg"
	viewBox="0 0 24 24"
	fill="none"
	stroke="currentColor"
	stroke-width="2"
	stroke-linecap="round"
	stroke-linejoin="round"
	class="sb-toggle"
	data-open={shown || undefined}
	aria-hidden="true"
>
	<rect width="18" height="18" x="3" y="3" rx="2" />
	<path class="divider" d="M9 3v18" />
	<path class="chevron" d="m13 9 3 3-3 3" />
</svg>

<style>
	.divider,
	.chevron {
		transition: transform 200ms cubic-bezier(0.25, 0.8, 0.3, 1);
		transform-box: view-box;
	}
	.chevron {
		transform-origin: 14.5px 12px;
	}
	/* Open: the chevron points left, to close. */
	[data-open] .chevron {
		transform: rotate(180deg);
	}
	:global(:hover) > .sb-toggle .divider {
		transform: translateX(2px);
	}
	:global(:hover) > .sb-toggle[data-open] .divider {
		transform: translateX(-2px);
	}
	@media (prefers-reduced-motion: reduce) {
		.divider,
		.chevron {
			transition: none;
		}
	}
</style>
