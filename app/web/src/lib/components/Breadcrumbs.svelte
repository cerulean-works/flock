<script lang="ts">
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import type { Crumb } from '#lib/storage.ts';

	interface Props {
		crumbs: Crumb[];
	}

	let { crumbs }: Props = $props();

	const LINK =
		'rounded-sm underline-offset-3 outline-none hover:text-link hover:underline focus-visible:ring-3 focus-visible:ring-ring/50';
</script>

<!--
	Where a page is: every step a link, the last one the page itself. The same trail on every
	page, whether above a title or as a workspace header's subtitle.
-->
<nav aria-label="Breadcrumbs" class="min-w-0">
	<ol class="flex min-w-0 items-center gap-1 text-[13px] text-muted-foreground">
		{#each crumbs as crumb, i (i)}
			{@const last = i === crumbs.length - 1}
			{#if i}<li aria-hidden="true" class="flex-none">
					<ChevronRightIcon class="size-3.5" />
				</li>{/if}
			<li class={['min-w-0', last ? 'truncate' : 'flex-none']}>
				<a
					href={crumb.href}
					class={[LINK, last && 'text-foreground']}
					aria-current={last ? 'page' : undefined}
				>
					{crumb.label}
				</a>
			</li>
		{/each}
	</ol>
</nav>
