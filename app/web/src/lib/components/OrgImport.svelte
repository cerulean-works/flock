<script lang="ts" module>
	import type { OrgImport as Job } from '#lib/types.ts';

	/** How long an import that stopped short stays on the page, for someone to resume it. */
	const ATTENTION = 3 * 86_400;

	/**
	 * Whether the import is worth showing: while it runs, or for a few days after it failed or was
	 * interrupted. A finished or cancelled import leaves nothing behind.
	 */
	export function importShown(job: Job | null, now: number): job is Job {
		if (!job) return false;
		if (job.state === 'running') return true;
		if (job.state !== 'failed' && job.state !== 'interrupted') return false;
		return now - (job.finished_at ?? job.started_at) < ATTENTION;
	}
</script>

<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import ActivityIcon from '@lucide/svelte/icons/activity';
	import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import FolderInputIcon from '@lucide/svelte/icons/folder-input';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import { toast } from 'svelte-sonner';
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import FormError from '#lib/components/FormError.svelte';
	import MetaItem from '#lib/components/MetaItem.svelte';
	import MetaRow from '#lib/components/MetaRow.svelte';
	import WandbImportFields from '#lib/components/WandbImportFields.svelte';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { ImportState } from '#lib/types.ts';
	import { count, dateTime, relativeTime } from '#lib/utils.ts';
	import { pollImport, wandbImport } from '#lib/wandb.ts';

	interface Props {
		/** The org's ID: the W&B entity imported when none is entered. */
		org: string;
		/** What people call it. */
		label: string;
		/** The org's latest import, or null if it never had one. Shown only while it matters. */
		job: Job | null;
		/** Only super admins start and cancel imports. */
		superadmin: boolean;
		now: number;
	}

	let { org, label, job, superadmin, now }: Props = $props();

	const running = $derived(job?.state === 'running');
	// The import on show: none once it has finished, been cancelled, or failed a while ago.
	const shown = $derived(importShown(job, now) ? job : null);
	$effect(() => {
		if (running) return pollImport();
	});

	// Polling reloads this page only. When the import moves on to another project, or ends,
	// reload the sidebar too, which lists the org's projects. A finished import leaves the page,
	// so a toast says it finished.
	let seen: string | null = null;
	$effect(() => {
		const mark = job ? `${job.state}/${job.current_project}/${job.projects_done}` : null;
		if (seen !== null && mark !== seen) {
			if (seen.startsWith('running/') && job?.state === 'finished')
				toast.success(`Imported ${count(job.runs_done, 'run')} from ${job.entity}`);
			invalidateAll();
		}
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
	const handled = $derived(shown ? shown.runs_done + shown.runs_skipped + shown.runs_failed : 0);
	const share = $derived(
		shown && shown.runs_total > 0 ? Math.min(1, handled / shown.runs_total) : 0
	);
	const n = (v: number) => v.toLocaleString('en-US');

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
	const cancelling = new Mutation<{ import: Job }>({
		toastErrors: true,
		success: (data) =>
			data.import.state === 'cancelled' ? 'Import cancelled' : 'The import had already ended'
	});
</script>

{#if shown}
	<div class="mt-4 grid gap-3 rounded-lg border border-divider p-4">
		<div class="flex flex-wrap items-center gap-x-3 gap-y-1">
			<Badge variant="secondary" class="gap-1.5 text-foreground">
				<span class={['size-1.5 rounded-full', STATES[shown.state].tone]}></span>
				{STATES[shown.state].label}
			</Badge>
			<span class="min-w-0 truncate">
				From <span class="font-medium">{shown.entity}</span>
				{#if shown.base_url !== 'https://api.wandb.ai'}
					<span class="text-muted-foreground">on {shown.base_url}</span>
				{/if}
			</span>
			<span
				class="ml-auto text-[13px] text-muted-foreground"
				{@attach hint(dateTime(shown.started_at))}
			>
				{#if shown.finished_at}
					Ended {relativeTime(shown.finished_at, now)}
				{:else}
					Started {relativeTime(shown.started_at, now)}
				{/if}
			</span>
		</div>

		<div
			class="h-1.5 overflow-hidden rounded-full bg-muted"
			role="progressbar"
			aria-label="Runs imported"
			aria-valuemin={0}
			aria-valuemax={shown.runs_total}
			aria-valuenow={handled}
		>
			<div
				class={['h-full rounded-full transition-[width]', STATES[shown.state].tone]}
				style:width="{share * 100}%"
			></div>
		</div>

		<MetaRow>
			<MetaItem icon={ActivityIcon} class="text-foreground">
				{n(handled)} of {count(shown.runs_total, 'run')}
			</MetaItem>
			<MetaItem icon={FolderIcon}>
				{n(shown.projects_done)} of {count(shown.projects_total, 'project')}
			</MetaItem>
			{#if running && shown.current_project}
				<MetaItem icon={FolderInputIcon}>
					Copying <span class="text-foreground">{shown.current_project}</span>
				</MetaItem>
			{/if}
			{#if shown.runs_skipped}
				<MetaItem icon={CircleCheckIcon}>{n(shown.runs_skipped)} already here</MetaItem>
			{/if}
			{#if shown.runs_failed}
				<MetaItem icon={TriangleAlertIcon} class="text-destructive">
					{n(shown.runs_failed)} failed
				</MetaItem>
			{/if}
		</MetaRow>

		{#if shown.error}
			<p class="text-[13px] break-words text-destructive">
				{shown.state === 'failed' ? 'Stopped by an error.' : 'Last error.'}
				{shown.error}
			</p>
		{/if}
		{#if shown.state === 'interrupted'}
			<p class="text-[13px] text-muted-foreground">
				Flock restarted during the import and doesn’t keep the API key. Resume it with the key and
				the runs already copied are skipped.
			</p>
		{/if}

		{#if superadmin}
			{#if running}
				<form method="POST" action="?/cancelImport" use:enhance={cancelling.enhance}>
					<Button type="submit" variant="outline" pending={cancelling.pending}>
						Cancel import
					</Button>
				</form>
			{:else if !resuming}
				<div>
					<Button variant="outline" onclick={() => (resuming = true)}>
						<DownloadIcon /> Resume import
					</Button>
				</div>
			{/if}
		{/if}
	</div>
{:else if superadmin && !resuming}
	<p class="mt-2 text-muted-foreground">
		Copy every project and run from a W&B team or user into {label}.
	</p>
	<div class="mt-4">
		<Button variant="outline" onclick={() => (resuming = true)}>
			<DownloadIcon /> Import from W&B
		</Button>
	</div>
{/if}

{#if superadmin && resuming && !running}
	<form method="POST" action="?/startImport" class="mt-4 grid gap-4" use:enhance={starting.enhance}>
		<WandbImportFields
			id="org-wandb"
			{org}
			entity={job?.entity ?? ''}
			url={job?.base_url ?? ''}
			always
		/>
		<p class="text-[13px] text-muted-foreground">Runs already in {label} are skipped.</p>
		<FormError error={starting.error} />
		<div class="flex gap-2">
			<Button type="submit" pending={starting.pending}>
				{shown ? 'Resume import' : 'Start import'}
			</Button>
			<Button variant="outline" onclick={() => (resuming = false)}>Cancel</Button>
		</div>
	</form>
{/if}
