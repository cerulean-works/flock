<script lang="ts">
	import type { Scope } from '#lib/scope.ts';
	import { DatasetError, datasetCall, datasetsApi, refHref } from '#lib/datasets.ts';
	import type { DatasetInfo } from '#lib/types.ts';
	import { bytes, dateTime } from '#lib/utils.ts';
	import Markdown from '../Markdown.svelte';
	import TagChip from '../TagChip.svelte';

	interface Props {
		scope: Scope;
		path: string;
		/** The version shown; null when Lance couldn't read it, and only the README shows. */
		info: DatasetInfo | null;
		/** The tags on that version. */
		tags: string[];
		/** For a blend made by Flock: opens the blend dialog filled in with its settings. */
		onblendagain?: () => void;
		/** For a fork or blend that still reads its sources (D178). */
		onindependent?: () => void;
	}

	let { scope, path, info, tags, onblendagain, onindependent }: Props = $props();

	const origin = $derived(info?.origin ?? null);
	const fmt = (n: number) => n.toLocaleString('en-US');
	/** How much of a source a blend took: a percentage, or a multiple once oversampled. */
	const share = (rows: number, live: number) =>
		!live
			? '—'
			: rows > live
				? `${(rows / live).toFixed(1)}× its rows`
				: `${((rows / live) * 100).toFixed(rows / live < 0.1 ? 2 : 1)}% of its rows`;
	const blendFacts = $derived(
		origin?.kind === 'blend'
			? [
					['Seed', String(origin.seed)],
					[
						'Split',
						origin.spec.mode === 'fraction'
							? `By percentage of ${fmt(origin.spec.total)}`
							: 'By row count'
					],
					...(origin.replace ? [['Oversampled', 'Yes']] : [])
				]
			: []
	);

	let readme = $state<{ text: string; truncated: boolean } | null>(null);
	let missing = $state(false);
	let failed = $state<string | null>(null);

	// README.md in the dataset's folder (D92). It isn't versioned: every version shows this one.
	$effect(() => {
		const controller = new AbortController();
		readme = null;
		missing = false;
		failed = null;
		datasetCall<{ text: string; truncated: boolean }>(datasetsApi(scope, 'readme', { path }), {
			signal: controller.signal
		})
			.then((got) => (readme = got))
			.catch((e: Error) => {
				if (controller.signal.aborted) return;
				if (e instanceof DatasetError && e.status === 404) missing = true;
				else failed = e.message;
			});
		return () => controller.abort();
	});

	const facts = $derived(
		info
			? [
					['Rows', info.rows.toLocaleString('en-US')],
					['Columns', info.columns.toLocaleString('en-US')],
					['Fragments', info.fragments.toLocaleString('en-US')],
					['Data size', bytes(info.data_size)],
					['Written', dateTime(info.timestamp)]
				]
			: []
	);
</script>

