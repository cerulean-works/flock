<script lang="ts">
	import SlidersHorizontalIcon from '@lucide/svelte/icons/sliders-horizontal';
	import SparklesIcon from '@lucide/svelte/icons/sparkles';
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import Page from '#lib/components/Page.svelte';
	import AutoGroups from '#lib/components/groups/AutoGroups.svelte';
	import GroupEditor from '#lib/components/groups/GroupEditor.svelte';
	import * as Tabs from '#lib/components/ui/tabs/index.js';
	import { fromFlockFilter, newGroupHref, rowsOf, type Proposal } from '#lib/groups.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const entity = $derived(page.params.entity!);
	const project = $derived(page.params.project!);

	/**
	 * Two ways to make groups: **Auto**, where the assistant proposes them in a conversation
	 * (the default when it's on), and **Manual**, the editor (D223). A URL that brings a draft
	 * along (a suggestion, an ungrouped run) opens Manual; `?tab=` picks one.
	 */
	type Tab = 'auto' | 'manual';
	const available = $derived(!!data.groupList?.assistant);
	const prefilled = $derived(
		data.prefill.runs.length > 0 || data.prefill.filters.length > 0 || !!data.prefill.name
	);
	const asked = $derived(page.url.searchParams.get('tab'));
	const initial = (): Tab =>
		asked === 'auto' || asked === 'manual' ? asked : prefilled || !available ? 'manual' : 'auto';

	let tab = $state<Tab>(untrack(initial));
	/** Auto stays mounted once opened, so its conversation survives a trip to Manual. */
	let autoOpened = $state(untrack(() => tab === 'auto'));
	/** Manual too, so its draft survives a trip to Auto. */
	let manualOpened = $state(untrack(() => tab === 'manual'));

	// A new draft in the URL (a sidebar suggestion, **Edit manually**) shows Manual.
	$effect.pre(() => {
		void page.url.search;
		if (untrack(() => prefilled)) untrack(() => show('manual'));
	});

	function show(next: Tab) {
		tab = next;
		if (next === 'auto') autoOpened = true;
		else manualOpened = true;
	}

	/** Manual, seeded with a proposal's runs, filter and name when one is given. */
	function manual(p?: Proposal, name?: string) {
		if (!p) return show('manual');
		void goto(
			newGroupHref(entity, project, {
				runs: p.runs,
				filters: fromFlockFilter(p.filter ?? {}) ?? [],
				name: name?.trim() || p.name
			})
		).then(() => show('manual'));
	}

	const rows = $derived(rowsOf(data.runs));
</script>

<svelte:head>
	<title>New group · {project} · {data.shell.orgName ?? entity}</title>
</svelte:head>

<Page width="full">
	<div class="flex min-h-0 flex-1 flex-col">
		<!-- The two ways in, centred above either. -->
		<div class="flex flex-none justify-center pt-4">
			<Tabs.Root value={tab} onValueChange={(v) => show(v as Tab)}>
				<Tabs.List aria-label="How to make groups" class="h-9!">
					<Tabs.Trigger value="auto" class="px-3" title="The assistant proposes groups">
						<SparklesIcon />Auto
					</Tabs.Trigger>
					<Tabs.Trigger value="manual" class="px-3" title="Pick runs with filters and by hand">
						<SlidersHorizontalIcon />Manual
					</Tabs.Trigger>
				</Tabs.List>
			</Tabs.Root>
		</div>

		{#if autoOpened}
			<div class={['min-h-0 flex-1 flex-col', tab === 'auto' ? 'flex' : 'hidden']}>
				<AutoGroups
					{entity}
					{project}
					{rows}
					groupList={data.groupList}
					{available}
					onmanual={manual}
				/>
			</div>
		{/if}

		{#if manualOpened}
			<!-- Keyed by the URL's prefill, so a suggestion opened from here starts a fresh draft. -->
			<div class={['min-h-0 flex-1 flex-col', tab === 'manual' ? 'flex' : 'hidden']}>
				{#key page.url.search}
					<GroupEditor
						{entity}
						{project}
						runs={data.runs}
						summaryKeys={data.summaryKeys}
						people={data.people}
						groupList={data.groupList}
						prefill={data.prefill}
					/>
				{/key}
			</div>
		{/if}
	</div>
</Page>
