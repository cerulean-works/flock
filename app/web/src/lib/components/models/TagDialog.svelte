<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { ModelError, isCheckpoint, modelApi, modelCall, tagError } from '#lib/models.ts';
	import FormError from '../FormError.svelte';

	interface Props {
		open: boolean;
		org: string;
		name: string;
		/** The version to tag. */
		version: number;
		/** Called once the tag is on the version. */
		ontagged: () => void;
	}

	let { open = $bindable(), org, name, version, ontagged }: Props = $props();

	// Reset whenever the dialog opens.
	let tag = $derived.by(() => {
		void open;
		return '';
	});
	let error = $derived.by((): string | null => {
		void open;
		return null;
	});
	let pending = $state(false);
	/** The version the tag is already on, when it can move: the dialog then offers to. */
	let taken = $derived.by((): number | null => {
		void open;
		return null;
	});
	/** The version a checkpoint tag is already on. It never moves (D107). */
	let fixed = $derived.by((): number | null => {
		void open;
		return null;
	});

	async function submit(move: boolean) {
		const invalid = tagError(tag);
		if (invalid) {
			error = invalid;
			return;
		}
		error = null;
		pending = true;
		try {
			await modelCall(modelApi(org, name, `tags/${encodeURIComponent(tag)}`), {
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

<Dialog.Root bind:open>
	<Dialog.Content class="sm:max-w-md">
		<Dialog.Header>
			<Dialog.Title>Tag version {version}</Dialog.Title>
			<Dialog.Description>
				Load it as {org}/{name}@{tag || 'tag'} once it’s tagged.
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
				<Label for="model-tag-name">Name</Label>
				<Input
					id="model-tag-name"
					bind:value={tag}
					oninput={() => {
						taken = fixed = null;
						error = null;
					}}
					placeholder="release"
					autocomplete="off"
					spellcheck="false"
				/>
			</div>
			{#if taken !== null}
				<p class="text-[13px]" role="status">
					{tag} is on version {taken}. Move it to version {version}?
				</p>
			{:else if fixed !== null}
				<p class="text-[13px] text-destructive" role="alert">
					{tag} is on version {fixed}. Checkpoint tags don’t move.
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
