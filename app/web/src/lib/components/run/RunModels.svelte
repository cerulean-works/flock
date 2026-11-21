<script lang="ts">
	import BoxIcon from '@lucide/svelte/icons/box';
	import { sortTags, versionHref } from '#lib/models.ts';
	import type { ModelVersion } from '#lib/types.ts';

	/** Model versions of a run (D108): those it made, or those it scored as an eval run. */
	let { title, versions }: { title: string; versions: ModelVersion[] } = $props();

	const orgOf = (v: ModelVersion) => v.model.slice(0, v.model.indexOf('/'));
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
					href={versionHref(orgOf(v), v.name, v.number)}
					title="{v.model}@{v.number}"
				>
					{v.name}@{v.number}
				</a>
				<span class="ml-auto min-w-0 truncate text-right text-xs text-muted-foreground">
					{[
						v.primary ? 'primary' : null,
						v.step !== null ? `step ${v.step.toLocaleString('en-US')}` : null,
						...tags
					]
						.filter(Boolean)
						.join(' · ')}
				</span>
			</li>
		{/each}
	</ul>
</section>
