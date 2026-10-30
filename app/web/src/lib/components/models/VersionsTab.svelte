<script lang="ts">
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import EyeIcon from '@lucide/svelte/icons/eye';
	import StarIcon from '@lucide/svelte/icons/star';
	import TagIcon from '@lucide/svelte/icons/tag';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import XIcon from '@lucide/svelte/icons/x';
	import type { ResolvedPathname } from '$app/types';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Switch } from '#lib/components/ui/switch/index.js';
	import { modelApi, modelCall, runPathHref, sortTags } from '#lib/models.ts';
	import type { ModelInfo, ModelVersion, ModelVersionsPage } from '#lib/types.ts';
	import { bytes, dateTime, relativeTime } from '#lib/utils.ts';
	import DeleteVersionDialog from './DeleteVersionDialog.svelte';
	import MakePrimaryDialog from './MakePrimaryDialog.svelte';
	import RemoveTagDialog from './RemoveTagDialog.svelte';
	import TagDialog from './TagDialog.svelte';

	interface Props {
		org: string;
		info: ModelInfo;
		/** The version the page shows, null while there is none. */
		shown: number | null;
		/** The page's URL at another `?version=`. */
		versionHref: (version: string | null) => ResolvedPathname;
		/** Called after a version, a tag or the primary changed. */
		onchange?: () => unknown;
		now: number;
	}

	let { org, info, shown, versionHref, onchange, now }: Props = $props();
	const name = $derived(info.name);

	// Newest first, 100 a page; the Checkpoints filter shows only versions with a checkpoint tag.
	let checkpoints = $state(false);
	let versions = $state<ModelVersion[]>([]);
	let cursor = $state<number | null>(null);
	let loaded = $state(false);
	let loading = $state(false);
	let failed = $state<string | null>(null);

	async function load(more = false) {
		loading = true;
		failed = null;
		try {
			const page = await modelCall<ModelVersionsPage>(
				modelApi(org, name, 'versions', {
					before: more ? cursor : null,
					checkpoints: checkpoints || null
				})
			);
			versions = more ? [...versions, ...page.versions] : page.versions;
			cursor = page.cursor;
			loaded = true;
		} catch (e) {
			failed = (e as Error).message;
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		void [name, checkpoints];
		void load();
	});

	function changed() {
		void load();
		void onchange?.();
	}

	let tagging = $state<number | null>(null);
	let removing = $state<{ tag: string; version: number } | null>(null);
	let promoting = $state<number | null>(null);
	let deleting = $state<ModelVersion | null>(null);
</script>

<div class="mb-3 flex items-center gap-2">
	<Switch id="checkpoints-only" bind:checked={checkpoints} />
	<Label for="checkpoints-only" class="font-normal">Checkpoints</Label>
</div>

