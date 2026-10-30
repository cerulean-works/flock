<script lang="ts">
	import BoxIcon from '@lucide/svelte/icons/box';
	import { modelsHref, pullSnippet } from '#lib/models.ts';
	import type { ModelEntry } from '#lib/types.ts';
	import { bytes, dateTime, relativeTime } from '#lib/utils.ts';
	import CopyBlock from '../CopyBlock.svelte';

	interface Props {
		org: string;
		models: ModelEntry[];
		now: number;
	}

	let { org, models, now }: Props = $props();

	const saveSnippet = $derived(
		['from flock.hub import models', '', `models.save_to_hub(model, "${org}/my-model")`].join('\n')
	);
</script>

{#if !models.length}
	<div class="max-w-[68ch]">
		<p class="text-[15px]">No models yet.</p>
		<p class="mt-1 text-muted-foreground">Save a model from Python and it shows up here.</p>
		<div class="mt-3"><CopyBlock code={saveSnippet} /></div>
		<p class="mt-3 text-[13px] text-muted-foreground">
			Or push a folder with <code class="font-mono text-[0.9em]"
				>flock models push ./model {org}/my-model</code
			>, and load it back with
			<code class="font-mono text-[0.9em]">{pullSnippet(`${org}/my-model`)}</code>.
		</p>
	</div>
{:else}
	<table class="w-full table-fixed border-collapse text-[13.5px]">
		<thead>
			<tr class="h-10 border-b text-left text-xs font-medium text-muted-foreground">
				<th class="px-2 font-medium">Name</th>
				<th class="hidden w-28 px-2 font-medium sm:table-cell">Latest</th>
				<th class="w-28 px-2 font-medium">Primary</th>
				<th class="hidden w-28 px-2 font-medium sm:table-cell">Size</th>
				<th class="w-32 px-2 font-medium">Updated</th>
			</tr>
		</thead>
		<tbody>
			{#each models as m (m.name)}
				<tr class="h-10 border-b border-divider hover:bg-muted">
					<td class="truncate px-2">
						<a
							class="flex items-center gap-2 underline-offset-3 hover:text-link hover:underline"
							href={modelsHref(org, m.name)}
						>
							<BoxIcon class="size-4 flex-none text-muted-foreground" />
							<span class="truncate">{m.name}</span>
						</a>
					</td>
					<td class="hidden px-2 text-muted-foreground tabular-nums sm:table-cell">
						{m.latest === null ? '—' : `Version ${m.latest}`}
					</td>
					<td class="px-2 text-muted-foreground tabular-nums">
						{m.primary === null ? '—' : `Version ${m.primary}`}
					</td>
					<td class="hidden px-2 text-muted-foreground tabular-nums sm:table-cell">
						{m.size == null ? '—' : bytes(m.size)}
					</td>
					<td class="px-2 text-muted-foreground tabular-nums" title={dateTime(m.updated_at)}>
						{relativeTime(m.updated_at, now)}
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}
