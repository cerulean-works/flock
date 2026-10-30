<script lang="ts">
	import { flatten } from '#lib/data/run.ts';
	import { readText, runPathHref, sortTags } from '#lib/models.ts';
	import type { ModelVersionDetail } from '#lib/types.ts';
	import { bytes, dateTime, formatValue } from '#lib/utils.ts';
	import Markdown from '../Markdown.svelte';

	interface Props {
		org: string;
		version: ModelVersionDetail;
	}

	let { org, version }: Props = $props();

	/** A file of the version read from storage: its text, or why it couldn't be read. */
	type Read = { text: string } | { error: string } | null;

	const has = (path: string) => version.files.some((f) => f.path === path);
	let readme = $state<Read>(null);
	let config = $state<Read>(null);
	let flockJson = $state<Read>(null);

	// The README, config.json and flock.json of this version (D108), read once it's shown.
	$effect(() => {
		let stale = false;
		const read = (path: string, set: (r: Read) => void) => {
			if (!has(path)) return;
			readText(org, `${version.prefix}${path}`)
				.then((text) => stale || set({ text }))
				.catch((e: Error) => stale || set({ error: e.message }));
		};
		read('README.md', (r) => (readme = r));
		read('config.json', (r) => (config = r));
		read('flock.json', (r) => (flockJson = r));
		return () => (stale = true);
	});

	/** A JSON file's fields, flattened to dotted keys; null when it isn't a JSON object. */
	function fields(read: Read): [string, unknown][] | null {
		if (!read || !('text' in read)) return null;
		try {
			const value = JSON.parse(read.text);
			if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
			return flatten(value).sort(([a], [b]) => a.localeCompare(b));
		} catch {
			return null;
		}
	}

	const runHref = $derived(version.run ? runPathHref(version.run) : null);
	const facts = $derived(
		[
			['Size', bytes(version.total_bytes)],
			['Files', version.files.length.toLocaleString('en-US')],
			['Step', version.step === null ? null : version.step.toLocaleString('en-US')],
			['Pushed', dateTime(version.created_at)],
			['Pushed by', version.created_by?.name ?? null]
		].filter((f): f is [string, string] => f[1] !== null)
	);
</script>

{#snippet table(title: string, read: Read)}
	{@const rows = fields(read)}
	<section class="mt-8 first:mt-0" aria-label={title}>
		<h2 class="text-[13px] font-medium">{title}</h2>
		{#if read === null}
			<p class="mt-2 text-[13px] text-muted-foreground">Loading…</p>
		{:else if 'error' in read}
			<p class="mt-2 text-[13px] text-destructive" role="alert">{read.error}</p>
		{:else if rows === null}
			<p class="mt-2 text-[13px] text-muted-foreground">This file isn’t a JSON object.</p>
		{:else}
			<table class="mt-1 w-full border-collapse font-mono text-[12.5px]">
				<tbody>
					{#each rows as [key, value] (key)}
						<tr class="border-b border-divider">
							<th scope="row" class="py-1.5 pr-4 text-left font-normal text-muted-foreground">
								{key}
							</th>
							<td class="py-1.5 text-right break-all tabular-nums">{formatValue(value)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	</section>
{/snippet}

<div class="grid gap-x-12 gap-y-8 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
	<div class="min-w-0">
		{#if !has('README.md')}
			<p class="mb-8 bg-card px-4 py-6 text-[13px] text-muted-foreground">
				This version has no README. Add a README.md to the folder you push to describe it.
			</p>
		{:else if readme === null}
			<p class="mb-8 bg-card px-4 py-6 text-[13px] text-muted-foreground">Loading…</p>
		{:else if 'error' in readme}
			<p class="mb-8 bg-card px-4 py-6 text-[13px] text-destructive" role="alert">
				{readme.error}
			</p>
		{:else}
			<div class="mb-8"><Markdown text={readme.text} /></div>
		{/if}
		{#if has('config.json')}{@render table('config.json', config)}{/if}
		{#if has('flock.json')}{@render table('flock.json', flockJson)}{/if}
	</div>

	<aside aria-labelledby="overview-version">
		<h2 id="overview-version" class="text-[13px] text-muted-foreground">
			Version {version.number}
		</h2>
		<table class="mt-1 w-full border-collapse text-[13px]">
			<tbody>
				{#each facts as [label, value] (label)}
					<tr class="border-b border-divider">
						<th scope="row" class="py-2 pr-4 text-left font-normal text-muted-foreground">
							{label}
						</th>
						<td class="py-2 text-right tabular-nums">{value}</td>
					</tr>
				{/each}
				{#if version.run}
					<tr class="border-b border-divider">
						<th scope="row" class="py-2 pr-4 text-left font-normal text-muted-foreground">Run</th>
						<td class="py-2 text-right break-all">
							{#if runHref}
								<a class="text-link underline-offset-3 hover:underline" href={runHref}
									>{version.run}</a
								>
							{:else}
								{version.run}
							{/if}
						</td>
					</tr>
				{/if}
				{#if version.tags.length}
					<tr class="border-b border-divider">
						<th scope="row" class="py-2 pr-4 text-left align-top font-normal text-muted-foreground">
							{version.tags.length === 1 ? 'Tag' : 'Tags'}
						</th>
						<td class="py-2 text-right break-all">{sortTags(version.tags).join(', ')}</td>
					</tr>
				{/if}
				<tr class="border-b border-divider">
					<th scope="row" class="py-2 pr-4 text-left align-top font-normal text-muted-foreground">
						Digest
					</th>
					<td class="py-2 text-right font-mono text-[12px] break-all" title={version.digest}>
						{version.digest.slice(0, 12)}
					</td>
				</tr>
			</tbody>
		</table>
	</aside>
</div>
