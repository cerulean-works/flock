<script lang="ts">
	import PlusIcon from '@lucide/svelte/icons/plus';
	import XIcon from '@lucide/svelte/icons/x';
	import { untrack } from 'svelte';
	import { deserialize } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import { clearRunPages } from '#lib/data/cache.ts';
	import { forgetRunValues, runValues } from '#lib/data/run-values.svelte.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';

	// Runs' tags as Notion's multi-select: chips in a field, the project's tags under it, each
	// change saved at once. In a ⋯ menu's Tags submenu, or the selection's Tags panel.

	/** The runs, by id. */
	let { ids }: { ids: readonly string[] } = $props();

	const ws = getWorkspace();

	/** Each tag on the runs, and how many of them have it: read when it opens, then kept here. */
	let tags = $state<[string, number][]>([]);
	$effect.pre(() => {
		const rows = ws.rowsOf(ids);
		untrack(() => {
			const counts: Record<string, number> = {};
			for (const r of rows) {
				const list = ws.cell(r, 'tags');
				if (Array.isArray(list)) for (const t of list) counts[t] = (counts[t] ?? 0) + 1;
			}
			tags = Object.entries(counts).toSorted(([a], [b]) => a.localeCompare(b));
		});
	});

	let query = $state('');
	let field = $state<HTMLInputElement | null>(null);
	let active = $state(0);

	/** The project's tags, with how many runs have each. */
	const known = $derived(runValues(ws.meta.entity, ws.meta.project, ['tags'], ws.group).tags ?? []);
	const q = $derived(query.trim().toLowerCase());
	const everywhere = (t: string) => tags.some(([tag, n]) => tag === t && n === ids.length);
	const options = $derived.by(() => {
		const out = known
			.map((v) => ({ tag: String(v.value), runs: v.runs }))
			.filter((o) => !everywhere(o.tag) && (!q || o.tag.toLowerCase().includes(q)));
		const typed = query.trim();
		if (typed && !/[\s,]/.test(typed) && !known.some((v) => v.value === typed))
			out.push({ tag: typed, runs: -1 });
		return out;
	});

	/**
	 * Sends one change to the page's `tag` action, then reloads the page's data. Posted by hand
	 * rather than through `enhance`, whose `applyAction` moves focus to the page and so would
	 * close a menu the editor is in.
	 */
	async function send(change: { add?: string; remove?: string }) {
		const body = new FormData();
		for (const id of ids) body.append('run', id);
		body.append('add', change.add ?? '');
		if (change.remove) body.append('remove', change.remove);
		forgetRunValues();
		try {
			const res = await fetch(`${location.pathname}?/tag`, {
				method: 'POST',
				body,
				headers: { 'x-sveltekit-action': 'true' }
			});
			const result = deserialize(await res.text());
			if (result.type === 'failure' || result.type === 'error') {
				const message =
					result.type === 'failure'
						? String(result.data?.error ?? 'Couldn’t change the tags')
						: 'Couldn’t change the tags';
				toast.error(message.charAt(0).toUpperCase() + message.slice(1));
			}
		} catch {
			toast.error('Couldn’t change the tags');
		}
		clearRunPages();
		await invalidateAll();
	}

	function addTag(tag: string) {
		const has = tags.find(([t]) => t === tag);
		tags = has
			? tags.map(([t, n]) => [t, t === tag ? ids.length : n])
			: [...tags, [tag, ids.length]];
		query = '';
		active = 0;
		void send({ add: tag });
		field?.focus();
	}

	function removeTag(tag: string) {
		tags = tags.filter(([t]) => t !== tag);
		void send({ remove: tag });
		field?.focus();
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
			e.preventDefault();
			const n = options.length;
			if (n) active = (active + (e.key === 'ArrowDown' ? 1 : n - 1)) % n;
		} else if (e.key === 'Enter' || (e.key === ',' && query.trim())) {
			e.preventDefault();
			const pick = options[active] ?? (query.trim() ? { tag: query.trim() } : null);
			if (pick) addTag(pick.tag);
		} else if (e.key === 'Backspace' && !query && tags.length) {
			e.preventDefault();
			removeTag(tags[tags.length - 1][0]);
		}
	}
</script>

<!-- Keys typed here stay here: a menu around it would take letters and arrows for its own. -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	class="text-[13px]"
	onkeydown={(e) => {
		if (e.key !== 'Escape' && e.key !== 'Tab') e.stopPropagation();
	}}
	{@attach () => void queueMicrotask(() => field?.focus())}
>
	<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
	<div
		class="flex min-h-10 flex-wrap items-center gap-1 border-b border-divider px-2 py-1.5"
		onclick={() => field?.focus()}
	>
		{#each tags as [tag, n] (tag)}
			{@const some = n < ids.length}
			<span
				class={[
					'inline-flex h-6 max-w-full items-center rounded-md bg-muted text-xs',
					some && 'text-muted-foreground'
				]}
			>
				{#if some}
					<!-- On only some of the runs: a click puts it on all of them. -->
					<button
						type="button"
						class="truncate py-0.5 pr-1 pl-2 hover:text-foreground"
						aria-label="Add {tag} to every run"
						onclick={() => addTag(tag)}
					>
						{tag} <span class="tabular-nums">{n} of {ids.length}</span>
					</button>
				{:else}
					<span class="truncate py-0.5 pr-1 pl-2">{tag}</span>
				{/if}
				<button
					type="button"
					class="grid h-6 w-5 flex-none place-items-center rounded-r-md text-muted-foreground hover:text-foreground"
					aria-label="Remove {tag}"
					onclick={() => removeTag(tag)}
				>
					<XIcon class="size-3" />
				</button>
			</span>
		{/each}
		<input
			bind:this={field}
			bind:value={query}
			class="h-6 min-w-16 flex-1 bg-transparent px-1 outline-none placeholder:text-faint"
			placeholder={tags.length ? '' : 'Add a tag…'}
			aria-label="Add a tag"
			role="combobox"
			aria-expanded="true"
			aria-controls="tag-options"
			autocomplete="off"
			spellcheck="false"
			oninput={() => (active = 0)}
			{onkeydown}
		/>
	</div>
	<ul id="tag-options" role="listbox" class="max-h-60 overflow-y-auto p-1" aria-label="Tags">
		{#each options as o, i (o.tag)}
			<li role="option" aria-selected={i === active}>
				<button
					type="button"
					class={[
						'flex h-8 w-full items-center gap-2 rounded-md px-2 text-left',
						i === active ? 'bg-muted' : 'hover:bg-muted'
					]}
					onpointermove={() => (active = i)}
					onclick={() => addTag(o.tag)}
				>
					{#if o.runs < 0}
						<PlusIcon class="size-3.5 flex-none text-muted-foreground" />
						<span class="min-w-0 flex-1 truncate">Create “{o.tag}”</span>
					{:else}
						<span class="min-w-0 flex-1 truncate">{o.tag}</span>
						<span class="text-xs text-muted-foreground tabular-nums">{o.runs}</span>
					{/if}
				</button>
			</li>
		{:else}
			<li class="px-2 py-2 text-xs text-muted-foreground">
				{known.length ? 'No other tags' : 'Type a tag, then Enter'}
			</li>
		{/each}
	</ul>
</div>
