<script lang="ts">
	import EmptyState from '#lib/components/EmptyState.svelte';
	import type { DatasetRows } from '#lib/types.ts';
	import { count } from '#lib/utils.ts';
	import DatasetRowsTable from './DatasetRowsTable.svelte';
	import RowsPager from './RowsPager.svelte';

	interface Props {
		entity: string;
		dataset: string;
		/** The version to read; the latest when null. */
		version: number | null;
	}

	let { entity, dataset, version }: Props = $props();

	const SIZES = [25, 50, 100, 250, 500] as const;
	let size = $state(100);
	let offset = $state(0);
	let rows = $state.raw<DatasetRows | null>(null);
	let loading = $state(false);
	let failed = $state<string | null>(null);

	const url = $derived(
		`/api/datasets/${encodeURIComponent(entity)}/${encodeURIComponent(dataset)}/rows`
	);

	// Another version starts again from the first row.
	$effect.pre(() => {
		void [url, version];
		offset = 0;
	});

	$effect(() => {
		const search = `offset=${offset}&limit=${size}${version != null ? `&version=${version}` : ''}`;
		const ctl = new AbortController();
		loading = true;
		failed = null;
		fetch(`${url}?${search}`, { signal: ctl.signal })
			.then(async (res) => {
				if (!res.ok) {
					const body = (await res.json().catch(() => null)) as { message?: string } | null;
					throw new Error(body?.message ?? `Flock answered ${res.status}`);
				}
				rows = (await res.json()) as DatasetRows;
			})
			.catch((e: unknown) => {
				if (ctl.signal.aborted) return;
				failed = e instanceof Error ? e.message : String(e);
			})
			.finally(() => {
				if (!ctl.signal.aborted) loading = false;
			});
		return () => ctl.abort();
	});
</script>

<div class="flex h-full min-h-0 flex-col">
	<div class="flex h-11 flex-none items-center gap-3 border-b border-divider px-5">
		<span class="text-[13px] text-muted-foreground">
			{#if rows}
				{rows.columns.length} columns · version {rows.version}
			{:else if loading}
				Loading rows…
			{/if}
		</span>
		{#if rows?.truncated.length}
			<span class="text-[13px] text-muted-foreground max-md:hidden">
				· {count(rows.truncated.length, 'long value')} cut short
			</span>
		{/if}
		<div class="ml-auto">
			<RowsPager
				{offset}
				count={rows?.rows.length ?? 0}
				total={rows?.total ?? 0}
				{size}
				sizes={SIZES}
				disabled={loading}
				onoffset={(o) => (offset = o)}
				onsize={(s) => {
					offset = Math.floor(offset / s) * s;
					size = s;
				}}
			/>
		</div>
	</div>
	<div class={['relative min-h-0 flex-1', loading && rows && 'opacity-60']} aria-busy={loading}>
		{#if failed}
			<EmptyState tone="error">Couldn’t read the rows: {failed}</EmptyState>
		{:else if rows && !rows.total}
			<EmptyState>This version has no rows.</EmptyState>
		{:else if rows}
			<DatasetRowsTable page={rows} />
		{/if}
	</div>
</div>
