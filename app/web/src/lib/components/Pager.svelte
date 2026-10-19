<script lang="ts">
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import type { RunQuery } from '#lib/types.ts';
	import { applyRunQuery, PAGE_SIZES, workspaceHref } from '#lib/workspace/url.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';

	const ws = getWorkspace();
	const q = $derived(ws.page.query);
	const pages = $derived(Math.max(1, Math.ceil(ws.page.total / ws.meta.pageSize)));
	const first = $derived((q.page - 1) * ws.meta.pageSize);

	function href(changes: Partial<RunQuery>) {
		const params = new URLSearchParams(page.url.search);
		applyRunQuery(params, { ...q, ...changes });
		return workspaceHref(ws.meta.entity, ws.meta.project, params);
	}

	/** A new page size keeps the first run shown on screen: its page at the new size. */
	let choosing = $state(false);

	function resize(value: string) {
		// Closed here: the navigation re-renders the menu, which would otherwise stay open.
		choosing = false;
		const size = Number(value);
		if (size === q.size) return;
		void goto(href({ size, page: Math.floor(first / size) + 1 }), { reset: false });
	}
</script>

<!-- Links preload on hover (SvelteKit) on top of the idle prefetch of both neighbours. -->
<nav
	class="pager absolute bottom-3.5 left-4 z-5 flex h-10 items-center gap-1 rounded-xl bg-popover py-1 pr-1 pl-3.5 text-[13px] tabular-nums shadow-md ring-1 ring-foreground/10"
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
	<span class="mx-0.5 h-5 w-px bg-border" aria-hidden="true"></span>
	<!-- A menu rather than a select: its items act on click, so the click doesn't land on the
	     run row under the menu once it closes. -->
	<DropdownMenu.Root bind:open={choosing}>
		<DropdownMenu.Trigger>
			{#snippet child({ props })}
				<Button
					{...props}
					variant="ghost"
					size="sm"
					class="gap-1 px-2 font-normal"
					aria-label="Runs per page"
					title="Runs per page"
				>
					{q.size} per page
					<ChevronDownIcon class="text-muted-foreground" />
				</Button>
			{/snippet}
		</DropdownMenu.Trigger>
		<DropdownMenu.Content side="top" align="end" class="w-40">
			<DropdownMenu.Group>
				<DropdownMenu.Label>Runs per page</DropdownMenu.Label>
				<DropdownMenu.RadioGroup value={String(q.size)} onValueChange={resize}>
					{#each PAGE_SIZES as n (n)}
						<DropdownMenu.RadioItem value={String(n)}>{n}</DropdownMenu.RadioItem>
					{/each}
				</DropdownMenu.RadioGroup>
			</DropdownMenu.Group>
		</DropdownMenu.Content>
	</DropdownMenu.Root>
</nav>
