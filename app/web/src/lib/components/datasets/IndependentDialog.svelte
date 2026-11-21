<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { toast } from 'svelte-sonner';
	import { invalidateAll } from '$app/navigation';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { DatasetError, datasetCall, datasetsApi } from '#lib/datasets.ts';
	import type { DatasetJob } from '#lib/types.ts';
	import { bytes } from '#lib/utils.ts';
	import ConfirmField from '../ConfirmField.svelte';
	import CopyBlock from '../CopyBlock.svelte';
	import FormError from '../FormError.svelte';

	// Making a fork or blend independent (D178): a full copy, and its history goes.
	interface Props {
		open: boolean;
		org: string;
		path: string;
		/** Its data size now, which is roughly what gets copied. */
		size: number | null;
	}

	let { open = $bindable(), org, path, size }: Props = $props();

	let typed = $state('');
	let error = $state<string | null>(null);
	let command = $state<string | null>(null);
	let pending = $state(false);
	let job = $state<DatasetJob | null>(null);

	async function start() {
		pending = true;
		error = null;
		command = null;
		try {
			job = await datasetCall<DatasetJob>(datasetsApi(org, 'independent'), {
				method: 'POST',
				body: { path }
			});
			while (job.state === 'running') {
				await new Promise((r) => setTimeout(r, 1000));
				job = await datasetCall<DatasetJob>(datasetsApi(org, `jobs/${job.id}`));
			}
			if (job.state !== 'finished') throw new Error(job.error ?? `It ${job.state}.`);
			toast.success(`${path} is now independent`);
			open = false;
			await invalidateAll();
		} catch (e) {
			error = (e as Error).message;
			if (e instanceof DatasetError && typeof e.body.command === 'string') command = e.body.command;
		} finally {
			pending = false;
		}
	}
</script>

<AlertDialog.Root
	bind:open={() => open, (o) => (open = pending ? true : o)}
	onOpenChangeComplete={(o) => {
		if (o) return;
		typed = '';
		error = null;
		command = null;
		job = null;
	}}
>
	<AlertDialog.Content class="sm:max-w-md">
		<AlertDialog.Header>
			<AlertDialog.Title>Make {path} independent?</AlertDialog.Title>
			<AlertDialog.Description>
				Its rows are copied into its own files{size !== null ? ` (about ${bytes(size)})` : ''}, so
				it stops reading its sources and they can be moved or deleted again. Its earlier versions
				and their tags are removed.
			</AlertDialog.Description>
		</AlertDialog.Header>
		<form
			class="grid gap-4"
			onsubmit={(e) => {
				e.preventDefault();
				if (typed === path) void start();
			}}
		>
			<ConfirmField id="independent-path" text={path} bind:value={typed} disabled={pending} />
			{#if pending && job}
				<p class="text-[13px] text-muted-foreground tabular-nums">
					Copying… {Math.round(job.progress * 100)}%
				</p>
			{/if}
			<FormError {error} />
			{#if command}<CopyBlock code={command} />{/if}
			<AlertDialog.Footer>
				<AlertDialog.Cancel disabled={pending}>Cancel</AlertDialog.Cancel>
				<Button type="submit" disabled={typed !== path || pending}>
					{#if pending}<LoaderCircleIcon class="animate-spin" /> Copying…{:else}Make independent{/if}
				</Button>
			</AlertDialog.Footer>
		</form>
	</AlertDialog.Content>
</AlertDialog.Root>
