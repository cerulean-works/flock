<script lang="ts">
	import type { Scope } from '#lib/scope.ts';
	import { flatten } from '#lib/data/run.ts';
	import { readText } from '#lib/models.ts';
	import type { ModelVersionDetail } from '#lib/types.ts';
	import { formatValue } from '#lib/utils.ts';
	import Markdown from '../Markdown.svelte';
	import Skeleton from '#lib/components/Skeleton.svelte';

	interface Props {
		scope: Scope;
		version: ModelVersionDetail;
	}

	let { scope, version }: Props = $props();

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
			readText(scope.org, `${version.prefix}${path}`)
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
</script>

{#snippet table(title: string, read: Read)}
	{@const rows = fields(read)}
	<section class="mt-8 first:mt-0" aria-label={title}>
		<h2 class="text-[13px] font-medium">{title}</h2>
		{#if read === null}
			<Skeleton shape="lines" class="mt-2" />
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

<!-- The version's README and config files (its facts are in the page's properties pane). -->
<div class="max-w-3xl">
	{#if !has('README.md')}
		<p class="mb-8 text-[13px] text-muted-foreground">
			No README. Add a README.md to the folder you push to describe it.
		</p>
	{:else if readme === null}
		<Skeleton shape="lines" count={6} class="mb-8" />
	{:else if 'error' in readme}
		<p class="mb-8 text-[13px] text-destructive" role="alert">{readme.error}</p>
	{:else}
		<div class="mb-8"><Markdown text={readme.text} /></div>
	{/if}
	{#if has('config.json')}{@render table('config.json', config)}{/if}
	{#if has('flock.json')}{@render table('flock.json', flockJson)}{/if}
</div>
