<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import CheckIcon from '@lucide/svelte/icons/check';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import XIcon from '@lucide/svelte/icons/x';
	import type { Snippet } from 'svelte';
	import * as Popover from '#lib/components/ui/popover/index.js';
	import type { EvalRunChoice } from '#lib/types.ts';
	import { relativeTime } from '#lib/utils.ts';
	import Skeleton from '#lib/components/Skeleton.svelte';

	/**
	 * Picks one side of a comparison (025 D185) from the keyboard: the search box has focus, ↑↓
	 * move, Enter picks. It lists the eval runs of one project, the side's own at first; Backspace
	 * in an empty box (or the project chip) lists the org's projects to switch to.
	 */
	interface Props {
		/** `A` or `B`, on the trigger and in labels. */
		side: string;
		/** The run on this side now. */
		entity: string;
		project: string;
		run: string;
		/** Opened from outside too, by the page's A and B shortcuts. */
		open?: boolean;
		onpick: (entity: string, project: string, run: string) => void;
		/** The other side's run, `entity/project/id`: left out, a run isn't compared with itself. */
		exclude?: string;
		/** The field's content: the run on this side, which the whole field opens the list from. */
		children: Snippet;
	}

	let {
		side,
		entity,
		project,
		run,
		open = $bindable(false),
		onpick,
		exclude,
		children
	}: Props = $props();

	const id = $props.id();
	let mode = $state<'runs' | 'projects'>('runs');
	// The project listed, which the chip shows: the side's own until another is chosen.
	let chosen = $state<{ entity: string; project: string } | null>(null);
	const scope = $derived(chosen ?? { entity, project });
	let query = $state('');
	let active = $state(0);
	let runs = $state<EvalRunChoice[] | null>(null);
	let projects = $state<{ entity: string; name: string }[] | null>(null);
	let failed = $state<string | null>(null);

	// Each opening starts over, however it was opened (a click, or the page's shortcut).
	$effect(() => {
		if (open) return;
		mode = 'runs';
		chosen = null;
		query = '';
		failed = null;
	});

	// The runs of the project listed, searched as typed.
	$effect(() => {
		if (!open || mode !== 'runs') return;
		const { entity: e, project: p } = scope;
		const q = query.trim();
		const controller = new AbortController();
		const timer = setTimeout(
			async () => {
				try {
					const res = await fetch(
						`/api/projects/${encodeURIComponent(e)}/${encodeURIComponent(p)}/eval-runs?${new URLSearchParams({ q })}`,
						{ signal: controller.signal }
					);
					if (!res.ok) throw new Error(`Couldn’t list the runs of ${e}/${p} (${res.status})`);
					const got = ((await res.json()) as { runs: EvalRunChoice[] }).runs;
					runs = got.filter((c) => `${e}/${p}/${c.id}` !== exclude);
					failed = null;
					// Unsearched, the list starts on the run this side shows.
					const at = q ? -1 : runs.findIndex((c) => c.id === run && e === entity && p === project);
					active = Math.max(0, at);
				} catch (err) {
					if (!controller.signal.aborted) failed = (err as Error).message;
				}
			},
			q ? 150 : 0
		);
		return () => {
			clearTimeout(timer);
			controller.abort();
		};
	});

	// The org's projects, read once, the first time they're listed.
	$effect(() => {
		if (!open || mode !== 'projects' || projects) return;
		const controller = new AbortController();
		fetch(`/api/projects?${new URLSearchParams({ entity })}`, { signal: controller.signal })
			.then((res) => res.json() as Promise<{ projects: { entity: string; name: string }[] }>)
			.then((got) => (projects = got.projects))
			.catch((err: Error) => {
				if (!controller.signal.aborted) failed = err.message;
			});
		return () => controller.abort();
	});

	const shownProjects = $derived(
		(projects ?? []).filter((p) => p.name.toLowerCase().includes(query.trim().toLowerCase()))
	);
	const count = $derived(mode === 'runs' ? (runs?.length ?? 0) : shownProjects.length);
	const now = Date.now() / 1000;

	function toProjects() {
		mode = 'projects';
		query = '';
		active = Math.max(
			0,
			(projects ?? []).findIndex((p) => p.name === scope.project)
		);
	}

	function toRuns(p: { entity: string; name: string }) {
		chosen = { entity: p.entity, project: p.name };
		mode = 'runs';
		query = '';
		runs = null;
	}

	function pick(choice: EvalRunChoice) {
		open = false;
		onpick(scope.entity, scope.project, choice.id);
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
			e.preventDefault();
			if (count) active = (active + (e.key === 'ArrowDown' ? 1 : count - 1)) % count;
		} else if (e.key === 'Enter') {
			e.preventDefault();
			if (mode === 'runs' && runs?.[active]) pick(runs[active]);
			else if (mode === 'projects' && shownProjects[active]) toRuns(shownProjects[active]);
		} else if (e.key === 'Backspace' && !query && mode === 'runs') {
			e.preventDefault();
			toProjects();
		} else if (e.key === 'Escape' && mode === 'projects') {
			// Back to the runs, rather than closing.
			e.preventDefault();
			e.stopPropagation();
			mode = 'runs';
			query = '';
		}
	}

	// The active option stays in view as the arrows move it.
	$effect(() => {
		void active;
		document.getElementById(`${id}-${mode}-${active}`)?.scrollIntoView({ block: 'nearest' });
	});

	/** `flock:flock/olmo-1b@3` as `olmo-1b@3`. */
	const shortModel = (m: string | null) =>
		m
			?.replace(/^flock:/, '')
			.split('/')
			.pop() ?? '';
