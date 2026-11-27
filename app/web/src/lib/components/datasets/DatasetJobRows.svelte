<script lang="ts">
	import XIcon from '@lucide/svelte/icons/x';
	import { toast } from 'svelte-sonner';
	import { Button } from '#lib/components/ui/button/index.js';
	import { jobText, jobsAt } from '#lib/forks.ts';
	import type { DatasetJobList } from '#lib/forks.svelte.ts';
	import type { Scope } from '#lib/scope.ts';
	import type { DatasetJob } from '#lib/types.ts';

	// Blends and datasets being made independent in this folder (D176), as rows above the
	// listing, as Hugging Face imports show.
	interface Props {
		scope: Scope;
		list: DatasetJobList;
		folder: string;
	}

	let { scope, list, folder }: Props = $props();

	const rows = $derived(jobsAt(list.shown, scope, folder));
	let cancelling = $state<number | null>(null);

	async function cancel(job: DatasetJob) {
		cancelling = job.id;
		try {
			await list.cancel(job.id);
		} catch (e) {
			toast.error((e as Error).message);
		} finally {
			cancelling = null;
		}
	}
</script>

{#if rows.length}
	<!-- One muted line a job, over the folder's table: no fill, a × to hide it; one that went well is a toast instead. -->
	<ul class="mb-3 grid gap-0.5" aria-label="Blends">
		{#each rows as job (job.id)}
			<li class="group/job flex min-h-7 items-center gap-3 text-[13px] text-muted-foreground">
				<p class="min-w-0 truncate tabular-nums">
					{jobText(job)}
				</p>
				{#if job.state === 'running'}
					<div
						class="h-1 w-24 flex-none overflow-hidden rounded-full bg-muted"
						role="progressbar"
						aria-label="Progress of {job.dest}"
						aria-valuemin={0}
						aria-valuemax={100}
						aria-valuenow={Math.round(job.progress * 100)}
					>
						<div
							class="h-full rounded-full bg-primary transition-[width]"
							style:width="{Math.max(2, job.progress * 100)}%"
						></div>
					</div>
					{#if job.kind === 'blend'}
						<Button
							variant="quiet"
							size="bar"
							onclick={() => cancel(job)}
							pending={cancelling === job.id}
						>
							Cancel
						</Button>
					{/if}
				{:else}
					{#if job.error}<span class="min-w-0 truncate text-destructive">{job.error}</span>{/if}
					<Button
						variant="quiet"
						size="icon-bar"
						class="size-6"
						aria-label="Hide"
						onclick={() => list.hide(job.id)}
					>
						<XIcon />
					</Button>
				{/if}
			</li>
		{/each}
	</ul>
{/if}
