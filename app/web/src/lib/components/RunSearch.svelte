<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import BarSearch from '#lib/components/BarSearch.svelte';
	import { applyRunQuery } from '#lib/workspace/url.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';

	const ws = getWorkspace();

	/** Typing pauses this long before the runs are searched. */
	const DEBOUNCE_MS = 300;

	let text = $state(ws.page.query.search);
	let timer: ReturnType<typeof setTimeout> | undefined;

	// Back, forward or a link changed the search: show it, unless it's being typed.
	$effect(() => {
		const search = ws.page.query.search;
		if (!(document.activeElement as HTMLElement | null)?.closest('[data-run-search]'))
			text = search;
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

	$effect(() => () => clearTimeout(timer));
</script>

<!-- By a run's name or id. -->
<span class="contents" data-run-search>
	<BarSearch
		bind:value={text}
		things="runs"
		{oninput}
		onkeydown={(e) => {
			if (e.key === 'Enter') search(text);
		}}
		onclear={() => search('')}
	/>
</span>