<!-- The README beside this version's numbers. -->
<div class="grid gap-x-12 gap-y-8 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
	<div class="min-w-0">
		{#if missing}
			<p class="bg-card px-4 py-6 text-[13px] text-muted-foreground">
				This dataset has no README. Add a README.md to its folder to describe it.
			</p>
		{:else if failed}
			<p class="bg-card px-4 py-6 text-[13px] text-destructive" role="alert">{failed}</p>
		{:else if readme}
			<Markdown text={readme.text} />
			{#if readme.truncated}
				<p class="mt-4 text-[13px] text-muted-foreground">Showing the first 1 MB of README.md.</p>
			{/if}
		{:else}
			<p class="bg-card px-4 py-6 text-[13px] text-muted-foreground">Loading…</p>
		{/if}
	</div>

	{#if info}
		<aside aria-labelledby="about-version">
			{#if origin}
				<section class="mb-6 text-[13px]" aria-labelledby="about-origin">
					<h2 id="about-origin" class="text-[13px] text-muted-foreground">
						{origin.kind === 'fork' ? 'Fork' : 'Blend'}
					</h2>
					{#if origin.kind === 'fork'}
						<p class="mt-2">
							Forked from
							<a
								class="font-mono text-link underline-offset-3 hover:underline"
								href={refHref(scope.org, origin.source.path, origin.source.version)}
								>{origin.source.path}</a
							>
							at version {origin.source.version}.
							{#if origin.independent}It has its own copy of the rows now.{/if}
						</p>
					{:else}
						<table class="mt-1 w-full border-collapse">
							<tbody>
								{#each blendFacts as [label, value] (label)}
									<tr class="border-b border-divider">
										<th
											scope="row"
											class="py-2 pr-4 text-left font-normal whitespace-nowrap text-muted-foreground"
											>{label}</th
										>
										<td class="py-2 text-right tabular-nums">{value}</td>
									</tr>
								{/each}
							</tbody>
						</table>
						<ul class="mt-3 grid gap-2" aria-label="Sources">
							{#each origin.sources as s (s.path)}
								<li>
									<a
										class="font-mono break-all text-link underline-offset-3 hover:underline"
										href={refHref(scope.org, s.path, s.version)}>{s.path}</a
									>
									<p class="text-xs text-muted-foreground tabular-nums">
										{[
											s.fraction !== undefined ? `${Math.round(s.fraction * 1000) / 10}%` : null,
											`v${s.version}`,
											`${fmt(s.rows)} rows`,
											share(s.rows, s.live)
										]
											.filter(Boolean)
											.join(' · ')}
									</p>
								</li>
							{/each}
						</ul>
						{#if origin.independent}
							<p class="mt-2">It has its own copy of the rows now.</p>
						{/if}
					{/if}
					{#if (onblendagain && origin.kind === 'blend') || (onindependent && !origin.independent)}
						<div class="mt-3 flex flex-wrap gap-x-3 gap-y-1">
							{#if onblendagain && origin.kind === 'blend'}
								<button
									type="button"
									class="text-link underline-offset-3 hover:underline"
									onclick={onblendagain}>Blend again…</button
								>
							{/if}
							{#if onindependent && !origin.independent}
								<button
									type="button"
									class="text-link underline-offset-3 hover:underline"
									onclick={onindependent}>Make independent…</button
								>
							{/if}
						</div>
					{/if}
				</section>
			{/if}
			<h2 id="about-version" class="text-[13px] text-muted-foreground">
				Version {info.version}
			</h2>
			<table class="mt-1 w-full border-collapse text-[13px]">
				<tbody>
					{#each facts as [label, value] (label)}
						<tr class="border-b border-divider">
							<th
								scope="row"
								class="py-2 pr-4 text-left font-normal whitespace-nowrap text-muted-foreground"
							>
								{label}
							</th>
							<td class="py-2 text-right tabular-nums">{value}</td>
						</tr>
					{/each}
					{#if tags.length}
						<tr class="border-b border-divider">
							<th
								scope="row"
								class="py-2 pr-4 text-left align-top font-normal whitespace-nowrap text-muted-foreground"
							>
								{tags.length === 1 ? 'Tag' : 'Tags'}
							</th>
							<td class="py-2">
								<ul class="flex flex-wrap justify-end gap-1" aria-label="Tags">
									{#each tags as tag (tag)}
										<TagChip as="li" {tag} />
									{/each}
								</ul>
							</td>
						</tr>
					{/if}
				</tbody>
			</table>
			{#if info.dependents?.count}
				<h2 class="mt-6 text-[13px] text-muted-foreground">
					Read by {info.dependents.count} dataset{info.dependents.count === 1 ? '' : 's'}
				</h2>
				<ul class="mt-1 grid gap-1 text-[13px]" aria-label="Datasets reading this one">
					{#each info.dependents.paths as p (p)}
						<li>
							<a
								class="font-mono text-link underline-offset-3 hover:underline"
								href={refHref(scope.org, p)}>{p}</a
							>
						</li>
					{/each}
					{#if info.dependents.trashed}
						<li class="text-muted-foreground">{info.dependents.trashed} in the trash</li>
					{/if}
					{#if info.dependents.count - info.dependents.trashed > info.dependents.paths.length}
						<li class="text-muted-foreground">
							and {info.dependents.count - info.dependents.trashed - info.dependents.paths.length} more
						</li>
					{/if}
				</ul>
				<p class="mt-2 text-xs text-muted-foreground">
					It can’t be renamed, moved or deleted while they read it.
				</p>
			{/if}
		</aside>
	{/if}
</div>
