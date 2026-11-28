<script lang="ts">
	/** A pinned project's folder: Lucide's outline folder with a small star in it, like a sticker. */
	let { filled = false, class: className }: { filled?: boolean; class?: string } = $props();
</script>

<svg
	xmlns="http://www.w3.org/2000/svg"
	viewBox="0 0 24 24"
	fill="none"
	stroke="currentColor"
	stroke-width="2"
	stroke-linecap="round"
	stroke-linejoin="round"
	class={className}
	aria-hidden="true"
>
	<path
		d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"
	/>
	<path
		d="M12 9.5 13.03 11.98 15.71 12.19 13.66 13.94 14.29 16.56 12 15.15 9.71 16.56 10.34 13.94 8.29 12.19 10.97 11.98Z"
		stroke-width="1.5"
		fill={filled ? 'currentColor' : 'none'}
	/>
</svg>
