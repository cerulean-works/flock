<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import { runPathHref, sortTags } from '#lib/models.ts';
	import type { ModelInfo, ModelVersionDetail } from '#lib/types.ts';
	import { bytes, dateTime } from '#lib/utils.ts';
	import Properties from '../Properties.svelte';
	import Property from '../Property.svelte';
	import TagChip from '../TagChip.svelte';

	interface Props {
		info: ModelInfo;
		/** The version shown. */
		version: ModelVersionDetail;
	}

	let { info, version }: Props = $props();

	const runHref = $derived(version.run ? runPathHref(version.run) : null);
	const madePrimary = $derived(version.primary && info.primary_set_at !== null);
</script>

<!-- The version's facts in the page's properties pane (D6). -->
<Properties label="Version {version.number}">
	<Property label="Size">{bytes(version.total_bytes)}</Property>
	<Property label="Files">{version.files.length.toLocaleString('en-US')}</Property>
	{#if version.step !== null}
		<Property label="Step">{version.step.toLocaleString('en-US')}</Property>
	{/if}
	<Property label="Pushed">{dateTime(version.created_at)}</Property>
	{#if version.created_by}<Property label="Pushed by">{version.created_by.name}</Property>{/if}
	{#if version.tags.length}
		<Property label={version.tags.length === 1 ? 'Tag' : 'Tags'}>
			<ul class="flex flex-wrap gap-1" aria-label="Tags">
				{#each sortTags(version.tags) as tag (tag)}
					<TagChip as="li" {tag} />
				{/each}
			</ul>
		</Property>
	{/if}
	<Property label="Digest" mono>
		<span {@attach hint(version.digest)}>{version.digest.slice(0, 12)}</span>
	</Property>
</Properties>

{#if version.run || madePrimary}
	<Properties label="Lineage">
		{#if version.run}
			<Property label="Run" stack>
				{#if runHref}
					<a class="break-all text-link underline-offset-3 hover:underline" href={runHref}
						>{version.run}</a
					>
				{:else}
					<span class="break-all">{version.run}</span>
				{/if}
			</Property>
		{/if}
		{#if madePrimary && info.primary_set_at !== null}
			<Property label="Made primary">{dateTime(info.primary_set_at)}</Property>
			{#if info.primary_set_by}
				<Property label="By">{info.primary_set_by.name}</Property>
			{/if}
		{/if}
	</Properties>
{/if}
