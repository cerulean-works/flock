<script lang="ts">
	import DownloadIcon from '@lucide/svelte/icons/download';
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import FormError from '#lib/components/FormError.svelte';
	import WandbImportFields from '#lib/components/WandbImportFields.svelte';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { ImportState, OrgImport } from '#lib/types.ts';
	import { count, dateTime, relativeTime } from '#lib/utils.ts';
	import { pollImport, wandbImport } from '#lib/wandb.ts';

	interface Props {
		/** The org's ID: the W&B entity imported when none is entered. */
		org: string;
		/** What people call it. */
		label: string;
		/** The org's latest import, or null if it never had one. */
		job: OrgImport | null;
		/** Only super admins start and cancel imports. */
		superadmin: boolean;
		now: number;
	}

	let { org, label, job, superadmin, now }: Props = $props();

	const running = $derived(job?.state === 'running');
	$effect(() => {
		if (running) return pollImport();
	});

	// Polling reloads this page only. When the import moves on to another project, or ends,
	// reload the sidebar too, which lists the org's projects.
	let seen: string | null = null;
	$effect(() => {
		const mark = job ? `${job.state}/${job.current_project}/${job.projects_done}` : null;
		if (seen !== null && mark !== seen) invalidateAll();
		seen = mark;
	});

	const STATES: Record<ImportState, { label: string; tone: string }> = {
		running: { label: 'Importing', tone: 'bg-state-running' },
		finished: { label: 'Finished', tone: 'bg-state-finished' },
		failed: { label: 'Failed', tone: 'bg-state-failed' },
		cancelled: { label: 'Cancelled', tone: 'bg-faint' },
		interrupted: { label: 'Interrupted', tone: 'bg-faint' }
	};

	// Copied and skipped runs are both done with; the total can grow as runs are listed.
	const handled = $derived(job ? job.runs_done + job.runs_skipped + job.runs_failed : 0);
	const share = $derived(
		job && job.runs_total > 0
			? Math.min(1, handled / job.runs_total)
			: job?.state === 'finished'
				? 1
				: 0
	);

	let resuming = $state(false);
	const starting = new Mutation({
		validate: (form) => {
			const wandb = wandbImport(form, org);
			return wandb && 'error' in wandb ? wandb.error : null;
		},
		success: () => 'Import started',
		done: () => (resuming = false)
	});
	// It may have ended on its own just before the cancel arrived.
	const cancelling = new Mutation<{ import: OrgImport }>({
		toastErrors: true,
		success: (data) =>
			data.import.state === 'cancelled' ? 'Import cancelled' : 'The import had already ended'
	});
</script>

{#if job}
	<div class="mt-4 grid gap-3 rounded-lg border border-divider p-4">
		<div class="flex flex-wrap items-center gap-x-3 gap-y-1">
			<Badge variant="secondary" class="gap-1.5 text-foreground">
				<span class={['size-1.5 rounded-full', STATES[job.state].tone]}></span>
				{STATES[job.state].label}
			</Badge>
			<span class="min-w-0 truncate">
				From <span class="font-medium">{job.entity}</span>
				{#if job.base_url !== 'https://api.wandb.ai'}
					<span class="text-muted-foreground">on {job.base_url}</span>
				{/if}
			</span>
			<span class="ml-auto text-[13px] text-muted-foreground" title={dateTime(job.started_at)}>
				{#if job.finished_at}
					Ended {relativeTime(job.finished_at, now)}
				{:else}
					Started {relativeTime(job.started_at, now)}
				{/if}
			</span>
		</div>

		<div
			class="h-1.5 overflow-hidden rounded-full bg-muted"
			role="progressbar"
			aria-label="Runs imported"
			aria-valuemin={0}
			aria-valuemax={job.runs_total}
			aria-valuenow={handled}
		>
			<div
				class={['h-full rounded-full transition-[width]', STATES[job.state].tone]}
				style:width="{share * 100}%"
			></div>
		</div>

		<p class="text-[13px] text-muted-foreground">
			<span class="text-foreground tabular-nums">
				{handled.toLocaleString('en-US')} of {count(job.runs_total, 'run')}
			</span>
			· {job.projects_done.toLocaleString('en-US')} of {count(job.projects_total, 'project')}
			{#if running && job.current_project}
				· copying <span class="text-foreground">{job.current_project}</span>
			{/if}
			{#if job.runs_skipped}· {job.runs_skipped.toLocaleString('en-US')} already here{/if}
			{#if job.runs_failed}
				· <span class="text-destructive">{job.runs_failed.toLocaleString('en-US')} failed</span>
			{/if}
		</p>

		{#if job.error}
			<p class="text-[13px] break-words text-destructive">
				{job.state === 'failed' ? 'Stopped:' : 'Last failure:'}
				{job.error}
			</p>
		{/if}
		{#if job.state === 'interrupted'}
			<p class="text-[13px] text-muted-foreground">
				Flock restarted during the import. Flock doesn’t keep the API key, so resume it with the
				key: the runs already copied are skipped.
			</p>
		{/if}

		{#if superadmin}
			{#if running}
				<form method="POST" action="?/cancelImport" use:enhance={cancelling.enhance}>
					<Button type="submit" variant="outline" disabled={cancelling.pending}>
						{cancelling.pending ? 'Cancelling…' : 'Cancel import'}
					</Button>
				</form>
			{:else if !resuming}
				<div>
					<Button variant="outline" onclick={() => (resuming = true)}>
						<DownloadIcon />
						{job.state === 'finished' ? 'Import again' : 'Resume import'}
					</Button>
				</div>
			{/if}
		{/if}
	</div>
{:else if superadmin && !resuming}
	<p class="mt-2 max-w-[62ch] text-muted-foreground">
		Copy every project and run of a W&B team or user into {label}.
	</p>
	<div class="mt-4">
		<Button variant="outline" onclick={() => (resuming = true)}>
			<DownloadIcon /> Import from W&B
		</Button>
	</div>
{/if}

{#if superadmin && resuming && !running}
	<form
		method="POST"
		action="?/startImport"
		class="mt-4 grid max-w-md gap-4"
		use:enhance={starting.enhance}
	>
		<WandbImportFields
			id="org-wandb"
			{org}
			entity={job?.entity ?? ''}
			url={job?.base_url ?? ''}
			always
		/>
		<p class="text-[13px] text-muted-foreground">
			Runs {label} already has are skipped, so only what’s missing is copied.
		</p>
		<FormError error={starting.error} />
		<div class="flex gap-2">
			<Button type="submit" disabled={starting.pending}>
				{starting.pending
					? 'Starting…'
					: !job
						? 'Start import'
						: job.state === 'finished'
							? 'Import again'
							: 'Resume import'}
			</Button>
			<Button variant="outline" onclick={() => (resuming = false)}>Cancel</Button>
		</div>
	</form>
{/if}
