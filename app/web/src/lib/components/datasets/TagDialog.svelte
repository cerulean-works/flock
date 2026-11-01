<script lang="ts">
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { DatasetError, datasetCall, datasetsApi, tagError } from '#lib/datasets.ts';
	import { toast } from 'svelte-sonner';
	import FormError from '../FormError.svelte';

	interface Props {
		open: boolean;
		org: string;
		path: string;
		/** The version to tag. */
		version: number;
		/** Called once the tag is on the version. */
		ontagged: () => void;
	}

	let { open = $bindable(), org, path, version, ontagged }: Props = $props();

	// Reset whenever the dialog opens.
	let name = $derived.by(() => {
		void open;
		return '';
	});
	let error = $derived.by((): string | null => {
		void open;
		return null;
	});
	let pending = $state(false);
	/** The version the name is already on, when it is: the dialog then offers to move it. */
	let taken = $derived.by((): number | null => {
		void open;
		return null;
	});

	async function submit(move: boolean) {
		const invalid = tagError(name);
		if (invalid) {
			error = invalid;
			return;
		}
		error = null;
		pending = true;
		try {
			await datasetCall(datasetsApi(org, `tags/${encodeURIComponent(name)}`), {
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

<Dialog.Root bind:open>
	<Dialog.Content class="sm:max-w-md">
		<Dialog.Header>
			<Dialog.Title>Tag version {version}</Dialog.Title>
			<Dialog.Description>
				Lance keeps tagged versions when it cleans up old ones.
			</Dialog.Description>
		</Dialog.Header>
		<form
			class="grid gap-4"
			onsubmit={(e) => {
				e.preventDefault();
				void submit(taken !== null);
			}}
		>
			<div class="grid gap-2">
				<Label for="tag-name">Name</Label>
				<Input
					id="tag-name"
					bind:value={name}
					oninput={() => (taken = null)}
					placeholder="v1.0"
					autocomplete="off"
					spellcheck="false"
				/>
			</div>
			{#if taken !== null}
				<p class="text-[13px]" role="status">
					{name} is on version {taken}. Move it to version {version}?
				</p>
			{/if}
			<FormError {error} />
			<Dialog.Footer>
				<Button type="button" variant="outline" onclick={() => (open = false)}>Cancel</Button>
				<Button type="submit" disabled={pending}>
					{#if taken !== null}
						{pending ? 'Moving…' : 'Move tag'}
					{:else}
						{pending ? 'Adding…' : 'Add tag'}
					{/if}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
