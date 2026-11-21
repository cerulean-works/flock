<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import XIcon from '@lucide/svelte/icons/x';
	import { toast } from 'svelte-sonner';
	import { Button } from '#lib/components/ui/button/index.js';
	import { datasetsHref } from '#lib/datasets.ts';
	import { jobText, jobsAt } from '#lib/forks.ts';
	import type { DatasetJobList } from '#lib/forks.svelte.ts';
	import type { DatasetJob } from '#lib/types.ts';

	// Blends and datasets being made independent in this folder (D176), as rows above the
	// listing, as Hugging Face imports show.
	interface Props {
		org: string;
		list: DatasetJobList;
		folder: string;
	}

	let { org, list, folder }: Props = $props();

	const rows = $derived(jobsAt(list.shown, folder));
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
	<ul class="mb-5 grid gap-2" aria-label="Blends">
		{#each rows as job (job.id)}
			<li class="bg-card px-3 py-2.5 text-[13.5px]">
				{#if job.state === 'running'}
					<div class="flex items-center gap-3">
						<p class="min-w-0 flex-1 truncate tabular-nums">{jobText(job)}</p>
						{#if job.kind === 'blend'}
							<Button
								variant="outline"
								size="sm"
								disabled={cancelling === job.id}
								onclick={() => cancel(job)}
							>
								{#if cancelling === job.id}<LoaderCircleIcon class="animate-spin" />{/if}
								Cancel
							</Button>
						{/if}
					</div>
					<div
						class="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"
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
				{:else}
					<div class="flex items-start gap-3">
						<div class="grid min-w-0 flex-1 gap-1">
							<p class="leading-7">
								{jobText(job)}
								{#if job.state === 'finished'}
									<a
										class="text-link underline-offset-3 hover:underline"
										href={datasetsHref(org, job.dest)}>Open</a
									>
								{/if}
							</p>
							{#if job.error}<p class="text-destructive">{job.error}</p>{/if}
						</div>
						<Button
							variant="ghost"
							size="icon"
							class="size-7"
							aria-label="Hide"
							onclick={() => list.hide(job.id)}
						>
							<XIcon />
						</Button>
					</div>
				{/if}
			</li>
		{/each}
	</ul>
{/if}
