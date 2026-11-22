<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { toast } from 'svelte-sonner';
	import { Button } from '#lib/components/ui/button/index.js';
	import type { LaunchList } from '#lib/launches.svelte.ts';
	import type { EvalLaunch } from '#lib/types.ts';
	import { money, relativeTime } from '#lib/utils.ts';
	import { runHref } from '#lib/workspace/url.ts';

	// A project's eval launches (026), each as a row above its runs: what it scores and how it
	// stands, Cancel while it runs (for who launched it, or an admin), its run once finished.
	interface Props {
		org: string;
		project: string;
		list: LaunchList;
		/** The viewer's user id, and whether they are an admin of the org. */
		me: number | null;
		admin: boolean;
	}

	let { org, project, list, me, admin }: Props = $props();

	const STATES = {
		running: { label: 'Running', dot: 'bg-state-running' },
		finished: { label: 'Finished', dot: 'bg-state-finished' },
		failed: { label: 'Failed', dot: 'bg-state-failed' },
		cancelled: { label: 'Cancelled', dot: 'bg-faint' }
	};

	let cancelling = $state<number | null>(null);

	async function cancel(job: EvalLaunch) {
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

{#if list.shown.length}
	<ul
		class="grid max-h-44 flex-none gap-1.5 overflow-y-auto border-b py-2 pr-7 pl-5"
		aria-label="Eval launches"
	>
		{#each list.shown as job (job.id)}
			<li class="flex items-start gap-3 bg-card px-3 py-2 text-[13.5px]">
				<div class="grid min-w-0 flex-1 gap-0.5">
					<p class="flex items-center gap-2">
						<span class={['size-[7px] flex-none rounded-full', STATES[job.state].dot]}></span>
						<span>{STATES[job.state].label}</span>
						<span class="truncate font-mono text-[12.5px]">
							{job.model}@{job.sha.slice(0, 7)}
						</span>
						<span class="truncate text-muted-foreground">
							{[...job.suites, ...job.tasks].join(', ')}
						</span>
					</p>
					<p class="text-[12.5px] text-muted-foreground tabular-nums">
						{job.started_by?.name ?? 'Someone'}, {relativeTime(job.started_at, Date.now() / 1000)},
						cap {money(job.cap_usd, 'USD')}{job.add_bos ? ', with BOS' : ''}
					</p>
					{#if job.state === 'failed' && job.error}
						<p class="text-destructive">{job.error}</p>
					{/if}
				</div>
				{#if job.state === 'finished'}
					<a
						class="leading-7 text-link underline-offset-3 hover:underline"
						href={runHref(org, project, job.run)}>Open run</a
					>
				{:else if job.state === 'running' && (admin || job.started_by?.id === me)}
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
			</li>
		{/each}
	</ul>
{/if}
