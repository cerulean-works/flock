<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { TRASH_DAYS, isCheckpoint, modelApi, modelCall, modelsHref } from '#lib/models.ts';
	import type { ModelVersion } from '#lib/types.ts';
	import { count } from '#lib/utils.ts';
	import FormError from '../FormError.svelte';

	interface Props {
		open: boolean;
		org: string;
		version: ModelVersion | null;
		/** It's the version the page shows: the page goes to the primary after. */
		shown: boolean;
		ondeleted: () => void;
	}

	let { open = $bindable(), org, version, shown, ondeleted }: Props = $props();

	const ref = $derived(version ? `${version.model}@${version.number}` : '');
	// What the API would refuse (D109), said before anything is typed, in its words.
	const kept = $derived(version ? version.tags.filter((t) => !isCheckpoint(t)) : []);
	const refusal = $derived(
		!version
			? null
			: version.primary
				? `Version ${version.number} is the primary version. Make another version primary first.`
				: kept.length
					? `Version ${version.number} is tagged ${kept.join(', ')}. Move or remove the tags first.`
					: null
	);

	// Typing the name is the confirmation; reset whenever the dialog opens.
	let typed = $derived.by(() => {
		void open;
		return '';
	});
	let error = $derived.by((): string | null => {
		void open;
		return null;
	});
	let pending = $state(false);

	async function remove() {
		if (!version) return;
		pending = true;
		error = null;
		try {
			await modelCall(modelApi(org, version.name, `versions/${version.number}`), {
				method: 'DELETE'
			});
			toast.success(`Deleted ${ref}`);
			open = false;
			if (shown) await goto(modelsHref(org, version.name), { replace: true });
			else ondeleted();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			pending = false;
		}
	}
</script>

<AlertDialog.Root bind:open={() => open, (o) => (open = pending ? true : o)}>
	<AlertDialog.Content class="sm:max-w-md">
		<AlertDialog.Header>
			<AlertDialog.Title>Delete version {version?.number}?</AlertDialog.Title>
			<AlertDialog.Description>
				{refusal ?? `Org admins can restore it for ${TRASH_DAYS} days.`}
			</AlertDialog.Description>
		</AlertDialog.Header>
		{#if refusal}
			<AlertDialog.Footer>
				<AlertDialog.Cancel>Close</AlertDialog.Cancel>
			</AlertDialog.Footer>
		{:else}
			<form
				class="grid gap-4"
				onsubmit={(e) => {
					e.preventDefault();
					if (typed === ref) void remove();
				}}
			>
				{#if version?.eval_runs}
					<p class="bg-card px-3 py-2.5 text-[13px]" role="status">
						{count(version.eval_runs, 'eval run')} scored this version. Their results stay, but the version
						they name can’t be loaded after it’s deleted.
					</p>
				{/if}
				<div class="grid gap-2">
					<Label for="delete-version-ref">
						Type <span class="font-mono text-[12.5px]">{ref}</span> to confirm
					</Label>
					<Input
						id="delete-version-ref"
						bind:value={typed}
						autocomplete="off"
						spellcheck="false"
						disabled={pending}
					/>
				</div>
				<FormError {error} />
				<AlertDialog.Footer>
					<AlertDialog.Cancel disabled={pending}>Cancel</AlertDialog.Cancel>
					<Button type="submit" variant="destructive" disabled={typed !== ref || pending}>
						{pending ? 'Deleting…' : 'Delete version'}
					</Button>
				</AlertDialog.Footer>
			</form>
		{/if}
	</AlertDialog.Content>
</AlertDialog.Root>
