<script lang="ts">
	interface Props {
		/** Items a bulk action left as they were, with Flock's reason. */
		failures: readonly { path: string; error: string | null }[];
		/** What the list is, e.g. "2 items didn’t move." */
		summary: string;
	}

	let { failures, summary }: Props = $props();
</script>

{#if failures.length}
	<div class="grid gap-1.5" role="alert">
		<p class="text-[13px] text-destructive">{summary}</p>
		<ul class="max-h-40 overflow-y-auto border-y border-divider py-1 text-[13px]">
			{#each failures as f (f.path)}
				<li class="py-0.5">
					<span class="font-mono text-[12.5px] break-all">{f.path}</span>
					{#if f.error}<span class="block text-muted-foreground">{f.error}</span>{/if}
				</li>
			{/each}
		</ul>
	</div>
{/if}
