<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import CopyButton from '#lib/components/CopyButton.svelte';
	import DatasetCard from '#lib/components/datasets/DatasetCard.svelte';
	import DatasetSettings from '#lib/components/datasets/DatasetSettings.svelte';
	import DatasetUse from '#lib/components/datasets/DatasetUse.svelte';
	import DatasetViewer from '#lib/components/datasets/DatasetViewer.svelte';
	import HubOff from '#lib/components/datasets/HubOff.svelte';
	import VersionsTimeline from '#lib/components/datasets/VersionsTimeline.svelte';
	import { VISIBILITY } from '#lib/components/datasets/visibility.ts';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import * as Tabs from '#lib/components/ui/tabs/index.js';
	import { repoId } from '#lib/datasets.ts';
	import { bytes, count } from '#lib/utils.ts';
	import { datasetHref, datasetsHref, withParam } from '#lib/workspace/url.ts';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();

	type Tab = 'card' | 'viewer' | 'versions' | 'use' | 'settings';
	const tabs = $derived.by(() => {
		const all: { value: Tab; label: string }[] = [
			{ value: 'card', label: 'Card' },
			{ value: 'viewer', label: 'Viewer' },
			{ value: 'versions', label: 'Versions' },
			{ value: 'use', label: 'Use' }
		];
		// Outsiders reading an internal dataset change nothing.
		if (data.hub && data.dataset.role) all.push({ value: 'settings', label: 'Settings' });
		return all;
	});

	// In the URL, so a tab can be linked to; switching never re-runs the load (it reads `v` only).
	let tabOverride = $state<Tab | null>(null);
	const urlTab = $derived<Tab>(
		tabs.find((t) => t.value === page.url.searchParams.get('tab'))?.value ?? 'card'
	);
	const tab = $derived(tabOverride ?? urlTab);

	function setTab(t: Tab) {
		if (t === tab) return;
		tabOverride = t;
		void goto(withParam(page.url, 'tab', t === 'card' ? null : t), {
			replace: true,
			reset: false
		}).finally(() => (tabOverride = null));
	}

	const readable = $derived(data.hub ? data.versions.filter((v) => !v.pruned) : []);

	function setVersion(value: string) {
		if (!data.hub) return;
		const v = Number(value);
		void goto(withParam(page.url, 'v', v === data.dataset.latest_version ? null : String(v)), {
			reset: false
		});
	}

	const versionLabel = (v: number) =>
		data.hub && v === data.dataset.latest_version ? `v${v} · latest` : `v${v}`;
</script>

<svelte:head>
	<title>{params.dataset} · Datasets</title>
</svelte:head>

{#if !data.hub}
	<PageHeader title={params.dataset} />
	<main class="min-h-0 flex-1 overflow-y-auto"><HubOff /></main>
{:else}
	{@const d = data.dataset}
	<PageHeader title={d.name} subtitleTitle={d.description || undefined}>
		{#snippet badges()}
			<Badge
				variant="secondary"
				class="text-muted-foreground"
				title={VISIBILITY[d.visibility].hint}
			>
				{VISIBILITY[d.visibility].label}
			</Badge>
		{/snippet}
		{#snippet subtitle()}
			<a class="hover:text-link" href={datasetsHref(d.org)}>{d.org}</a>
			{#if d.latest_version != null}
				· v{d.latest_version}
				{#if d.rows != null}· {count(d.rows, 'row')}{/if}
				{#if d.bytes != null}· {bytes(d.bytes)}{/if}
			{:else}
				· no versions yet
			{/if}
			{#if d.mirror}
				· mirror of {d.mirror.repo}{#if d.mirror.config}&nbsp;({d.mirror.config}){/if}
			{/if}
		{/snippet}
		{#snippet actions()}
			<span class="flex max-w-[22rem] min-w-0 items-center gap-0.5 max-lg:hidden">
				<code class="truncate font-mono text-[12.5px] text-muted-foreground" title="Its Lance URI"
					>{d.uri}</code
				>
				<CopyButton text={d.uri} label="Copy the URI" />
			</span>
		{/snippet}
		{#snippet toolbar()}
			<Tabs.Root value={tab} onValueChange={(v) => setTab(v as Tab)}>
				<Tabs.List variant="line" aria-label="Dataset">
					{#each tabs as t (t.value)}
						<Tabs.Trigger value={t.value}>{t.label}</Tabs.Trigger>
					{/each}
				</Tabs.List>
			</Tabs.Root>
			{#if readable.length && data.version != null}
				<div class="ml-auto flex items-center gap-2">
					<span class="text-[13px] text-muted-foreground max-sm:hidden" id="version-label">
						Version
					</span>
					<Select.Root type="single" value={String(data.version)} onValueChange={setVersion}>
						<Select.Trigger size="sm" class="min-w-28" aria-labelledby="version-label">
							{versionLabel(data.version)}
						</Select.Trigger>
						<Select.Content align="end" class="max-h-80">
							{#each readable as v (v.version)}
								<Select.Item value={String(v.version)} label={versionLabel(v.version)}>
									<span class="tabular-nums">{versionLabel(v.version)}</span>
									{#if v.tags.length}
										<span class="truncate font-mono text-xs text-muted-foreground">
											{v.tags.join(', ')}
										</span>
									{/if}
								</Select.Item>
							{/each}
						</Select.Content>
					</Select.Root>
				</div>
			{/if}
		{/snippet}
	</PageHeader>

	{#if data.pinned}
		<p class="flex-none border-b border-divider bg-selection/30 px-5 py-2 text-[13px]">
			Showing version {data.version}, not the latest.
			<a class="text-link underline underline-offset-3" href={withParam(page.url, 'v', null)}>
				Show the latest
			</a>
		</p>
	{/if}

	{#if tab === 'viewer'}
		<main class="min-h-0 flex-1">
			{#if d.latest_version == null}
				<p class="px-5 py-6 text-muted-foreground">No rows yet: the dataset has no versions.</p>
			{:else}
				<DatasetViewer
					entity={d.org}
					dataset={d.name}
					version={data.pinned ? data.version : null}
				/>
			{/if}
		</main>
	{:else}
		<main class="min-h-0 flex-1 overflow-y-auto">
			{#if tab === 'card'}
				{#if d.description}
					<p
						class="mx-auto max-w-[44rem] px-5 pt-8 font-serif text-[22px] leading-snug text-muted-foreground"
					>
						{d.description}
					</p>
				{/if}
				{#key data.readme.sha}
					<DatasetCard
						readme={data.readme}
						editable={!!d.role}
						pinned={data.pinned}
						empty={d.latest_version == null}
						repo={repoId(d.org, d.name)}
					/>
				{/key}
			{:else if tab === 'versions'}
				<VersionsTimeline
					versions={data.versions}
					total={data.totalVersions}
					latest={d.latest_version}
					current={data.version}
					role={d.role}
					me={data.shell.me.id}
					now={data.now}
					viewHref={(v) => datasetHref(d.org, d.name, { v, tab: 'viewer' })}
				/>
			{:else if tab === 'use'}
				<DatasetUse
					dataset={d}
					version={data.pinned ? data.version : null}
					flockUrl={page.data.flockUrl}
					s3={data.s3}
				/>
			{:else if tab === 'settings'}
				<DatasetSettings dataset={d} />
			{/if}
		</main>
	{/if}
{/if}
