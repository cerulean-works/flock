<script lang="ts">
	import CheckIcon from '@lucide/svelte/icons/check';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import type { ResolvedPathname } from '$app/types';
	import { goto } from '$app/navigation';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { modelApi, modelCall, sortTags } from '#lib/models.ts';
	import type { ModelTag, ModelVersion, ModelVersionsPage } from '#lib/types.ts';

	interface Props {
		org: string;
		name: string;
		/** The version shown. */
		version: number;
		/** What the URL asked for (a number or a tag), null for the primary. */
		requested: string | null;
		/** The primary version's number. */
		primary: number | null;
		tags: ModelTag[];
		/** The page's URL at another `?version=` (null: the primary). */
		versionHref: (version: string | null) => ResolvedPathname;
	}

	let { org, name, version, requested, primary, tags, versionHref }: Props = $props();

	let open = $state(false);
	let versions = $state<ModelVersion[] | null>(null);
	let failed = $state<string | null>(null);

	// The newest versions, read when the menu first opens.
	$effect(() => {
		if (!open || versions !== null) return;
		const controller = new AbortController();
		modelCall<ModelVersionsPage>(modelApi(org, name, 'versions'), { signal: controller.signal })
			.then((page) => (versions = page.versions))
			.catch((e: Error) => {
				if (!controller.signal.aborted) failed = e.message;
			});
		return () => controller.abort();
	});

	const sorted = $derived(sortTags(tags));
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
	<DropdownMenu.Content align="start" class="max-h-96 w-72 overflow-y-auto">
		<DropdownMenu.Group>
			<DropdownMenu.Label>Tags</DropdownMenu.Label>
			{#if primary !== null}
				<DropdownMenu.Item onSelect={() => choose(null)}>
					<span class="min-w-0 flex-1 truncate">primary</span>
					<span class="text-muted-foreground tabular-nums">version {primary}</span>
				</DropdownMenu.Item>
			{/if}
			{#each sorted as tag (tag.name)}
				<DropdownMenu.Item onSelect={() => choose(tag.name)}>
					<span class="min-w-0 flex-1 truncate">{tag.name}</span>
					<span class="text-muted-foreground tabular-nums">version {tag.version}</span>
				</DropdownMenu.Item>
			{/each}
		</DropdownMenu.Group>
		<DropdownMenu.Separator />
		<DropdownMenu.Group>
			<DropdownMenu.Label>Versions</DropdownMenu.Label>
			{#if failed}
				<p class="px-2 py-1.5 text-[13px] text-destructive">{failed}</p>
			{:else if versions === null}
				<p class="px-2 py-1.5 text-[13px] text-muted-foreground">Loading…</p>
			{:else}
				{#each versions as v (v.number)}
					<DropdownMenu.Item onSelect={() => choose(String(v.number))}>
						<span class="grid size-4 place-items-center">
							{#if v.number === version}<CheckIcon />{/if}
						</span>
						<span class="tabular-nums">Version {v.number}</span>
						<span class="min-w-0 flex-1 truncate text-right text-muted-foreground">
							{[v.primary ? 'primary' : null, ...sortTags(v.tags)].filter(Boolean).join(', ')}
						</span>
					</DropdownMenu.Item>
				{/each}
			{/if}
		</DropdownMenu.Group>
	</DropdownMenu.Content>
</DropdownMenu.Root>