{#if failed && !loaded}
	<p class="bg-card px-4 py-6 text-[13px] text-destructive" role="alert">{failed}</p>
{:else if !loaded}
	<p class="bg-card px-4 py-6 text-[13px] text-muted-foreground">Loading…</p>
{:else if !versions.length}
	<p class="bg-card px-4 py-6 text-[13px] text-muted-foreground">
		{checkpoints ? 'No version has a checkpoint tag.' : 'This model has no versions yet.'}
	</p>
{:else}
	<!-- Every cell starts on the same 24px line, so a row reads level whatever it holds. -->
	<table class="w-full border-collapse text-[13px] leading-6">
		<thead>
			<tr class="h-10 border-b text-left text-xs text-muted-foreground">
				<th class="w-px px-3 font-medium">Version</th>
				<th class="px-3 font-medium">Tags</th>
				<th class="w-px px-3 font-medium">Size</th>
				<th class="hidden w-px px-3 font-medium md:table-cell">Run</th>
				<th class="w-px px-3 font-medium">Pushed</th>
				<th class="w-px"><span class="sr-only">Actions</span></th>
			</tr>
		</thead>
		<tbody>
			{#each versions as v (v.number)}
				{@const runHref = v.run ? runPathHref(v.run) : null}
				<tr
					class={['border-b border-divider align-top', v.number === shown && 'bg-muted']}
					aria-current={v.number === shown ? 'true' : undefined}
				>
					<td class="px-3 py-3 whitespace-nowrap tabular-nums">
						<a
							class="underline-offset-3 hover:text-link hover:underline"
							href={versionHref(String(v.number))}>{v.number}</a
						>
						{#if v.primary}<Badge variant="secondary" class="ml-1.5">Primary</Badge>{/if}
					</td>
					<td class="px-3 py-3">
						{#if v.tags.length}
							<ul class="flex flex-wrap gap-1" aria-label="Tags of version {v.number}">
								{#each sortTags(v.tags) as tag (tag)}
									<li
										class="inline-flex h-6 max-w-full min-w-0 items-center gap-1 rounded-full bg-secondary pr-1 pl-2 text-xs"
									>
										<span class="truncate" title={tag}>{tag}</span>
										<button
											class="grid size-4 flex-none place-items-center rounded-full text-muted-foreground hover:bg-background hover:text-foreground"
											aria-label="Remove the tag {tag}"
											onclick={() => (removing = { tag, version: v.number })}
										>
											<XIcon class="size-3" />
										</button>
									</li>
								{/each}
							</ul>
						{:else}
							<span class="text-faint">None</span>
						{/if}
					</td>
					<td class="px-3 py-3 whitespace-nowrap text-muted-foreground tabular-nums">
						{bytes(v.total_bytes)}
					</td>
					<td class="hidden px-3 py-3 whitespace-nowrap text-muted-foreground md:table-cell">
						{#if v.run}
							{#if runHref}
								<a class="text-link underline-offset-3 hover:underline" href={runHref}
									>{v.run.split('/').at(-1)}</a
								>
							{:else}
								{v.run}
							{/if}
							{#if v.step !== null}
								<span class="tabular-nums">· step {v.step.toLocaleString('en-US')}</span>
							{/if}
						{:else if v.step !== null}
							<span class="tabular-nums">Step {v.step.toLocaleString('en-US')}</span>
						{:else}
							<span class="text-faint">None</span>
						{/if}
					</td>
					<td
						class="px-3 py-3 whitespace-nowrap text-muted-foreground tabular-nums"
						title={dateTime(v.created_at)}
					>
						{v.created_by ? `${v.created_by.name}, ` : ''}{relativeTime(v.created_at, now)}
					</td>
					<td class="py-2.5 pr-2 pl-3 whitespace-nowrap">
						<DropdownMenu.Root>
							<DropdownMenu.Trigger>
								{#snippet child({ props })}
									<Button
										{...props}
										variant="ghost"
										size="icon-sm"
										class="text-muted-foreground"
										aria-label="Version {v.number} actions"
									>
										<EllipsisIcon />
									</Button>
								{/snippet}
							</DropdownMenu.Trigger>
							<DropdownMenu.Content align="end" class="w-44">
								<DropdownMenu.Item>
									{#snippet child({ props })}
										<a {...props} href={versionHref(String(v.number))}>
											<EyeIcon /> View
										</a>
									{/snippet}
								</DropdownMenu.Item>
								<DropdownMenu.Item disabled={v.primary} onSelect={() => (promoting = v.number)}>
									<StarIcon /> Make primary
								</DropdownMenu.Item>
								<DropdownMenu.Item onSelect={() => (tagging = v.number)}>
									<TagIcon /> Add tag…
								</DropdownMenu.Item>
								<DropdownMenu.Separator />
								<DropdownMenu.Item variant="destructive" onSelect={() => (deleting = v)}>
									<Trash2Icon /> Delete…
								</DropdownMenu.Item>
							</DropdownMenu.Content>
						</DropdownMenu.Root>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
	{#if failed}
		<p class="mt-2 text-[13px] text-destructive" role="alert">{failed}</p>
	{/if}
	{#if cursor !== null}
		<div class="mt-3">
			<Button variant="outline" size="sm" disabled={loading} onclick={() => load(true)}>
				{loading ? 'Loading…' : 'Load more'}
			</Button>
		</div>
	{/if}
{/if}

<MakePrimaryDialog
	bind:open={() => promoting !== null, (o) => (promoting = o ? promoting : null)}
	{org}
	{name}
	number={promoting ?? 0}
	current={info.primary}
	onchanged={changed}
/>
<TagDialog
	bind:open={() => tagging !== null, (o) => (tagging = o ? tagging : null)}
	{org}
	{name}
	version={tagging ?? 0}
	ontagged={changed}
/>
<RemoveTagDialog
	bind:open={() => removing !== null, (o) => (removing = o ? removing : null)}
	{org}
	{name}
	tag={removing?.tag ?? ''}
	version={removing?.version ?? 0}
	onremoved={changed}
/>
<DeleteVersionDialog
	bind:open={() => deleting !== null, (o) => (deleting = o ? deleting : null)}
	{org}
	version={deleting}
	shown={deleting !== null && deleting.number === shown}
	ondeleted={changed}
/>
