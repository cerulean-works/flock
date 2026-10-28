<script lang="ts">
	import CheckIcon from '@lucide/svelte/icons/check';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import type { ResolvedPathname } from '$app/types';
	import { goto } from '$app/navigation';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { datasetCall, datasetsApi } from '#lib/datasets.ts';
	import type { DatasetVersion, VersionsPage } from '#lib/types.ts';

	interface Props {
		org: string;
		path: string;
		/** The version shown. */
		version: number;
		/** What the URL asked for (a number or a tag), null for the latest. */
		requested: string | null;
		tags: { name: string; version: number }[];
		/** The page's URL at another `?version=` (null: the latest). */
		versionHref: (version: string | null) => ResolvedPathname;
	}

	let { org, path, version, requested, tags, versionHref }: Props = $props();

	let open = $state(false);
	let versions = $state<DatasetVersion[] | null>(null);
	let latest = $state<number | null>(null);
	let failed = $state<string | null>(null);

	// The newest versions, read when the menu first opens.
	$effect(() => {
		if (!open || versions !== null) return;
		const controller = new AbortController();
		datasetCall<VersionsPage>(datasetsApi(org, 'versions', { path }), {
			signal: controller.signal
		})
			.then((page) => {
				versions = page.versions;
				latest = page.latest;
			})
			.catch((e: Error) => {
				if (!controller.signal.aborted) failed = e.message;
			});
		return () => controller.abort();
	});

	const tagged = $derived(requested !== null && !/^\d+$/.test(requested) ? requested : null);

	function choose(value: string | null) {
		open = false;

		void goto(versionHref(value), { reset: false });
	}
</script>

<DropdownMenu.Root bind:open>
	<DropdownMenu.Trigger>
		{#snippet child({ props })}
			<Button {...props} variant="outline" size="sm" class="gap-1.5 font-normal">
				Version {version}{#if tagged}<span class="text-muted-foreground">· {tagged}</span>{/if}
				<ChevronDownIcon class="text-muted-foreground" />
			</Button>
		{/snippet}
	</DropdownMenu.Trigger>
	<DropdownMenu.Content align="start" class="max-h-96 w-64 overflow-y-auto">
		{#if tags.length}
			<DropdownMenu.Group>
				<DropdownMenu.Label>Tags</DropdownMenu.Label>
				{#each tags as tag (tag.name)}
					<DropdownMenu.Item onSelect={() => choose(tag.name)}>
						<span class="min-w-0 flex-1 truncate">{tag.name}</span>
						<span class="text-muted-foreground tabular-nums">version {tag.version}</span>
					</DropdownMenu.Item>
				{/each}
			</DropdownMenu.Group>
			<DropdownMenu.Separator />
		{/if}
		<DropdownMenu.Group>
			<DropdownMenu.Label>Versions</DropdownMenu.Label>
			{#if failed}
				<p class="px-2 py-1.5 text-[13px] text-destructive">{failed}</p>
			{:else if versions === null}
				<p class="px-2 py-1.5 text-[13px] text-muted-foreground">Loading…</p>
			{:else}
				{#each versions as v (v.version)}
					<DropdownMenu.Item onSelect={() => choose(String(v.version))}>
						<span class="grid size-4 place-items-center">
							{#if v.version === version}<CheckIcon />{/if}
						</span>
						<span class="tabular-nums">Version {v.version}</span>
						<span class="min-w-0 flex-1 truncate text-right text-muted-foreground">
							{[v.version === latest ? 'latest' : null, ...v.tags].filter(Boolean).join(', ')}
						</span>
					</DropdownMenu.Item>
				{/each}
			{/if}
		</DropdownMenu.Group>
	</DropdownMenu.Content>
</DropdownMenu.Root>
