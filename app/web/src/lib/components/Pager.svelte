<script lang="ts" module>
	/** The floating pager's pill, on the runs table and under Home's feed alike. */
	export const PAGER_PILL =
		'flex h-10 items-center gap-1 rounded-xl bg-popover py-1 pr-1 pl-3.5 text-[13px] tabular-nums shadow-md ring-1 ring-foreground/10';
</script>

<script lang="ts">
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { Button } from '#lib/components/ui/button/index.js';
	import type { RunQuery } from '#lib/types.ts';
	import { applyRunQuery, PAGE_SIZES } from '#lib/workspace/url.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import PageSizeMenu from './PageSizeMenu.svelte';

	const ws = getWorkspace();
	const q = $derived(ws.page.query);
	const pages = $derived(Math.max(1, Math.ceil(ws.page.total / ws.meta.pageSize)));
	const first = $derived((q.page - 1) * ws.meta.pageSize);

	function href(changes: Partial<RunQuery>) {
		const params = new URLSearchParams(page.url.search);
		applyRunQuery(params, { ...q, ...changes });
		return ws.href(params);
	}

	/** A new page size keeps the first run shown on screen: its page at the new size. */
	function resize(size: number) {
		void goto(href({ size, page: Math.floor(first / size) + 1 }), { reset: false });
	}
</script>

<!-- Only when there's more than one page. Links preload on hover (SvelteKit) on top of the idle prefetch of both neighbours. -->
{#if pages > 1}<nav
		class={['pager absolute bottom-[var(--pager-bottom,0.875rem)] left-4 z-5', PAGER_PILL]}
		aria-label="Run pages"
	>
		<span>{first + 1}–{first + ws.page.runs.length}</span>
		<span class="of mr-1 text-muted-foreground">of {ws.page.total}</span>
		<Button
			variant="ghost"
			size="icon-sm"
			href={href({ page: q.page - 1 })}
			disabled={q.page <= 1}
			class="aria-disabled:pointer-events-none aria-disabled:opacity-40"
			aria-label="Previous page"
			data-sveltekit-preload-data="hover"
		>
			<ChevronLeftIcon />
		</Button>
		<Button
			variant="ghost"
			size="icon-sm"
			href={href({ page: q.page + 1 })}
			disabled={q.page >= pages}
			class="aria-disabled:pointer-events-none aria-disabled:opacity-40"
			aria-label="Next page"
			data-sveltekit-preload-data="hover"
		>
			<ChevronRightIcon />
		</Button>
		<PageSizeMenu size={q.size} sizes={PAGE_SIZES} label="Runs per page" onsize={resize} />
	</nav>{/if}
