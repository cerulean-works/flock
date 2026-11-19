<script lang="ts">
	import ArrowDownIcon from '@lucide/svelte/icons/arrow-down';
	import ArrowUpIcon from '@lucide/svelte/icons/arrow-up';
	import ArrowUpDownIcon from '@lucide/svelte/icons/arrow-up-down';
	import FilterIcon from '@lucide/svelte/icons/list-filter';
	import SettingsIcon from '@lucide/svelte/icons/settings-2';
	import StateBadge from '#lib/components/StateBadge.svelte';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import Page from '#lib/components/Page.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import * as Popover from '#lib/components/ui/popover/index.js';
	import { onMount } from 'svelte';
	import { invalidate } from '$app/navigation';
	import type { PageProps } from './$types';
	let { data }: PageProps = $props();
	const jobs = $derived(data.compute.jobs);
	const partitions = $derived(data.compute.partitions);
	const clusters = $derived(data.compute.clusters);
	let selectedClusters = $state<string[] | null>(null);
	const activeClusters = $derived(selectedClusters ?? clusters);
	onMount(() => {
		let stopped = false;
		let timer: ReturnType<typeof setTimeout>;
		async function refresh() {
			try {
				await invalidate('compute:snapshot');
			} finally {
				if (!stopped) timer = setTimeout(refresh, 10000);
			}
		}
		timer = setTimeout(refresh, 10000);
		return () => {
			stopped = true;
			clearTimeout(timer);
		};
	});

	let view = $state('Job queue');
	let partitionSearch = $state('');
	let partitionSort = $state<string | null>(null);
	let partitionDescending = $state(false);
	const partitionColumns = [
		'Partition',
		'Cluster',
		'Availability',
		'Time limit',
		'Allocated nodes',
		'Idle nodes',
		'Other nodes',
		'Running jobs',
		'Pending jobs'
	];
	type Partition = (typeof partitions)[number];
	function partitionValue(partition: Partition, column: string): string | number | null {
		const counts = jobs.filter(
			(job) =>
				job.cluster === partition.cluster &&
				job.partition === partition.name &&
				(!planOnly || planAccounts.includes(job.account))
		);
		const values: Record<string, string | number | null> = {
			Partition: partition.name,
			Cluster: partition.cluster,
			Availability: partition.availability,
			'Time limit': partition.limit,
			'Allocated nodes': partition.allocated,
			'Idle nodes': partition.idle,
			'Other nodes': partition.other,
			'Running jobs': counts.filter((job) => job.state === 'Running').length,
			'Pending jobs': counts.filter((job) => job.state === 'Pending').length
		};
		return values[column];
	}
	const visiblePartitions = $derived.by(() => {
		const rows = partitions.filter(
			(partition) =>
				activeClusters.includes(partition.cluster) &&
				(partition.name + ' ' + partition.cluster)
					.toLowerCase()
					.includes(partitionSearch.toLowerCase())
		);
		if (!partitionSort) return rows;
		const column = partitionSort;
		return rows.toSorted(
			(a, b) =>
				collator.compare(String(partitionValue(a, column)), String(partitionValue(b, column))) *
				(partitionDescending ? -1 : 1)
		);
	});
	function sortPartitions(column: string) {
		if (partitionSort === column && partitionDescending) {
			partitionSort = null;
			partitionDescending = false;
			return;
		}
		partitionDescending = partitionSort === column;
		partitionSort = column;
	}
	function openPartition(partition: Partition) {
		selectedClusters = [partition.cluster];
		filters = { ...emptyFilters, partition: [partition.name] };
		search = '';
		view = 'Job queue';
	}
	let search = $state('');
	let planOnly = $state(false);
	let sortColumn = $state<string | null>(null);
	let descending = $state(false);
	const planAccounts = $derived(data.compute.planAccounts);
	const filterFields = {
		state: 'State',
		owner: 'User',
		partition: 'Partition',
		account: 'Account',
		qos: 'QoS'
	};
	type FilterField = keyof typeof filterFields;
	const filterKeys = Object.keys(filterFields) as FilterField[];
	const emptyFilters: Record<FilterField, string[]> = {
		state: [],
		owner: [],
		partition: [],
		account: [],
		qos: []
	};
	let filters = $state({ ...emptyFilters });
	const filterCount = $derived(filterKeys.filter((key) => filters[key].length > 0).length);
	const optionsFor = (key: FilterField) => [...new Set(jobs.map((job) => job[key]))].sort();
	const scoped = $derived(
		jobs.filter(
			(job) =>
				activeClusters.includes(job.cluster) && (!planOnly || planAccounts.includes(job.account))
		)
	);
	const sumGpus = (rows: typeof jobs) =>
		rows.some((job) => job.gpus === null)
			? '—'
			: rows.reduce((total, job) => total + (job.gpus ?? 0), 0);
	const filtered = $derived(
		scoped.filter(
			(job) =>
				filterKeys.every((key) => !filters[key].length || filters[key].includes(job[key])) &&
				[
					job.name,
					job.id,
					job.owner,
					job.state,
					job.partition,
					job.account,
					job.qos,
					job.detail
				].some((field) => field.toLowerCase().includes(search.trim().toLowerCase()))
		)
	);
	const running = $derived(filtered.filter((job) => job.state === 'Running'));
	const pending = $derived(filtered.filter((job) => job.state === 'Pending'));
	type Job = (typeof jobs)[number];
	const sortValues: Record<string, (job: Job) => string | number | null> = {
		'Job ID': (job) => job.id,
		Name: (job) => job.name,
		User: (job) => job.owner,
		State: (job) => job.state,
		Cluster: (job) => job.cluster,
		Partition: (job) => job.partition,
		GPUs: (job) => job.gpus,
		Elapsed: (job) => (job.state === 'Running' ? job.elapsedSeconds : null),
		'Time limit': (job) => job.timeLimitSeconds,
		Nodes: (job) => job.nodes,
		'Node list / reason': (job) => job.nodeList ?? job.detail,
		Account: (job) => job.account,
		QoS: (job) => job.qos,
		Submitted: (job) => job.submitted,
		Started: (job) => job.started
	};
	const columns = Object.keys(sortValues);
	let hiddenColumns = $state<string[]>([]);
	const shownColumns = $derived(columns.filter((column) => !hiddenColumns.includes(column)));
	const widths: Record<string, number> = {
		Name: 220,
		State: 145,
		'Node list / reason': 230,
		GPUs: 100
	};
	const cell = (job: Job, column: string) => {
		if (column === 'Elapsed') return job.state === 'Running' ? job.detail : '—';
		if (column === 'Time limit') return job.timeLimit;
		return sortValues[column](job) ?? '—';
	};
	const filterKey = (column: string) => filterKeys.find((key) => filterFields[key] === column);
	function toggleColumn(column: string) {
		hiddenColumns = hiddenColumns.includes(column)
			? hiddenColumns.filter((name) => name !== column)
			: [...hiddenColumns, column];
	}
	function exportCsv() {
		const quote = (value: unknown) => {
			let text = String(value);
			if (/^[=+@-]/.test(text)) text = "'" + text;
			return '"' + text.replaceAll('"', '""') + '"';
		};
		const rows =
			view === 'Partitions'
				? [
						partitionColumns,
						...visiblePartitions.map((partition) =>
							partitionColumns.map((column) =>
								column === 'Time limit' ? partition.limitLabel : partitionValue(partition, column)
							)
						)
					]
				: [shownColumns, ...visible.map((job) => shownColumns.map((column) => cell(job, column)))];
		const blob = new Blob(['\ufeff' + rows.map((row) => row.map(quote).join(',')).join('\r\n')], {
			type: 'text/csv;charset=utf-8'
		});
		const url = URL.createObjectURL(blob);
		const link = document.createElement('a');
		link.href = url;
		link.download = view === 'Partitions' ? 'slurm-partitions.csv' : 'slurm-jobs.csv';
		link.click();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	}
	const numeric = new Set(['GPUs', 'Nodes']);
	const collator = new Intl.Collator('en', { numeric: true, sensitivity: 'base' });
	const visible = $derived.by(() => {
		if (!sortColumn) return filtered;
		const value = sortValues[sortColumn];
		return filtered.toSorted((a, b) => {
			const left = value(a);
			const right = value(b);
			if (left === null) return right === null ? 0 : 1;
			if (right === null) return -1;
			const order =
				typeof left === 'number' && typeof right === 'number'
					? left - right
					: collator.compare(String(left), String(right));
			return descending ? -order : order;
		});
	});

	let pageSize = $state(50);
	let pageChoice = $state({ key: '', number: 1 });
	let choosingSize = $state(false);
	const queryKey = $derived(
		JSON.stringify([selectedClusters, planOnly, search, filters, sortColumn, descending])
	);
	const pageCount = $derived(Math.max(1, Math.ceil(visible.length / pageSize)));
	const currentPage = $derived(
		Math.min(pageChoice.key === queryKey ? pageChoice.number : 1, pageCount)
	);
	const firstJob = $derived((currentPage - 1) * pageSize);
	const pageJobs = $derived(visible.slice(firstJob, firstJob + pageSize));
	function changePage(number: number) {
		pageChoice = { key: queryKey, number: Math.max(1, Math.min(number, pageCount)) };
	}
	function resizePage(value: string) {
		const first = firstJob;
		pageSize = Number(value);
		pageChoice = { key: queryKey, number: Math.floor(first / pageSize) + 1 };
		choosingSize = false;
	}

	function sortBy(column: string) {
		if (sortColumn === column && descending) {
			sortColumn = null;
			descending = false;
			return;
		}
		descending = sortColumn === column;
		sortColumn = column;
	}
