<script lang="ts">
	import { untrack } from 'svelte';
	import * as Select from '#lib/components/ui/select/index.js';
	import { datasetCall, datasetsApi } from '#lib/datasets.ts';
	import type { DatasetVersion, VersionsPage } from '#lib/types.ts';

	// A dataset's version, for a blend source or a fork: the latest (an empty value), or one of
	// its versions, each shown with its tags and rows. Another dataset goes back to the latest.

	interface Props {
		value: string;
		org: string;
		/** The dataset chosen; its versions are read when it changes. */
		path: string;
		disabled?: boolean;
		id?: string;
		'aria-label'?: string;
	}

	let {
		value = $bindable(''),
		org,
		path,
		disabled = false,
		id,
		'aria-label': ariaLabel
	}: Props = $props();

	let versions = $state<DatasetVersion[] | null>(null);
	let latest = $state<number | null>(null);
	let cursor = $state<number | null>(null);
	let failed = $state<string | null>(null);
	/** The select's value for the latest; not a tag, since tags can't start with `:`. */
	const LATEST = ':latest';
	/** The dataset the value was chosen for; another one resets it. */
	let chosenFor = untrack(() => path);

	$effect(() => {
		const at = path;
		if (at !== chosenFor) {
			chosenFor = at;
			value = '';
		}
		versions = null;
		failed = null;
		const controller = new AbortController();
		datasetCall<VersionsPage>(datasetsApi(org, 'versions', { path: at }), {
			signal: controller.signal
		})
			.then((page) => {
				versions = page.versions;
				latest = page.latest;
				cursor = page.cursor;
			})
			.catch((e: Error) => {
				if (!controller.signal.aborted) failed = e.message;
			});
		return () => controller.abort();
	});

	async function older() {
		if (cursor === null) return;
		const page = await datasetCall<VersionsPage>(datasetsApi(org, 'versions', { path, cursor }));
		versions = [...(versions ?? []), ...page.versions];
		cursor = page.cursor;
	}

	/** The chosen version, or the one a tag names, for the trigger. */
	const chosen = $derived(
		versions?.find((v) => String(v.version) === value || v.tags.includes(value)) ?? null
	);
	/** The version the trigger names: the chosen one, or the latest. */
	const shown = $derived(value ? chosen : (versions?.find((v) => v.version === latest) ?? null));
	const other = $derived(/^\d+$/.test(value) ? `v${value}` : value);
	const rowsOf = (v: DatasetVersion) => `${v.rows.toLocaleString('en-US')} rows`;
	/** What follows the trigger's name: the version's number (for the latest), tags and rows. */
	const detail = $derived(
		[
			!value && latest !== null ? `v${latest}` : null,
			...(value ? (chosen?.tags ?? []) : []),
			shown ? rowsOf(shown) : null
		]
			.filter(Boolean)
			.join(' · ')
	);
</script>

<Select.Root
	type="single"
	bind:value={() => value || LATEST, (v) => (value = v === LATEST ? '' : v)}
	{disabled}
>
	<Select.Trigger {id} class="h-8 w-full min-w-0 font-normal" aria-label={ariaLabel}>
		<span class="flex min-w-0 gap-1.5 tabular-nums">
			<span class="shrink-0">{value ? (chosen ? `v${chosen.version}` : other) : 'Latest'}</span>
			{#if detail}<span class="truncate text-muted-foreground">· {detail}</span>{/if}
		</span>
	</Select.Trigger>
	<Select.Content class="max-h-80 min-w-72" align="end">
		<Select.Item value={LATEST} label="Latest">
			<span class="flex-1">Latest</span>
			{#if latest !== null}<span class="text-muted-foreground tabular-nums">v{latest}</span>{/if}
		</Select.Item>
		<Select.Separator />
		{#if failed}
			<p class="px-2 py-1.5 text-xs text-destructive">{failed}</p>
		{:else if versions === null}
			<p class="px-2 py-1.5 text-xs text-muted-foreground">Loading…</p>
		{:else}
			{#each versions as v (v.version)}
				<Select.Item value={String(v.version)} label="v{v.version}">
					<span class="w-8 shrink-0 tabular-nums">v{v.version}</span>
					<span class="flex min-w-0 flex-1 flex-wrap gap-1">
						{#each v.tags as tag (tag)}
							<span class="rounded bg-secondary px-1.5 text-[11px] text-secondary-foreground"
								>{tag}</span
							>
						{/each}
					</span>
					<span class="shrink-0 text-xs text-muted-foreground tabular-nums">{rowsOf(v)}</span>
				</Select.Item>
			{/each}
			{#if cursor !== null}
				<button
					type="button"
					class="w-full rounded-md px-1.5 py-1 text-left text-xs text-link hover:bg-accent"
					onclick={older}>Show older versions</button
				>
			{/if}
		{/if}
	</Select.Content>
</Select.Root>
