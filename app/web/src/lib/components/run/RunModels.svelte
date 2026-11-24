<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import BoxIcon from '@lucide/svelte/icons/box';
	import TagIcon from '@lucide/svelte/icons/tag';
	import MetaItem from '#lib/components/MetaItem.svelte';
	import MetaRow from '#lib/components/MetaRow.svelte';
	import { sortTags, versionHref } from '#lib/models.ts';
	import type { ModelVersion } from '#lib/types.ts';

	/**
	 * The model versions of a run (D108): those it made, its Checkpoints, or those it scored as an
	 * eval run. A global model's by its path, a project's as `ModelVersion` prints it,
	 * `pretrain:sweeps/lr-3e-4@7` (D169).
	 */
	let { title, versions }: { title: string; versions: ModelVersion[] } = $props();

	/**
	 * Its model's scope (D159). A version from before 022 has no `org`: its ref is `org/name`.
	 */
	const scopeOf = (v: ModelVersion) => ({
		org: v.org ?? v.model.slice(0, v.model.indexOf('/')),
		project: v.project ?? null
	});
</script>

<section class="border-t border-divider px-4 py-3" aria-label={title}>
	<h3 class="flex items-center justify-between font-medium">
		{title}
		<span class="text-xs font-normal text-muted-foreground tabular-nums">{versions.length}</span>
	</h3>
	<ul class="mt-1.5 grid gap-1">
		{#each versions as v (`${v.model}@${v.number}`)}
			{@const tags = sortTags(v.tags)}
			<li class="flex min-w-0 items-baseline gap-2">
				<BoxIcon class="size-3.5 flex-none translate-y-0.5 text-muted-foreground" />
				<a
					class="min-w-0 truncate text-link underline-offset-3 hover:underline"
					href={versionHref(scopeOf(v), v.name, v.number)}
					{@attach hint(`${v.model}@${v.number}`)}
				>
					{v.project ? v.model : v.name}@{v.number}
				</a>
				<MetaRow class="ml-auto flex-nowrap justify-end gap-x-2.5 overflow-hidden text-xs">
					{#if v.primary}<MetaItem chip class="h-4.5 px-1.5 text-[11px]">Primary</MetaItem>{/if}
					{#if v.step !== null}<MetaItem class="flex-none"
							>step {v.step.toLocaleString('en-US')}</MetaItem
						>{/if}
					{#if tags.length}<MetaItem icon={TagIcon}>{tags.join(', ')}</MetaItem>{/if}
				</MetaRow>
			</li>
		{/each}
	</ul>
</section>
