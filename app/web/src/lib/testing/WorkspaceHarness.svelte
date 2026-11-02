<script lang="ts">
	import type { Component } from 'svelte';
	import type { WorkspaceData } from '#lib/types.ts';
	import { Workspace, setWorkspace, type RunDialog } from '#lib/workspace/workspace.svelte.ts';

	interface Props {
		data: WorkspaceData;
		/** What to render inside the workspace, with its props. */
		// eslint-disable-next-line @typescript-eslint/no-explicit-any -- any component's props
		component: Component<any, any>;
		props?: Record<string, unknown>;
		/** A run dialog to open from the start. */
		dialog?: RunDialog | null;
	}

	let { data, component: Inner, props = {}, dialog = null }: Props = $props();

	// For component specs: a workspace of `data`, as a project's or a run's page sets one up.
	const ws = new Workspace(
		() => data.meta,
		() => data.page
	);
	setWorkspace(ws);
	// svelte-ignore state_referenced_locally
	ws.dialog = dialog;
</script>

<Inner {...props} />
