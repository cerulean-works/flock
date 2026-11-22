<script lang="ts">
	import FlaskConicalIcon from '@lucide/svelte/icons/flask-conical';
	import { toast } from 'svelte-sonner';
	import { Button } from '#lib/components/ui/button/index.js';
	import { DatasetError, datasetCall } from '#lib/datasets.ts';
	import { type LaunchList, launchesApi } from '#lib/launches.svelte.ts';
	import type { EvalCatalogue, EvalLaunch } from '#lib/types.ts';
	import EvalLaunchDialog from './EvalLaunchDialog.svelte';

	// "Launch eval" on a project's page (026), for members and admins. Hidden on a server that
	// doesn't launch evals, which answers the catalogue with 404.
	interface Props {
		org: string;
		/** The project the launch logs its run to. */
		project: string;
		list: LaunchList;
	}

	let { org, project, list }: Props = $props();

	let open = $state(false);
	let catalogue = $state<EvalCatalogue | null>(null);
	let catalogueError = $state<string | null>(null);
	let unavailable = $state(false);

	// The first answer can take tens of seconds (the worker starts cold): the dialog says so.
	$effect(() => {
		void datasetCall<EvalCatalogue>(launchesApi(org, 'catalogue')).then(
			(found) => (catalogue = found),
			(e: Error) => {
				if (e instanceof DatasetError && e.status === 404) unavailable = true;
				else catalogueError = e.message;
			}
		);
	});

	function started(job: EvalLaunch) {
		list.add(job);
		toast.success(`Launched ${job.model}`);
	}
</script>

{#if !unavailable}
	<Button title="Launch an eval on a Modal H100" onclick={() => (open = true)}>
		<FlaskConicalIcon /><span class="max-sm:sr-only">Launch eval</span>
	</Button>
	<EvalLaunchDialog bind:open {org} {project} {catalogue} {catalogueError} onstarted={started} />
{/if}