</script>

<Popover.Root bind:open>
	<Popover.Trigger>
		{#snippet child({ props })}
			<!-- A quiet control in the view bar: the side's letter, the run, a caret. -->
			<button
				{...props}
				type="button"
				{@attach hint(`Change run ${side}  ${side}`)}
				class="group/field flex h-7 max-w-full min-w-0 items-center gap-1.5 rounded-md px-2 text-left text-[13px] text-subtle transition-colors outline-none hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 data-[state=open]:bg-muted data-[state=open]:text-foreground"
			>
				<span class="flex-none text-muted-foreground">{side}</span>
				<span class="min-w-0 truncate">{@render children()}</span>
				<ChevronDownIcon
					class="size-3.5 flex-none text-muted-foreground transition-transform group-data-[state=open]/field:rotate-180"
				/>
			</button>
		{/snippet}
	</Popover.Trigger>
	<Popover.Content align="start" class="w-[28rem] max-w-[calc(100vw-2rem)] gap-0 p-0">
		<div class="flex items-center gap-1.5 border-b border-divider px-2 py-1.5">
			{#if mode === 'runs'}
				<button
					type="button"
					class="inline-flex max-w-40 flex-none items-center gap-1 rounded bg-secondary px-1.5 py-0.5 text-xs hover:bg-accent"
					{@attach hint(
						`Runs of ${scope.entity}/${scope.project}: click, or Backspace, to change project`
					)}
					onclick={toProjects}
				>
					<FolderIcon class="size-3 flex-none text-muted-foreground" />
					<span class="truncate">{scope.project}</span>
					<XIcon class="size-3 flex-none text-muted-foreground" />
				</button>
			{:else}
				<button
					type="button"
					class="flex-none rounded p-0.5 text-muted-foreground hover:bg-accent"
					aria-label="Back to the runs"
					onclick={() => (mode = 'runs')}
				>
					<ArrowLeftIcon class="size-3.5" />
				</button>
			{/if}
			<!-- svelte-ignore a11y_autofocus -->
			<input
				autofocus
				role="combobox"
				aria-expanded="true"
				aria-controls="{id}-list"
				aria-activedescendant={count ? `${id}-${mode}-${active}` : undefined}
				aria-label={mode === 'runs' ? `Search eval runs for ${side}` : 'Search projects'}
				placeholder={mode === 'runs' ? 'Search eval runs…' : 'Search projects…'}
				class="h-7 min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-faint"
				spellcheck="false"
				autocomplete="off"
				bind:value={query}
				oninput={() => (active = 0)}
				{onkeydown}
			/>
		</div>
		<div id="{id}-list" role="listbox" class="max-h-72 overflow-y-auto p-1 text-[13px]">
			{#if failed}
				<p class="px-2 py-2 text-xs text-destructive" role="alert">{failed}</p>
			{:else if mode === 'runs'}
				{#if runs === null}
					<Skeleton shape="lines" class="px-2" />
				{/if}
				{#each runs ?? [] as r, i (r.id)}
					{@const current = scope.entity === entity && scope.project === project && r.id === run}
					<!-- svelte-ignore a11y_click_events_have_key_events -->
					<div
						id="{id}-runs-{i}"
						role="option"
						tabindex="-1"
						aria-selected={i === active}
						class="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 aria-selected:bg-accent"
						onmousemove={() => (active = i)}
						onclick={() => pick(r)}
					>
						<CheckIcon class={['size-3.5 flex-none', !current && 'invisible']} />
						<!-- The name comes first and keeps its room; the model gives way. -->
						<span class="max-w-[75%] flex-none truncate">{r.display_name ?? r.id}</span>
						<span
							class="min-w-0 flex-1 truncate text-right text-muted-foreground"
							{@attach hint(r.model ?? undefined)}>{shortModel(r.model)}</span
						>
						<span class="w-14 flex-none text-right text-xs text-faint"
							>{relativeTime(r.created_at, now)}</span
						>
					</div>
				{:else}
					{#if runs}
						<p class="px-2 py-2 text-xs text-muted-foreground">
							No eval runs {query.trim() ? 'match' : 'in this project'}.
						</p>
					{/if}
				{/each}
			{:else}
				{#if projects === null}
					<Skeleton shape="lines" class="px-2" />
				{/if}
				{#each shownProjects as p, i (p.name)}
					<!-- svelte-ignore a11y_click_events_have_key_events -->
					<div
						id="{id}-projects-{i}"
						role="option"
						tabindex="-1"
						aria-selected={i === active}
						class="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 aria-selected:bg-accent"
						onmousemove={() => (active = i)}
						onclick={() => toRuns(p)}
					>
						<FolderIcon class="size-3.5 flex-none text-muted-foreground" />
						<span class="min-w-0 flex-1 truncate">{p.name}</span>
						{#if p.name === scope.project}<CheckIcon class="size-3.5 flex-none" />{/if}
					</div>
				{/each}
			{/if}
		</div>
	</Popover.Content>
</Popover.Root>
