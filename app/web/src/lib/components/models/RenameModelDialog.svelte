<script lang="ts">
	import type { Scope } from '#lib/scope.ts';
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { Input } from '#lib/components/ui/input/index.js';
	import { modelApi, modelCall, modelNameError, modelsHref } from '#lib/models.ts';
	import AnchoredForm from '../AnchoredForm.svelte';

	interface Props {
		open: boolean;
		/** The ⋯ menu it opens from. */
		anchor?: HTMLElement | null;
		scope: Scope;
		/** The model's name now. */
		name: string;
		/** Once renamed, in place of opening the model's new page (the list's row menu). */
		onrenamed?: (to: string) => void | Promise<void>;
	}

	let { open = $bindable(), anchor = null, scope, name, onrenamed }: Props = $props();

	// Reset once the form has closed, so its text stays put while it animates closed.
	let to = $derived(name);
	let error = $state<string | null>(null);
	let pending = $state(false);

	async function submit() {
		const target = to.trim();
		const invalid = target === name ? 'Enter a new name' : modelNameError(target);
		if (invalid) {
			error = invalid;
			return;
		}
		error = null;
		pending = true;
		try {
			await modelCall(modelApi(scope, name), { method: 'PATCH', body: { name: target } });
			toast.success(`Renamed to ${target}`);
			open = false;
			if (onrenamed) await onrenamed(target);
			else await goto(modelsHref(scope, target), { replace: true });
		} catch (e) {
			error = (e as Error).message;
		} finally {
			pending = false;
		}
	}
</script>

<AnchoredForm
	bind:open
	{anchor}
	align="end"
	title="Rename {name}"
	submit="Rename"
	{pending}
	{error}
	onsubmit={submit}
	onclosed={() => {
		to = name;
		error = null;
	}}
>
	<Input
		aria-label="New name"
		placeholder="e.g., olmo2-1b-sft"
		class="font-mono text-[13px]"
		bind:value={to}
		disabled={pending}
		autocomplete="off"
		spellcheck="false"
		aria-invalid={!!error}
	/>
	{#snippet note()}
		Code that loads it by its old name stops working. Its files stay where they are.
	{/snippet}
</AnchoredForm>
