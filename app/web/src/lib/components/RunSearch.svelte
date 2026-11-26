<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import SearchIcon from '@lucide/svelte/icons/search';
	import XIcon from '@lucide/svelte/icons/x';
	import { tick } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { applyRunQuery } from '#lib/workspace/url.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';

	const ws = getWorkspace();

	/** Typing pauses this long before the runs are searched. */
	const DEBOUNCE_MS = 300;

	let input = $state<HTMLInputElement>();
	let text = $state(ws.page.query.search);
	// An icon in the view bar until it's wanted: opened by a click or "/", it stays open while it
	// has text or focus.
	let opened = $state(false);
	const open = $derived(opened || !!text);

	async function expand() {
		opened = true;
		await tick();
		input?.focus();
		input?.select();
	}
	let timer: ReturnType<typeof setTimeout> | undefined;

	// Back, forward or a link changed the search: show it, unless it's being typed.
	$effect(() => {
		const search = ws.page.query.search;
		if (document.activeElement !== input) text = search;
	});

	function search(value: string) {
		clearTimeout(timer);
		const q = value.trim();
		if (q === ws.page.query.search) return;
		const params = new URLSearchParams(page.url.search);
		applyRunQuery(params, { ...ws.page.query, search: q, page: 1 });
		ws.selected.clear();
		void goto(ws.href(params), {
			replace: true,
			reset: false
		});
	}

	function oninput() {
		clearTimeout(timer);
		timer = setTimeout(() => search(text), DEBOUNCE_MS);
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.key === 'Enter') search(text);
		else if (e.key === 'Escape' && text) {
			e.stopPropagation();
			text = '';
			search('');
		} else if (e.key === 'Escape') input?.blur();
	}

	/** `/` anywhere outside a field jumps here. */
	function onwindowkey(e: KeyboardEvent) {
		if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return;
		const t = e.target as HTMLElement;
		if (t.closest('input, textarea, select, [contenteditable], [role="dialog"]')) return;
		e.preventDefault();
		void expand();
	}

	$effect(() => () => clearTimeout(timer));
</script>

<svelte:window onkeydown={onwindowkey} />

{#if !open}
	<button
		type="button"
		class="grid size-7 flex-none place-items-center rounded-md text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 [&_svg]:size-4"
		aria-label="Search runs"
		{@attach hint('Search runs  /')}
		onclick={expand}
	>
		<SearchIcon />
	</button>
{:else}
	<label
		class="flex h-7 w-52 min-w-0 items-center gap-1.5 rounded-md bg-muted px-2 focus-within:ring-3 focus-within:ring-ring/50"
	>
		<SearchIcon class="size-4 flex-none text-muted-foreground" />
		<input
			bind:this={input}
			bind:value={text}
			{oninput}
			{onkeydown}
			onblur={() => (opened = false)}
			type="search"
			class="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-faint [&::-webkit-search-cancel-button]:hidden"
			placeholder="Search runs"
			aria-label="Search runs by name or id"
			autocomplete="off"
			spellcheck="false"
		/>
		{#if text}
			<button
				type="button"
				class="grid size-5 flex-none place-items-center rounded text-muted-foreground hover:text-foreground"
				aria-label="Clear search"
				onclick={() => {
					text = '';
					search('');
					opened = false;
				}}
			>
				<XIcon class="size-3.5" />
			</button>
		{/if}
	</label>
{/if}
