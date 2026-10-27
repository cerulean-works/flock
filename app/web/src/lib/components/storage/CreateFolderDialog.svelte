<script lang="ts">
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { bucketCall, folderNameError, s3Uri } from '#lib/storage.ts';
	import FormError from '../FormError.svelte';

	interface Props {
		open: boolean;
		org: string;
		bucket: string;
		/** The folder it goes in (ending in `/`, or empty). */
		prefix: string;
		/** Once it's made, with its key. */
		oncreated: (prefix: string) => void;
	}

	let { open = $bindable(), org, bucket, prefix, oncreated }: Props = $props();

	let name = $state('');
	let pending = $state(false);
	let error = $state<string | null>(null);

	async function create(e: SubmitEvent) {
		e.preventDefault();
		error = folderNameError(name);
		if (error) return;
		pending = true;
		const key = `${prefix}${name.trim()}/`;
		try {
			await bucketCall(org, bucket, 'folders', { prefix: key });
			open = false;
			oncreated(key);
		} catch (err) {
			error = (err as Error).message;
		} finally {
			pending = false;
		}
	}
</script>

<Dialog.Root
	bind:open
	onOpenChangeComplete={(o) => {
		if (o) return;
		name = '';
		error = null;
	}}
>
	<Dialog.Content class="sm:max-w-md">
		<Dialog.Header>
			<Dialog.Title>New folder</Dialog.Title>
			<Dialog.Description class="break-all">
				In <span class="font-mono text-[12.5px]">{s3Uri(bucket, prefix)}</span>. It stays empty
				until you upload into it.
			</Dialog.Description>
		</Dialog.Header>
		<form class="grid gap-4" onsubmit={create}>
			<div class="grid gap-2">
				<Label for="folder-name">Name</Label>
				<Input
					id="folder-name"
					bind:value={name}
					autocomplete="off"
					spellcheck="false"
					autofocus
					aria-invalid={!!error}
				/>
			</div>
			<FormError {error} />
			<Dialog.Footer>
				<Button type="submit" disabled={pending}>{pending ? 'Creating…' : 'Create folder'}</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
