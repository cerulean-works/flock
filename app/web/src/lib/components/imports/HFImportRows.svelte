<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import XIcon from '@lucide/svelte/icons/x';
	import { toast } from 'svelte-sonner';
	import { Button } from '#lib/components/ui/button/index.js';
	import { datasetsHref } from '#lib/datasets.ts';
	import { endedText, importsAt, progressText } from '#lib/hf.ts';
	import type { HFImportList } from '#lib/hf.svelte.ts';
	import { versionHref } from '#lib/models.ts';
	import type { HFImport } from '#lib/types.ts';
	import CopyBlock from '../CopyBlock.svelte';

	// The org's Hugging Face imports into this folder, or into its models, each as a row above
	// the listing: progress and Cancel while it runs, then how it ended.
	interface Props {
		org: string;
		list: HFImportList;
		kind: 'dataset' | 'model';
		/** The folder shown, for datasets. */
		folder?: string;
	}

	let { org, list, kind, folder = '' }: Props = $props();

	const rows = $derived(importsAt(list.shown, kind, folder));
	let cancelling = $state<number | null>(null);

	async function cancel(job: HFImport) {
		cancelling = job.id;
		try {
			await list.cancel(job.id);
		} catch (e) {
			toast.error((e as Error).message);
		} finally {
			cancelling = null;
		}
	}

	function resultHref(job: HFImport) {
		if (job.kind === 'model' && job.result?.version)
			return versionHref(org, job.dest, job.result.version);
		const made = job.result?.datasets ?? [];
		return datasetsHref(org, made.length === 1 ? made[0].path : job.dest);
	}
</script>

{#if rows.length}
	<ul class="mb-5 grid gap-2" aria-label="Imports from Hugging Face">
		{#each rows as job (job.id)}
			<li class="bg-card px-3 py-2.5 text-[13.5px]">
				{#if job.state === 'running'}
					<div class="flex items-center gap-3">
						<div class="min-w-0 flex-1">
							<p class="truncate">
								Importing <span class="font-mono text-[12.5px]">{job.repo}</span> into
								<span class="font-mono text-[12.5px]">{job.dest}</span>
							</p>
							<p class="mt-0.5 text-[12.5px] text-muted-foreground tabular-nums">
								{progressText(job)}
							</p>
						</div>
						<Button
							variant="outline"
							size="sm"
							disabled={cancelling === job.id}
							onclick={() => cancel(job)}
						>
							{#if cancelling === job.id}<LoaderCircleIcon class="animate-spin" />{/if}
							Cancel
						</Button>
					</div>
					<div
						class="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"
						role="progressbar"
						aria-label="Progress of the import of {job.repo}"
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
						<div class="grid min-w-0 flex-1 gap-1.5">
							<!-- One line as tall as the Hide button, so they sit centered on each other. -->
							<p class="leading-7">
								{endedText(job)}
								{#if job.state === 'finished'}
									<a class="text-link underline-offset-3 hover:underline" href={resultHref(job)}
										>Open</a
									>
								{/if}
							</p>
							{#if job.state === 'failed'}
								{#if job.error}<p class="text-destructive">{job.error}</p>{/if}
								<p class="text-muted-foreground">You can run it with flock instead.</p>
								<CopyBlock code={job.command} />
							{/if}
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
