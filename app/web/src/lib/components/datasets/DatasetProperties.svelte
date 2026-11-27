<script lang="ts">
	import type { Scope } from '#lib/scope.ts';
	import { refHref } from '#lib/datasets.ts';
	import type { DatasetInfo } from '#lib/types.ts';
	import { bytes, dateTime } from '#lib/utils.ts';
	import Properties from '../Properties.svelte';
	import Property from '../Property.svelte';
	import TagChip from '../TagChip.svelte';

	interface Props {
		scope: Scope;
		/** The version shown. */
		info: DatasetInfo;
		/** The tags on that version. */
		tags: string[];
		/** For a blend made by Flock: opens the blend dialog filled in with its settings. */
		onblendagain?: () => void;
		/** For a fork or blend that still reads its sources (D178). */
		onindependent?: () => void;
	}

	let { scope, info, tags, onblendagain, onindependent }: Props = $props();

	const origin = $derived(info.origin ?? null);
	const fmt = (n: number) => n.toLocaleString('en-US');
	/** How much of a source a blend took: a percentage, or a multiple once oversampled. */
	const share = (rows: number, live: number) =>
		!live
			? ''
			: rows > live
				? `${(rows / live).toFixed(1)}× its rows`
				: `${((rows / live) * 100).toFixed(rows / live < 0.1 ? 2 : 1)}% of its rows`;
	const sourceFacts = (s: { fraction?: number; version: number; rows: number; live: number }) =>
		[
			s.fraction === undefined ? '' : `${Math.round(s.fraction * 1000) / 10}%`,
			`v${s.version}`,
			`${fmt(s.rows)} rows`,
			share(s.rows, s.live)
		]
			.filter(Boolean)
			.join(' · ');
	const LINK = 'text-link underline-offset-3 hover:underline';
</script>

<!-- The version's facts in the page's properties pane (D6): its numbers, where it came from, and
     what reads it. -->
<Properties label="Version {info.version}">
	<Property label="Rows">{fmt(info.rows)}</Property>
	<Property label="Columns">{fmt(info.columns)}</Property>
	<Property label="Fragments">{fmt(info.fragments)}</Property>
	<Property label="Data size">{bytes(info.data_size)}</Property>
	<Property label="Written">{dateTime(info.timestamp)}</Property>
	{#if tags.length}
		<Property label={tags.length === 1 ? 'Tag' : 'Tags'}>
			<ul class="flex flex-wrap gap-1" aria-label="Tags">
				{#each tags as tag (tag)}
					<TagChip as="li" {tag} />
				{/each}
			</ul>
		</Property>
	{/if}
</Properties>

{#if origin}
	<Properties label={origin.kind === 'fork' ? 'Fork' : 'Blend'}>
		{#if origin.kind === 'fork'}
			<Property label="Forked from">
				<a
					class={['font-mono text-[12px] break-all', LINK]}
					href={refHref(scope.org, origin.source.path, origin.source.version)}
					>{origin.source.path}</a
				>
				<span class="text-muted-foreground">v{origin.source.version}</span>
			</Property>
		{:else}
			<Property label="Seed">{origin.seed}</Property>
			<Property label="Split">
				{origin.spec.mode === 'fraction'
					? `By percentage of ${fmt(origin.spec.total)}`
					: 'By row count'}
			</Property>
			{#if origin.replace}<Property label="Oversampled">Yes</Property>{/if}
			<Property label="Sources" stack>
				<ul class="grid gap-2" aria-label="Sources">
					{#each origin.sources as s (s.path)}
						<li>
							<a
								class={['font-mono text-[12px] break-all', LINK]}
								href={refHref(scope.org, s.path, s.version)}>{s.path}</a
							>
							<p class="text-xs text-muted-foreground tabular-nums">{sourceFacts(s)}</p>
						</li>
					{/each}
				</ul>
			</Property>
		{/if}
		{#if origin.independent}
			<p class="py-1.5 text-[13px] text-muted-foreground">It has its own copy of the rows now.</p>
		{/if}
		{#if (onblendagain && origin.kind === 'blend') || (onindependent && !origin.independent)}
			<div class="flex flex-wrap gap-x-3 gap-y-1 py-1.5 text-[13px]">
				{#if onblendagain && origin.kind === 'blend'}
					<button type="button" class={LINK} onclick={onblendagain}>Blend again…</button>
				{/if}
				{#if onindependent && !origin.independent}
					<button type="button" class={LINK} onclick={onindependent}>Make independent…</button>
				{/if}
			</div>
		{/if}
	</Properties>
{/if}

{#if info.dependents?.count}
	{@const d = info.dependents}
	<Properties label="Datasets reading this one">
		<Property label="Read by" stack>
			<ul class="grid gap-1" aria-label="Datasets reading this one">
				{#each d.paths as p (p)}
					<li>
						<a class={['font-mono text-[12px] break-all', LINK]} href={refHref(scope.org, p)}>{p}</a
						>
					</li>
				{/each}
				{#if d.trashed}
					<li class="text-muted-foreground">{d.trashed} in the trash</li>
				{/if}
				{#if d.count - d.trashed > d.paths.length}
					<li class="text-muted-foreground">and {d.count - d.trashed - d.paths.length} more</li>
				{/if}
			</ul>
		</Property>
		<p class="py-1.5 text-xs text-muted-foreground">
			It can’t be renamed, moved or deleted while they read it.
		</p>
	</Properties>
{/if}
