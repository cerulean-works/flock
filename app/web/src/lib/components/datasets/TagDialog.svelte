<script lang="ts">
	import type { Scope } from '#lib/scope.ts';
	import { Input } from '#lib/components/ui/input/index.js';
	import { DatasetError, datasetCall, datasetsApi, tagError } from '#lib/datasets.ts';
	import { toast } from 'svelte-sonner';
	import AnchoredForm from '../AnchoredForm.svelte';

	interface Props {
		open: boolean;
		/** The ⋯ menu it opens from. */
		anchor?: HTMLElement | null;
		scope: Scope;
		path: string;
		/** The version to tag. */
		version: number;
		/** Called once the tag is on the version. */
		ontagged: () => void;
	}

	let { open = $bindable(), anchor = null, scope, path, version, ontagged }: Props = $props();

	// Reset once the form has closed, so its text stays put while it animates closed.
	let name = $state('');
	let error = $state<string | null>(null);
	let pending = $state(false);
	/** The version the name is already on, when it is: the form then offers to move it. */
	let taken = $state<number | null>(null);

	function reset() {
		name = '';
		error = null;
		taken = null;
	}

	async function submit(move: boolean) {
		const invalid = tagError(name);
		if (invalid) {
			error = invalid;
			return;
		}
		error = null;
		pending = true;
		try {
			await datasetCall(datasetsApi(scope, `tags/${encodeURIComponent(name)}`), {
				method: 'PUT',
				body: { path, version, move }
			});
			toast.success(move ? `Moved ${name} to version ${version}` : `Tagged version ${version}`);
			open = false;
			ontagged();
		} catch (e) {
			if (e instanceof DatasetError && e.status === 409 && typeof e.body.version === 'number')
				taken = e.body.version;
			else error = (e as Error).message;
		} finally {
			pending = false;
		}
	}
</script>

<AnchoredForm
	bind:open
	{anchor}
	title="Tag version {version}"
	submit={taken !== null ? 'Move tag' : 'Add tag'}
	{pending}
	{error}
	onsubmit={() => submit(taken !== null)}
	onclosed={reset}
>
	<Input
		bind:value={name}
		oninput={() => (taken = null)}
		aria-label="Name"
		placeholder="e.g., v1.0"
		autocomplete="off"
		spellcheck="false"
		aria-invalid={!!error}
	/>
	{#snippet note()}
		{#if taken !== null}
			<p class="text-foreground" role="status">
				{name} is on version {taken}. Move it to version {version}?
			</p>
		{:else}
			<p>Lance keeps tagged versions when it cleans up old ones.</p>
		{/if}
	{/snippet}
</AnchoredForm>
