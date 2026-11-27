<script lang="ts">
	import type { Scope } from '#lib/scope.ts';
	import { toast } from 'svelte-sonner';
	import { Input } from '#lib/components/ui/input/index.js';
	import {
		ModelError,
		isCheckpoint,
		modelApi,
		modelCall,
		modelRef,
		tagError
	} from '#lib/models.ts';
	import AnchoredForm from '../AnchoredForm.svelte';

	interface Props {
		open: boolean;
		/** The ⋯ menu it opens from. */
		anchor?: HTMLElement | null;
		scope: Scope;
		name: string;
		/** The version to tag. */
		version: number;
		/** Called once the tag is on the version. */
		ontagged: () => void;
	}

	let { open = $bindable(), anchor = null, scope, name, version, ontagged }: Props = $props();

	// Reset once the form has closed, so its text stays put while it animates closed.
	let tag = $state('');
	let error = $state<string | null>(null);
	let pending = $state(false);
	/** The version the tag is already on, when it can move: the form then offers to. */
	let taken = $state<number | null>(null);
	/** The version a checkpoint tag is already on. It never moves (D107). */
	let fixed = $state<number | null>(null);

	function reset() {
		tag = '';
		error = null;
		taken = fixed = null;
	}

	async function submit(move: boolean) {
		const invalid = tagError(tag);
		if (invalid) {
			error = invalid;
			return;
		}
		error = null;
		pending = true;
		try {
			await modelCall(modelApi(scope, name, `tags/${encodeURIComponent(tag)}`), {
				method: 'PUT',
				body: { version, move }
			});
			toast.success(move ? `Moved ${tag} to version ${version}` : `Tagged version ${version}`);
			open = false;
			ontagged();
		} catch (e) {
			if (e instanceof ModelError && e.status === 409 && typeof e.body.version === 'number') {
				if (isCheckpoint(tag)) fixed = e.body.version;
				else taken = e.body.version;
			} else error = (e as Error).message;
		} finally {
			pending = false;
		}
	}
</script>

<AnchoredForm
	bind:open
	{anchor}
	align="end"
	title="Tag version {version}"
	submit={taken !== null ? 'Move tag' : 'Add tag'}
	{pending}
	{error}
	onsubmit={() => submit(taken !== null)}
	onclosed={reset}
>
	<Input
		bind:value={tag}
		oninput={() => {
			taken = fixed = null;
			error = null;
		}}
		aria-label="Name"
		placeholder="e.g., release"
		autocomplete="off"
		spellcheck="false"
		aria-invalid={!!error}
	/>
	{#snippet note()}
		{#if taken !== null}
			<p class="text-foreground" role="status">
				{tag} is on version {taken}. Move it to version {version}?
			</p>
		{:else if fixed !== null}
			<p class="text-destructive" role="alert">
				{tag} is on version {fixed}. Checkpoint tags don’t move.
			</p>
		{:else}
			<p>
				Load it as <span class="font-mono">{modelRef(scope, name)}@{tag || 'tag'}</span> once it’s tagged.
			</p>
		{/if}
	{/snippet}
</AnchoredForm>