</script>

<svelte:head><title>Compute · Flock</title></svelte:head>

{#snippet clusterFilter()}
	<Popover.Root>
		<Popover.Trigger>
			{#snippet child({ props })}
				<button
					{...props}
					aria-label="Filter Cluster"
					class={[
						'inline-flex size-5 shrink-0 items-center justify-center rounded group-focus-within:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100 [@media(hover:none)]:opacity-100',
						activeClusters.length !== clusters.length ? 'text-link opacity-100' : 'opacity-0'
					]}
				>
					<FilterIcon class="size-3" />
				</button>
			{/snippet}
		</Popover.Trigger>
		<Popover.Content class="w-52">
			<div class="mb-1 flex items-center justify-between">
				<span class="text-sm font-medium">Cluster</span>
				<button class="text-xs text-link" onclick={() => (selectedClusters = null)}>Show all</button
				>
			</div>
			{#each clusters as name (name)}
				<label
					class="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-muted"
					><input
						type="checkbox"
						checked={activeClusters.includes(name)}
						onchange={() =>
							(selectedClusters = activeClusters.includes(name)
								? activeClusters.filter((value) => value !== name)
								: [...activeClusters, name])}
					/>{name}</label
				>
			{/each}
		</Popover.Content>
	</Popover.Root>
{/snippet}

<Page width="full">
	<main class="flex min-h-0 flex-1 flex-col gap-5 overflow-hidden px-4 py-6 md:px-6 md:py-8">
		<header class="flex shrink-0 flex-wrap items-center justify-between gap-x-6 gap-y-3">
			<div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
				<h1 class="text-[34px] leading-[1.15] tracking-[-0.025em]">Compute</h1>
				<div id="queue" class="flex gap-3" role="group" aria-label="Compute view">
					{#each ['Job queue', 'Partitions'] as name (name)}
						<button
							class={[
								'text-sm',
								view === name ? 'text-foreground' : 'text-muted-foreground hover:text-foreground'
							]}
							aria-pressed={view === name}
							onclick={() => (view = name)}>{name}</button
						>
					{/each}
				</div>
			</div>
		</header>
		<section
			class="flex min-h-0 min-w-0 flex-1 flex-col gap-3 border-t border-divider pt-4"
			aria-labelledby="queue"
		>
			{#if data.compute.message}<p class="text-sm text-muted-foreground" role="status">
					{data.compute.message}
				</p>{/if}
			<div class="flex shrink-0 flex-wrap items-center justify-between gap-2">
				<div class="flex flex-wrap items-center gap-2">
					{#if view === 'Job queue'}
						<Input
							type="search"
							class="w-full sm:w-64"
							placeholder="Search jobs"
							aria-label="Search jobs"
							bind:value={search}
						/>
					{:else}
						<Input
							type="search"
							class="w-full sm:w-64"
							placeholder="Search partitions"
							aria-label="Search partitions"
							bind:value={partitionSearch}
						/>
					{/if}
					<Button
						size="sm"
						variant={planOnly ? 'secondary' : 'outline'}
						aria-pressed={planOnly}
						disabled={!planAccounts.length}
						title={planAccounts.length
							? planAccounts.join(', ')
							: 'Plan accounts are not configured'}
						onclick={() => (planOnly = !planOnly)}>Our plan</Button
					>
				</div>
				<div class="flex items-center gap-3">
					<Popover.Root>
						<Popover.Trigger>
							{#snippet child({ props })}
								<Button {...props} variant="ghost" size="icon-sm" aria-label="Table settings"
									><SettingsIcon class="size-4" /></Button
								>
							{/snippet}
						</Popover.Trigger>
						<Popover.Content align="end" class="max-h-96 w-56 overflow-auto">
							{#if view === 'Job queue'}
								<div class="mb-3 flex items-center justify-between">
									<span class="text-sm font-medium">Columns</span>
									<button class="text-xs text-link" onclick={() => (hiddenColumns = [])}
										>Show all</button
									>
								</div>
								{#each columns as column (column)}
									<label class="flex items-center gap-2 py-1 text-sm">
										<input
											type="checkbox"
											checked={!hiddenColumns.includes(column)}
											disabled={shownColumns.length === 1 && shownColumns[0] === column}
											onchange={() => toggleColumn(column)}
										/>{column}
									</label>
								{/each}
							{/if}
							<div class="mt-3 grid gap-1 border-t border-divider pt-3">
								<Button size="sm" variant="outline" onclick={exportCsv}>Export CSV</Button>
								{#if filterCount}<Button
										size="sm"
										variant="ghost"
										onclick={() => (filters = { ...emptyFilters })}
										>Clear filters ({filterCount})</Button
									>{/if}
							</div>
						</Popover.Content>
					</Popover.Root>
				</div>
			</div>
			{#if view === 'Partitions'}
				<div class="min-h-0 flex-1 overflow-auto">
					<table class="w-full table-fixed text-left text-sm tabular-nums" style:min-width="1300px">
						<thead class="bg-background text-xs text-muted-foreground"
							><tr>
								{#each partitionColumns as column (column)}
									<th
										class="group h-10 border-b border-border px-3 font-medium hover:bg-muted"
										aria-sort={partitionSort === column
											? partitionDescending
												? 'descending'
												: 'ascending'
											: undefined}
									>
										<div class="flex items-center gap-1">
											<button
												class="inline-flex items-center gap-1"
												onclick={() => sortPartitions(column)}
												>{column}<span
													class={[
														'inline-flex size-3 shrink-0 items-center justify-center group-focus-within:opacity-100 group-hover:opacity-100',
														partitionSort === column ? 'opacity-100' : 'opacity-0'
													]}
													>{#if partitionSort === column}{#if partitionDescending}<ArrowDownIcon
																class="size-3"
															/>{:else}<ArrowUpIcon class="size-3" />{/if}{:else}<ArrowUpDownIcon
															class="size-3"
															aria-hidden="true"
														/>{/if}</span
												></button
											>
											{#if column === 'Cluster'}{@render clusterFilter()}{/if}
										</div>
									</th>
								{/each}
							</tr></thead
						>
						<tbody
							>{#each visiblePartitions as partition (partition.key)}
								<tr class="h-10 border-b border-divider hover:bg-muted">
									{#each partitionColumns as column (column)}
										<td
											class={['h-10 px-3', partitionColumns.indexOf(column) >= 3 && 'text-right']}
										>
											{#if column === 'Partition'}<button
													class="text-link hover:underline"
													title="View jobs in this partition"
													onclick={() => openPartition(partition)}>{partition.name}</button
												>
											{:else if column === 'Availability'}<StateBadge
													state={partition.availability}
												/>
											{:else if column === 'Time limit'}{partition.limitLabel}
											{:else}{partitionValue(partition, column) ?? '—'}{/if}
										</td>
									{/each}
								</tr>
							{:else}<tr
									><td colspan={partitionColumns.length} class="p-4 text-muted-foreground"
										>No partitions match these filters.</td
									></tr
								>{/each}</tbody
						>
					</table>
				</div>
				<p class="text-xs text-muted-foreground">
					Node counts are per partition. Idle nodes may have unavailable GPUs.
				</p>
			{:else}
				<div class="min-h-0 flex-1 overflow-auto">
					<table
						class="w-full table-fixed border-collapse text-left text-sm tabular-nums"
						style:min-width={shownColumns.reduce(
							(total, column) => total + (widths[column] ?? 140),
							0
						) + 'px'}
					>
						<colgroup
							>{#each shownColumns as column (column)}<col
									style:width={(widths[column] ?? 140) + 'px'}
								/>{/each}</colgroup
						>
						<thead class="sticky top-0 z-10 bg-background text-xs text-muted-foreground">
							<tr
								>{#each shownColumns as column (column)}
									{@const key = filterKey(column)}
									<th
										scope="col"
										aria-sort={sortColumn === column
											? descending
												? 'descending'
												: 'ascending'
											: undefined}
										class="group h-10 border-b border-border px-3 font-medium hover:bg-muted"
									>
										<div class="flex items-center gap-1">
											<button
												class="flex min-w-0 flex-1 items-center justify-between gap-1 hover:text-foreground"
												onclick={() => sortBy(column)}
											>
												<span class="truncate">{column}</span>
												<span
													class={[
														'inline-flex size-5 shrink-0 items-center justify-center group-focus-within:opacity-100 group-hover:opacity-100',
														sortColumn === column ? 'opacity-100' : 'opacity-0'
													]}
													>{#if sortColumn === column}{#if descending}<ArrowDownIcon
																class="size-3"
															/>{:else}<ArrowUpIcon class="size-3" />{/if}{:else}<ArrowUpDownIcon
															class="size-3"
															aria-hidden="true"
														/>{/if}</span
												>
											</button>
											{#if column === 'Cluster'}{@render clusterFilter()}{/if}
											{#if key}
												<Popover.Root>
													<Popover.Trigger>
														{#snippet child({ props })}
															<button
																{...props}
																aria-label={`Filter ${column}`}
																class={[
																	'inline-flex size-5 shrink-0 items-center justify-center rounded group-hover:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100 [@media(hover:none)]:opacity-100',
																	filters[key].length ? 'text-link opacity-100' : 'opacity-0'
																]}
															>
																<FilterIcon class="size-3" />
															</button>
														{/snippet}
													</Popover.Trigger>
													<Popover.Content class="w-52">
														<div class="grid gap-1">
															<div class="mb-1 flex items-center justify-between">
																<span class="text-sm font-medium">{column}</span>
																<button
																	class="text-xs text-link disabled:text-muted-foreground"
																	disabled={!filters[key].length}
																	onclick={() => (filters[key] = [])}>Clear</button
																>
															</div>
															{#each optionsFor(key) as option (option)}
																<label
																	class="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-muted"
																>
																	<input
																		type="checkbox"
																		value={option}
																		bind:group={filters[key]}
																	/>{option}
																</label>
															{/each}
														</div>
													</Popover.Content>
												</Popover.Root>
											{/if}
										</div>
									</th>
								{/each}</tr
							>
						</thead>
						<tbody>
							{#each pageJobs as job (job.key)}
								<tr class="h-10 border-b border-divider hover:bg-muted">
									{#each shownColumns as column (column)}
										<td
											class={['h-10 truncate px-3', numeric.has(column) && 'text-right']}
											title={String(cell(job, column))}
										>
											{#if column === 'State'}
												<StateBadge state={job.state.toLowerCase()} />
											{:else}{cell(job, column)}{/if}
										</td>
									{/each}
								</tr>
							{:else}<tr
									><td colspan={shownColumns.length} class="p-4 text-muted-foreground"
										>No jobs match these filters.</td
									></tr
								>{/each}
						</tbody>
						<tfoot class="sticky bottom-0 bg-background text-xs text-muted-foreground">
							<tr
								>{#each shownColumns as column (column)}
									<td class={['px-3 py-2.5', numeric.has(column) && 'text-right']}>
										{#if column === 'Job ID'}{visible.length} jobs
										{:else if column === 'State'}
											<div class="flex gap-3">
												<span class="inline-flex items-center gap-1.5" title="Running jobs"
													><span class="size-1.5 rounded-full bg-state-running" aria-hidden="true"
													></span>{running.length}<span class="sr-only"> running</span></span
												>
												<span class="inline-flex items-center gap-1.5" title="Pending jobs"
													><span
														class="size-1.5 rounded-full bg-state-preempting"
														aria-hidden="true"
													></span>{pending.length}<span class="sr-only"> pending</span></span
												>
											</div>
										{:else if column === 'GPUs'}<div title="GPUs allocated">
												{sumGpus(running)} alloc.
											</div>
											<div title="GPUs requested by pending jobs">{sumGpus(pending)} req.</div>{/if}
									</td>
								{/each}</tr
							>
						</tfoot>
					</table>
				</div>
				<nav
					class="flex h-10 w-fit shrink-0 items-center gap-1 rounded-xl bg-popover py-1 pr-1 pl-3.5 text-[13px] tabular-nums shadow-md ring-1 ring-foreground/10"
					aria-label="Job pages"
				>
					<Button
						variant="ghost"
						size="icon-sm"
						disabled={currentPage <= 1}
						class="shrink-0"
						aria-label="Previous page"
						onclick={() => changePage(currentPage - 1)}><ChevronLeftIcon /></Button
					>
					<Button
						variant="ghost"
						size="icon-sm"
						disabled={currentPage >= pageCount}
						class="shrink-0"
						aria-label="Next page"
						onclick={() => changePage(currentPage + 1)}><ChevronRightIcon /></Button
					>
					<span>{visible.length ? firstJob + 1 : 0}–{firstJob + pageJobs.length}</span>
					<span class="mr-1 text-muted-foreground">of {visible.length}</span>
					<span class="mx-0.5 h-5 w-px bg-border" aria-hidden="true"></span>
					<DropdownMenu.Root bind:open={choosingSize}>
						<DropdownMenu.Trigger>
							{#snippet child({ props })}
								<Button
									{...props}
									variant="ghost"
									size="sm"
									class="gap-1 px-2 font-normal"
									aria-label="Jobs per page"
									>{pageSize} per page<ChevronDownIcon class="text-muted-foreground" /></Button
								>
							{/snippet}
						</DropdownMenu.Trigger>
						<DropdownMenu.Content side="top" align="end" class="w-40">
							<DropdownMenu.Group>
								<DropdownMenu.Label>Jobs per page</DropdownMenu.Label>
								<DropdownMenu.RadioGroup value={String(pageSize)} onValueChange={resizePage}>
									{#each [25, 50, 100] as size (size)}<DropdownMenu.RadioItem value={String(size)}
											>{size}</DropdownMenu.RadioItem
										>{/each}
								</DropdownMenu.RadioGroup>
							</DropdownMenu.Group>
						</DropdownMenu.Content>
					</DropdownMenu.Root>
				</nav>
			{/if}
		</section>
	</main>
</Page>
