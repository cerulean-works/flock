<script lang="ts">
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import { toast } from 'svelte-sonner';
	import { goto, invalidate, refreshAll } from '$app/navigation';
	import { page } from '$app/state';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import { primaryWidth, textWidth } from '#lib/components/RunsTable.svelte';
	import Page from '#lib/components/Page.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import AddToGroup from '#lib/components/groups/AddToGroup.svelte';
	import BestDots, { type BestDot } from '#lib/components/groups/BestDots.svelte';
	import CompareHistory, { type CompareLine } from '#lib/components/groups/CompareHistory.svelte';
	import BestByPicker from '#lib/components/groups/BestByPicker.svelte';
	import BestMark from '#lib/components/groups/BestMark.svelte';
	import { SCROLL_FADE, scrollFade } from '#lib/scroll-fade.ts';
	import HeaderMenu from '#lib/components/HeaderMenu.svelte';
	import LiveDot from '#lib/components/groups/LiveDot.svelte';
	import { GroupsLive } from '#lib/components/groups/live.svelte.ts';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Card from '#lib/components/ui/card/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Switch } from '#lib/components/ui/switch/index.js';
	import { runPageUrl } from '#lib/data/cache.ts';
	import type { ProjectEvent } from '#lib/data/live.ts';
	import { decodeWorkspace } from '#lib/data/snapshot.ts';
	import { evalTable } from '#lib/eval-table.ts';
	import { formatScore } from '#lib/evals.ts';
	import { evalRuns } from '#lib/evals-view.svelte.ts';
	import { GROUP_HUES } from '#lib/chart/grouping.ts';
	import {
		GROUPS,
		GroupsError,
		beats,
		displayKey,
		formatDelta,
		groupHref,
		groupsApi,
		groupsCall,
		groupsHref,
		chartKey,
		metricLabel,
		newGroupHref,
		valueKey,
		type BestOrder,
		type CompareRow,
		type Group
	} from '#lib/groups.ts';
	import { scheme } from '#lib/theme.svelte.ts';
	import type { RunQuery, WorkspaceData } from '#lib/types.ts';
	import { count, formatNumber, formatValue, numberFormat } from '#lib/utils.ts';
	import { entityHref, runHref } from '#lib/workspace/url.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const entity = $derived(page.params.entity!);
	const project = $derived(page.params.project!);
	/** Outside the org: read-only, with nothing to add or create (D116). */
	const readonly = $derived(data.shell.access === 'public');

	const cmp = $derived(data.compare);
	const groups = $derived<Group[]>(data.groups?.groups ?? []);
	const byId = $derived(new Map(groups.map((g) => [g.id, g])));
	const order = $derived<BestOrder>(cmp.best_order ?? 'asc');

	// ------------------------------------------------------------------------------------------
	// Columns: the value, its change from the best group's, the spread across repeats, the run
	// count, the suggested settings and metrics, then the config keys that differ between the
	// rows (D221), or every key with the toggle off.

	/**
	 * A group ranked by another best-by column than the page's: the API puts it last, and its
	 * row is greyed with the metric named (D221).
	 */
	function otherMetric(row: CompareRow): string | null {
		const key = row.best_key ?? (row.group == null ? null : byId.get(row.group)?.best_key);
		return key && key !== cmp.best_key ? metricLabel(key) : null;
	}

	/** Every group ranks by a column of its own: nothing compares on the page's. */
	const allOwn = $derived(cmp.rows.length > 0 && cmp.rows.every((r) => otherMetric(r)));
	/** Groups ranked by the page's best-by column with a best run, in rank order. */
	const charted = $derived(cmp.rows.filter((r) => r.run && !otherMetric(r)));
	/** The best group, which the changes are from. */
	const top = $derived(charted.find((r) => r.value != null) ?? null);

	let differencesOnly = $state(true);
	const everyRow = $derived([...cmp.rows, ...cmp.ungrouped]);
	const allKeys = $derived([...new Set(everyRow.flatMap((r) => Object.keys(r.config)))].sort());
	const sameKeys = $derived(Math.max(0, allKeys.length - cmp.keys.length));

	/**
	 * Suggested columns: each group's primary columns (`.../groups/{group}/primary`, cached by
	 * the API), read without holding the page up, and read again only when a group or its best
	 * run changes. Until they come, or if they fail, the table has no Suggested section.
	 */
	let picks = $state.raw<string[][] | null>(null);
	const picksKey = $derived(
		`${cmp.best_key}|${cmp.rows.map((r) => `${r.group}:${r.run ?? ''}`).join(',')}`
	);
	$effect(() => {
		void picksKey;
		const ids = cmp.rows.flatMap((r) => (r.group == null ? [] : [r.group]));
		picks = null;
		if (!ids.length) return;
		const ctl = new AbortController();
		const one = (id: number) =>
			groupsCall<{ keys?: unknown }>(groupsApi(entity, project, `${id}/primary`), {
				signal: ctl.signal
			})
				.then((r) =>
					Array.isArray(r?.keys) ? r.keys.filter((k): k is string => typeof k === 'string') : []
				)
				.catch(() => []);
		void Promise.all(ids.map(one)).then((found) => {
			if (!ctl.signal.aborted) picks = found;
		});
		return () => ctl.abort();
	});

	const SUGGESTED_SETTINGS = 4;
	const SUGGESTED_METRICS = 4;
	/**
	 * The union of the groups' picks, by how many groups pick each (ties in the order first
	 * picked): settings that differ between the rows, then metrics some row has, past the
	 * best-by column (it has a column of its own).
	 */
	const suggested = $derived.by(() => {
		if (!picks) return { settings: [] as string[], metrics: [] as string[] };
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- local to this derivation
		const votes = new Map<string, number>();
		for (const keys of picks)
			for (const k of new Set(keys.map((k) => (/^(config|summary)\./.test(k) ? k : `config.${k}`))))
				votes.set(k, (votes.get(k) ?? 0) + 1);
		const ranked = [...votes.keys()].sort((a, b) => votes.get(b)! - votes.get(a)!);
		const differing = new Set(cmp.keys);
		const settings = ranked.filter((k) => differing.has(k)).slice(0, SUGGESTED_SETTINGS);
		const metrics = ranked
			.filter(
				(k) =>
					k.startsWith('summary.') &&
					k !== cmp.best_key &&
					everyRow.some((r) => r.summary?.[k] != null)
			)
			.slice(0, SUGGESTED_METRICS);
		return { settings, metrics };
	});
	const primary = $derived([...suggested.settings, ...suggested.metrics]);
	/** A suggested metric's header: its whole key when a suggested setting has its name. */
	const metricName = (key: string) =>
		suggested.settings.some((k) => displayKey(k) === metricLabel(key)) ? key : metricLabel(key);
	const keys = $derived(
		(differencesOnly ? cmp.keys : allKeys).filter((k) => !suggested.settings.includes(k))
	);

	// Fixed columns at their widths, the keys' fitted to their content (header or widest
	// value); an empty last cell takes what's left, so rows span the window without the
	// columns drifting apart.
	const NAME_W = 280;
	// The ranked column's header holds its name, the trophy and its menu.
	const VALUE_W = 176;
	const DELTA_W = 136;
	const STD_W = 84;
	const RUNS_W = 56;
	const KEY_MIN = 64;
	const KEY_MAX = 200;
	const FONT = "13px 'Host Grotesk', Arial, sans-serif";
	const HEAD_FONT = "500 12px 'Host Grotesk', Arial, sans-serif";
	/** Each setting column's number format, one per column (an lr column reads `3e-4`, `6e-5`). */
	const formatOf = $derived.by(() => {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- local to this derivation
		const out = new Map<string, (v: number) => string>();
		for (const key of new Set([...suggested.settings, ...keys]))
			out.set(key, numberFormat(everyRow.map((r) => r.config[key])));
		return out;
	});
	/** A row's setting as its column writes it. */
	const setting = (r: CompareRow, key: string): string => {
		const v = r.config[key];
		return typeof v === 'number' ? (formatOf.get(key) ?? formatNumber)(v) : formatValue(v);
	};

	/** A key column's width: its label or its widest value, plus a cell's and a chip's padding. */
	function keyWidth(key: string, metric = false): number {
		let w = textWidth(metric ? metricName(key) : displayKey(key), HEAD_FONT) ?? 112 - 28;
		for (const r of everyRow) {
			const v = metric ? r.summary?.[key] : r.config[key];
			if (v == null) continue;
			const text =
				metric && typeof v === 'number'
					? formatNumber(v)
					: metric
						? formatValue(v)
						: setting(r, key);
			w = Math.max(w, (textWidth(text, FONT) ?? 0) * 1.06);
		}
		return Math.max(KEY_MIN, Math.min(KEY_MAX, Math.ceil(w + 28)));
	}
	const widthOf = $derived.by(() => {
		const out: Record<string, number> = {};
		for (const k of suggested.settings) out[k] = primaryWidth(keyWidth(k));
		for (const k of suggested.metrics) out[k] = primaryWidth(keyWidth(k, true));
		for (const k of keys) out[k] ??= keyWidth(k);
		return out;
	});
	const sum = (ks: readonly string[]) => ks.reduce((a, k) => a + (widthOf[k] ?? 0), 0);
	const totalW = $derived(NAME_W + VALUE_W + DELTA_W + STD_W + RUNS_W + sum(primary) + sum(keys));
	/** A cell's flex: exactly `w` pixels. */
	const fit = (w: number) => `0 0 ${w}px`;
	/** The names' column: at most 40% of a phone's width, so the values show beside it. */
	const NAME_FLEX = `0 0 min(${NAME_W}px, 40vw)`;

	/** The largest change from the best group, which the bars are scaled to. */
	const maxDelta = $derived.by(() => {
		if (top?.value == null) return 0;
		let m = 0;
		for (const r of everyRow) if (r.value != null) m = Math.max(m, Math.abs(r.value - top.value));
		return m;
	});

	/** A setting that differs from the best group's best run. */
	const differs = (row: CompareRow, key: string) =>
		!!top && row !== top && valueKey(row.config[key]) !== valueKey(top.config[key]);

	// ------------------------------------------------------------------------------------------
	// Ungrouped runs (D220): folded under the groups; open, the best 3, and the rest on request.

	const UNGROUPED_SHOWN = 3;
	let ungroupedOpen = $state(false);
	let showAll = $state(false);
	const ungrouped = $derived(showAll ? cmp.ungrouped : cmp.ungrouped.slice(0, UNGROUPED_SHOWN));

	// ------------------------------------------------------------------------------------------
	// Charts under the table: the best-by metric's history of each group's best run, and the
	// best values side by side. Each group keeps its color.

	const hues = $derived(GROUP_HUES[scheme.dark ? 'dark' : 'light']);
	const colorOf = (r: CompareRow, i: number) => r.color ?? hues[i % hues.length];

	const lines = $derived.by((): CompareLine[] => {
		const out: CompareLine[] = [];
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- local to this derivation
		const seen = new Set<string>();
		charted.forEach((r, i) => {
			if (seen.has(r.run!)) return;
			seen.add(r.run!);
			out.push({ run: r.run!, label: r.name, color: colorOf(r, i), value: r.run_value ?? r.value });
		});
		return out;
	});

	const dots = $derived(
		charted.flatMap((r, i): BestDot[] =>
			r.value == null
				? []
				: [
						{
							key: `g${r.group}`,
							run: r.run,
							label: r.name,
							color: colorOf(r, i),
							value: r.value,
							std: r.std,
							values: r.values?.length ? r.values : [r.value]
						}
					]
		)
	);
	const spread = $derived(dots.some((d) => d.std != null && d.std > 0));

	/** The run hovered in the table or a chart: its row and lines come forward. */
	let hovered = $state<string | null>(null);

	/**
	 * The lines' histories: the project's workspace snapshot of just their runs, read again when
	 * the runs or their values change (live updates). The last one stays up meanwhile.
	 */
	let history = $state.raw<WorkspaceData | null>(null);
	let historyFailed = $state(false);
	/**
	 * The best-by metric was logged as a summary only (an eval run's suite score): no history
	 * panel, and the best values take the width.
	 */
	const summaryOnly = $derived(
		!!history && !history.meta.metrics.some((m) => m.key === chartKey(cmp.best_key ?? ''))
	);
	const historyRuns = $derived([...new Set(lines.map((l) => l.run))].sort());
	const historyKey = $derived(
		`${historyRuns.join(',')}|${everyRow.map((r) => r.value ?? '').join(',')}`
	);
	$effect(() => {
		void historyKey;
		const ids = historyRuns;
		if (!ids.length) {
			history = null;
			return;
		}
		const q: RunQuery = {
			page: 1,
			sort: 'created_at',
			order: 'asc',
			size: ids.length <= 50 ? 50 : 500,
			search: '',
			filters: [{ match: 'any', filters: ids.map((id) => ({ key: 'name', op: 'eq', value: id })) }]
		};
		const ctl = new AbortController();
		fetch(runPageUrl(entity, project, q), { signal: ctl.signal })
			.then((res) => {
				if (!res.ok) throw new Error(res.statusText);
				return res.arrayBuffer();
			})
			.then((buf) => {
				history = decodeWorkspace(buf, q);
				historyFailed = false;
			})
			.catch(() => {
				if (!ctl.signal.aborted) historyFailed = true;
			});
		return () => ctl.abort();
	});

	// ------------------------------------------------------------------------------------------
	// Toolbar

	async function setBest(key: string, best: BestOrder) {
		try {
			await groupsCall(groupsApi(entity, project, 'settings'), {
				method: 'PUT',
				body: { best_key: key, best_order: best }
			});
			await invalidate(GROUPS);
		} catch (err) {
			toast.error(err instanceof GroupsError ? err.message : 'Couldn’t change Best by');
		}
	}

	// ------------------------------------------------------------------------------------------
	// Live updates (D228): values and counts are read again; the rows' runs are watched.

	function onproject(e: ProjectEvent) {
		if (e.visibility === 'private') {
			toast(`${project} is no longer public`, { id: 'project' });
			void refreshAll();
		} else if (e.deleted) {
			toast(`${project} was deleted`, { id: 'project' });
			void goto(entityHref(entity), { replaceState: true });
		} else {
			const to = e.renamed_to ?? project;
			toast(`Project renamed to ${to}`, { id: 'project' });
			void goto(groupsHref(entity, to), { replaceState: true });
		}
	}

	const live = new GroupsLive(() => void invalidate(GROUPS), onproject, 5000);
	const watched = $derived(
		[...new Set(everyRow.flatMap((r) => (r.run ? [r.run] : [])))].sort().join(',')
	);
	$effect(() => {
		live.connect(entity, project, watched ? watched.split(',') : []);
		return () => live.close();
	});

	/**
	 * An eval suite's scale (accuracy or bits per byte), read from the best runs as the Evals
	 * preset reads it, so its scores read as on the eval pages: 62.4%, 0.687. Null for other
	 * metrics, which keep the run table's format.
	 */
	const scale = $derived.by(() => {
		const suite = cmp.best_key?.match(/^summary\.suites\/(.+)$/)?.[1];
		return suite && history ? (evalTable(evalRuns(history)).columns[suite]?.scale ?? null) : null;
	});
	/** A value; `own` is false on a row a group ranks by another metric (D221): plain format. */
	const fmt = (v: number | null, own = true) =>
		v == null
			? '–'
			: scale && own
				? formatScore(v, scale === 'accuracy' ? 'acc' : scale)
				: formatNumber(v);
	/** A spread across repeats, in the score's units: an accuracy's in points. */
	const fmtSpread = (s: number, own = true) =>
		!scale || !own
			? formatNumber(s)
			: scale === 'accuracy'
				? `${(100 * s).toFixed(1)} pts`
				: s.toFixed(3);
	/** A change from the best group's: an accuracy's in points, as the compare page has it. */
	const fmtDelta = (d: number) =>
		scale === 'accuracy'
			? `${d < 0 ? '−' : '+'}${Math.abs(100 * d).toFixed(1)} pts`
			: formatDelta(d);
	const empty = $derived(!cmp.rows.length && !cmp.ungrouped.length);
	/** No saved groups: only a viewer who can't create one gets here (the load sends others on). */
	const noGroups = $derived(!!data.groups && !groups.length);

	// Static class strings, as the runs table has them.
	const NAME_CELL =
		'sticky left-0 z-2 flex min-w-0 items-center gap-2.5 pr-3 pl-5 shadow-[inset_-1px_0_0_var(--divider)]';
	const CELL = 'flex min-w-0 items-center overflow-hidden px-3 whitespace-nowrap';
	/** Where the Suggested section ends: a bolder divider, as in a group's table. */
	const PRIMARY_END = 'shadow-[inset_-2px_0_0_var(--border)]';
