<script lang="ts">
	import type { Scope } from '#lib/scope.ts';
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { Input } from '#lib/components/ui/input/index.js';
	import { datasetCall, datasetsApi, datasetsHref, pathError } from '#lib/datasets.ts';
	import AnchoredForm from '../AnchoredForm.svelte';

	interface Props {
		open: boolean;
		/** The ⋯ menu it opens from. */
		anchor?: HTMLElement | null;
		scope: Scope;
		/** The dataset's or folder's path now. */
		path: string;
		/** A folder, renamed with everything in it (D133). */
		folder?: boolean;
		/** Once renamed, in place of opening the new path (the listing's row menu). */
		onrenamed?: (to: string) => void | Promise<void>;
	}

	let {
		open = $bindable(),
		anchor = null,
		scope,
		path,
		folder = false,
		onrenamed
	}: Props = $props();

	// Starts as the current path, a rename being a move too. Reset once the form has closed,
	// so its text stays put while it animates closed.
	let to = $derived(path);
	let error = $state<string | null>(null);
	let pending = $state(false);

	async function submit() {
		const target = to.trim();
		// Unchanged: nothing to do.
		if (target === path) {
			open = false;
			return;
		}
		const invalid = pathError(target);
		if (invalid) {
			error = invalid;
			return;
		}
		error = null;
		pending = true;
		try {
			await datasetCall(datasetsApi(scope, 'rename'), {
				method: 'POST',
				body: { path, to: target }
			});
			toast.success(`Renamed to ${target}`);
			open = false;
			if (onrenamed) await onrenamed(target);
			else await goto(datasetsHref(scope, target), { replace: true });
		} catch (e) {
			error = (e as Error).message;
		} finally {
			pending = false;
		}
	}
</script>

<!-- While the copy runs, the form stays open: the request decides how it ends. -->
<AnchoredForm
	bind:open
	{anchor}
	wide
	title="Rename {path}{folder ? '/' : ''}"
	submit="Rename"
	{pending}
	{error}
	onsubmit={submit}
	onclosed={() => {
		to = path;
		error = null;
	}}
>
	<Input
		aria-label="New path"
		placeholder="e.g., eval/mmlu-v2"
		class="font-mono text-base sm:text-[13px]"
		bind:value={to}
		disabled={pending}
		autocomplete="off"
		spellcheck="false"
		aria-invalid={!!error}
	/>
	{#snippet note()}
		{#if folder}Everything in it moves with it.{/if}
		Anything still writing to the old path will create a new dataset there.
	{/snippet}
</AnchoredForm>