</script>

<svelte:head>
	<title>Groups · {project} · {data.shell.orgName ?? entity}</title>
</svelte:head>

{#snippet row(r: CompareRow, kind: 'group' | 'ungrouped')}
	{@const other = otherMetric(r)}
	{@const better = r.value != null && top?.value != null && beats(r.value, top.value, order)}
	{@const delta = r.value != null && top?.value != null && !other ? r.value - top.value : null}
	{@const lit = hovered != null && r.run === hovered}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class={[
			'group/row flex h-11 w-full items-stretch border-b border-divider tabular-nums',
			lit ? 'bg-muted' : 'hover:bg-muted',
			other && 'text-muted-foreground'
		]}
		onpointerenter={() => (hovered = r.run)}
		onpointerleave={() => (hovered = null)}
	>
		<div
			class={[NAME_CELL, lit ? 'bg-muted' : 'bg-background group-hover/row:bg-muted']}
			style:flex={NAME_FLEX}
		>
			<span class="grid w-4 flex-none place-items-center" aria-hidden="true">
				{#if r.group == null}
					<span class="size-3 rounded-full border-[1.5px] border-faint"></span>
				{:else}
					<span
						class="size-3 rounded-[3px]"
						style:background={charted.includes(r)
							? colorOf(r, charted.indexOf(r))
							: (r.color ?? 'var(--faint)')}
					></span>
				{/if}
			</span>
			<span class="flex min-w-0 flex-1 flex-col leading-tight">
				{#if r.group == null && r.run}
					<a
						class="truncate text-foreground underline-offset-3 hover:text-link hover:underline"
						href={runHref(entity, project, r.run)}
						title={r.run_name ?? r.run}>{r.run_name ?? r.run}</a
					>
					<span class="truncate text-xs text-muted-foreground">ungrouped run</span>
				{:else if r.group != null}
					<a
						class="truncate font-medium text-foreground underline-offset-3 hover:text-link hover:underline"
						href={groupHref(entity, project, r.group)}
						title={r.name}>{r.name}</a
					>
					<span class="truncate text-xs text-muted-foreground">
						{#if other}
							best by {other}
						{:else if !r.run}
							no run has {metricLabel(cmp.best_key ?? '')}
						{:else}
							best:
							<a class="hover:text-link hover:underline" href={runHref(entity, project, r.run)}
								>{r.run_name}</a
							>{#if r.n > 1}{r.run_value != null ? ` ${fmt(r.run_value, !other)}` : ''}, mean of {count(
									r.n,
									'repeat'
								)}{/if}
						{/if}
					</span>
				{/if}
			</span>
			{#if kind === 'ungrouped' && !readonly && r.run}
				<AddToGroup {entity} {project} run={{ id: r.run, name: r.run_name ?? r.run }} {groups} />
			{/if}
		</div>
		<div
			class={[CELL, 'justify-end']}
			style:flex={fit(VALUE_W)}
			title={r.n > 1 && r.run_value != null
				? `Mean of ${r.n} repeats; the best run alone has ${fmt(r.run_value, !other)}`
				: undefined}
		>
			{fmt(r.value, !other)}
		</div>
		<div class={[CELL, 'justify-end gap-2']} style:flex={fit(DELTA_W)}>
			{#if delta == null}
				<span
					class="text-faint"
					title={other ? `Ranked by ${other}, not ${metricLabel(cmp.best_key ?? '')}` : undefined}
					>–</span
				>
			{:else if r === top}
				<span class="text-faint" title="The best group, which the changes are from">best</span>
			{:else}
				{#if maxDelta > 0 && delta !== 0}
					<span
						class={['h-2 rounded-[2px]', better ? 'bg-state-finished' : 'bg-state-failed/50']}
						style:width="{Math.max(2, Math.round((Math.abs(delta) / maxDelta) * 56))}px"
						aria-hidden="true"
					></span>
				{/if}
				<span class={better ? 'text-state-finished' : delta === 0 ? 'text-faint' : ''}
					>{delta === 0 ? 'same' : fmtDelta(delta)}</span
				>
			{/if}
		</div>
		<div class={[CELL, 'justify-end text-muted-foreground']} style:flex={fit(STD_W)}>
			{#if r.std != null}± {fmtSpread(r.std, !other)}{:else}<span class="text-faint">–</span>{/if}
		</div>
		<div class={[CELL, 'justify-end text-muted-foreground']} style:flex={fit(RUNS_W)}>
			{r.runs}
		</div>
		{#each suggested.settings as key, i (key)}
			{@const diff = differs(r, key)}
			<div
				class={[CELL, 'px-2', i === primary.length - 1 && PRIMARY_END]}
				style:flex={fit(widthOf[key])}
			>
				<span
					class={[
						'truncate rounded-[5px] px-1.5 py-0.5',
						diff ? 'bg-selection font-semibold text-foreground' : 'text-foreground'
					]}
					title={setting(r, key)}>{setting(r, key)}</span
				>
			</div>
		{/each}
		{#each suggested.metrics as key, i (key)}
			{@const v = r.summary?.[key]}
			<div
				class={[
					CELL,
					'justify-end',
					suggested.settings.length + i === primary.length - 1 && PRIMARY_END
				]}
				style:flex={fit(widthOf[key])}
			>
				{#if v == null}<span class="text-faint">–</span>{:else}{formatNumber(v)}{/if}
			</div>
		{/each}
		{#each keys as key (key)}
			{@const diff = differs(r, key)}
			<div class={[CELL, 'px-2']} style:flex={fit(widthOf[key])}>
				<span
					class={[
						'truncate rounded-[5px] px-1.5 py-0.5',
						diff ? 'bg-selection font-semibold text-foreground' : 'text-faint'
					]}
					title={setting(r, key)}>{setting(r, key)}</span
				>
			</div>
		{/each}
		<span class="min-w-5 flex-1" aria-hidden="true"></span>
	</div>
{/snippet}

<Page width="full">
	<PageHeader title="Groups">
		{#snippet actions()}
			<LiveDot connection={live.connection} />
		{/snippet}
		{#snippet toolbar()}
			<span class="flex-1"></span>
			<div class="flex items-center gap-2">
				<Switch id="differences-only" bind:checked={differencesOnly} />
				<Label for="differences-only" class="font-normal">Differences only</Label>
			</div>
		{/snippet}
	</PageHeader>

	<main class="relative min-h-0 flex-1">
		{#if !cmp.best_key}
			<EmptyState variant="onboarding" title="Pick what best means">
				{#snippet lead()}
					Compare ranks each group’s best run by one summary column. This project has none of
					val/loss, eval/loss or loss, so pick one.
				{/snippet}
				{#snippet actions()}
					<BestByPicker
						placeholder="Pick a column"
						key={null}
						order={null}
						keys={data.summaryKeys}
						disabled={readonly}
						onchange={setBest}
					/>
				{/snippet}
			</EmptyState>
		{:else if noGroups}
			<EmptyState variant="onboarding" title="No groups yet">
				{#snippet lead()}
					Nobody has grouped {project}’s runs into experiments yet. Its runs are all on the Runs
					page.
				{/snippet}
			</EmptyState>
		{:else if empty}
			<EmptyState variant="onboarding" title="Nothing to compare yet">
				{#snippet lead()}
					No run has {metricLabel(cmp.best_key ?? '')}. Groups and ungrouped runs show up here once
					runs log it.
				{/snippet}
				{#snippet actions()}
					{#if !readonly}
						<Button variant="outline" href={newGroupHref(entity, project)}>
							<PlusIcon /> New group
						</Button>
					{/if}
				{/snippet}
			</EmptyState>
		{:else}
			<!-- The table as tall as its rows, up to a little over half the page; the charts take
			     the rest, so they grow when there are few groups. -->
			<div class="absolute inset-0 flex flex-col overflow-y-auto overscroll-contain">
				<section
					class={[
						'max-h-[56%] min-h-24 flex-none overflow-auto overscroll-contain border-b border-divider text-[13px]',
						SCROLL_FADE
					]}
					aria-label="Compare"
					{@attach scrollFade}
				>
					<div style:min-width="{totalW}px">
						<!-- The band and the header stay on top; the names stay at the left. -->
						<div class="sticky top-0 z-3 bg-background">
							{#if keys.length || primary.length}
								<div class="flex h-[26px] text-xs text-muted-foreground">
									<div
										class="sticky left-0 z-2 bg-background shadow-[inset_-1px_0_0_var(--divider)]"
										style:flex={NAME_FLEX}
									></div>
									<div
										class="border-l border-divider px-3 pt-1.5"
										style:flex={fit(VALUE_W + DELTA_W + STD_W + RUNS_W)}
									>
										best run
									</div>
									{#if primary.length}
										<div
											class={['border-l border-divider px-3 pt-1.5 whitespace-nowrap', PRIMARY_END]}
											style:flex={fit(sum(primary))}
											title="What matters for comparing the groups: the settings and metrics their own tables suggest"
										>
											Suggested <span class="ml-0.5 text-faint">{primary.length}</span>
										</div>
									{/if}
									{#if !keys.length}
										<span class="min-w-5 flex-1" aria-hidden="true"></span>
									{:else}
										<div
											class="border-l border-divider px-3 pt-1.5 whitespace-nowrap"
											style:flex="1 0 {sum(keys)}px"
										>
											{#if differencesOnly}
												config · differs between rows
												<span class="text-faint">{keys.length}</span>
												{#if sameKeys}
													·
													<button
														type="button"
														class="text-faint hover:text-foreground"
														onclick={() => (differencesOnly = false)}
													>
														{sameKeys} same, hidden
													</button>
												{/if}
											{:else}
												config · every key <span class="text-faint">{keys.length}</span>
											{/if}
										</div>
									{/if}
								</div>
							{/if}
							<div
								class="flex h-10 w-full items-stretch border-b text-xs font-medium text-muted-foreground"
							>
								<div class={[NAME_CELL, 'bg-background']} style:flex={NAME_FLEX}>
									<span>Group</span>
									<span class="ml-auto truncate font-normal text-faint">
										{count(cmp.rows.length, 'group')} · {cmp.ungrouped_count} ungrouped
									</span>
								</div>
								<!-- The column groups are ranked by: its trophy changes which, and which way. -->
								<div class={[CELL, 'group/h justify-end gap-1']} style:flex={fit(VALUE_W)}>
									<span class="truncate">{metricLabel(cmp.best_key)}</span>
									<BestMark {order} source="the project’s setting; a group can have its own" />
									{#if !readonly}
										{@const key = cmp.best_key}
										<HeaderMenu
											label={metricLabel(key)}
											best={{
												active: true,
												order,
												onbest: (o) => setBest(key, o),
												keys: data.summaryKeys.filter((k) => k !== key),
												onpick: (k) => setBest(k, order)
											}}
										/>
									{/if}
								</div>
								<div class={[CELL, 'justify-end']} style:flex={fit(DELTA_W)}>vs best</div>
								<div class={[CELL, 'justify-end']} style:flex={fit(STD_W)}>± repeats</div>
								<div class={[CELL, 'justify-end']} style:flex={fit(RUNS_W)}>Runs</div>
								{#each suggested.settings as key, i (key)}
									<div
										class={[CELL, i === primary.length - 1 && PRIMARY_END]}
										style:flex={fit(widthOf[key])}
										title={key}
									>
										<span class="truncate">{displayKey(key)}</span>
									</div>
								{/each}
								{#each suggested.metrics as key, i (key)}
									<div
										class={[
											CELL,
											'group/h justify-end gap-1',
											suggested.settings.length + i === primary.length - 1 && PRIMARY_END
										]}
										style:flex={fit(widthOf[key])}
										title="{key} of each group’s best run"
									>
										<span class="truncate">{metricName(key)}</span>
										{#if !readonly && data.summaryKeys.includes(key)}
											<HeaderMenu
												label={metricName(key)}
												best={{ active: false, order: null, onbest: (o) => setBest(key, o) }}
											/>
										{/if}
									</div>
								{/each}
								{#each keys as key (key)}
									<div class={CELL} style:flex={fit(widthOf[key])} title={key}>
										<span class="truncate">{displayKey(key)}</span>
									</div>
								{/each}
								<span class="min-w-5 flex-1" aria-hidden="true"></span>
							</div>
						</div>

						{#each cmp.rows as r (r.group ?? r.run)}
							{@render row(r, 'group')}
						{/each}

						{#if cmp.ungrouped_count > 0}
							<button
								type="button"
								id="ungrouped"
								class="flex h-10 w-full items-center gap-2 border-b border-divider bg-card pr-5 pl-5 text-left hover:bg-muted"
								aria-expanded={ungroupedOpen}
								onclick={() => (ungroupedOpen = !ungroupedOpen)}
							>
								<span class="sticky left-5 flex items-center gap-2">
									{#if ungroupedOpen}
										<ChevronDownIcon class="size-3.5 text-muted-foreground" />
									{:else}
										<ChevronRightIcon class="size-3.5 text-muted-foreground" />
									{/if}
									<span class="font-medium">Ungrouped runs</span>
									<span class="text-muted-foreground">
										{cmp.ungrouped_count} · in no group{readonly ? '' : ' · add them to one'}
									</span>
								</span>
							</button>
							{#if ungroupedOpen}
								{#each ungrouped as r (r.run ?? r.name)}
									{@render row(r, 'ungrouped')}
								{/each}
								{#if cmp.ungrouped.length > UNGROUPED_SHOWN}
									<div class="flex h-9 w-full items-center border-b border-divider">
										<button
											type="button"
											class="sticky left-0 flex items-center gap-1 pl-[52px] text-xs text-link hover:underline"
											onclick={() => (showAll = !showAll)}
										>
											{#if showAll}
												Show the best {UNGROUPED_SHOWN}
											{:else}
												<ChevronRightIcon class="size-3" />
												Show {cmp.ungrouped.length - UNGROUPED_SHOWN} more ungrouped runs
											{/if}
										</button>
									</div>
								{/if}
							{/if}
						{/if}
					</div>
				</section>

				{#if allOwn}
					<!-- Charts compare groups on the page's column; none ranks by it. -->
					<p class="px-5 py-6 text-[13px] text-muted-foreground">
						Each group ranks its runs by a column of its own, so none compare on
						{metricLabel(cmp.best_key)}. Rank by one of theirs from the
						{metricLabel(cmp.best_key)} column’s menu to chart them here.
					</p>
				{:else}
					<section
						class="flex min-h-[340px] flex-1 gap-4 px-5 pt-5 pb-6 max-lg:min-h-[640px] max-lg:flex-col"
						aria-label="Charts"
					>
						{#if !summaryOnly}
							<Card.Root class="min-h-[300px] min-w-0 flex-[3] rounded-lg px-1">
								<Card.Header class="gap-0.5">
									<Card.Title class="text-[13px]">
										{metricLabel(cmp.best_key)}
										<span class="font-normal text-muted-foreground">· each group’s best run</span>
									</Card.Title>
									<Card.Description class="text-xs">
										{order === 'asc' ? 'Lower' : 'Higher'} is better
									</Card.Description>
								</Card.Header>
								<Card.Content class="flex min-h-0 flex-1 flex-col">
									{#if !lines.length}
										<div class="grid flex-1 place-items-center text-[13px] text-faint">
											No group has a best run by {metricLabel(cmp.best_key)} yet
										</div>
									{:else if history}
										<CompareHistory
											data={history}
											{lines}
											metric={chartKey(cmp.best_key)}
											{hovered}
											onhover={(run) => (hovered = run)}
										/>
									{:else}
										<div class="grid flex-1 place-items-center text-[13px] text-faint">
											{historyFailed ? 'Couldn’t read the runs’ histories' : 'Reading histories…'}
										</div>
									{/if}
								</Card.Content>
							</Card.Root>
						{/if}
						<Card.Root
							class={[
								'min-h-[300px] min-w-0 flex-[2] rounded-lg px-1',
								!summaryOnly && 'lg:max-w-[46%]'
							]}
						>
							<Card.Header class="gap-0.5">
								<Card.Title class="text-[13px]">
									{metricLabel(cmp.best_key)}
									<span class="font-normal text-muted-foreground">· every run, by group</span>
								</Card.Title>
								<Card.Description class="text-xs">
									Faint: each run · solid: the best{spread ? ', ± std across repeats' : ''}
								</Card.Description>
							</Card.Header>
							<Card.Content class="flex min-h-0 flex-1 flex-col">
								{#if dots.length}
									<BestDots
										{dots}
										{hovered}
										format={fmt}
										formatSpread={fmtSpread}
										onhover={(run) => (hovered = run)}
									/>
								{:else}
									<div class="grid flex-1 place-items-center text-[13px] text-faint">
										No group has a value yet
									</div>
								{/if}
							</Card.Content>
						</Card.Root>
					</section>
				{/if}
			</div>
		{/if}
	</main>
</Page>
