<script lang="ts">
	import { Input } from '#lib/components/ui/input/index.js';
	import { bucketCall, folderNameError, s3Uri } from '#lib/storage.ts';
	import AnchoredForm from '../AnchoredForm.svelte';

	interface Props {
		open: boolean;
		/** The button it opens from. */
		anchor?: HTMLElement | null;
		org: string;
		bucket: string;
		/** The folder it goes in (ending in `/`, or empty). */
		prefix: string;
		/** Once it's made, with its key. */
		oncreated: (prefix: string) => void;
	}

	let { open = $bindable(), anchor = null, org, bucket, prefix, oncreated }: Props = $props();

	let name = $state('');
	let pending = $state(false);
	let error = $state<string | null>(null);

	async function create() {
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

<AnchoredForm
	bind:open
	{anchor}
	title="New folder"
	submit="Create folder"
	{pending}
	{error}
	onsubmit={create}
	onclosed={() => {
		name = '';
		error = null;
	}}
>
	<Input
		bind:value={name}
		aria-label="Name"
		placeholder="Name"
		autocomplete="off"
		spellcheck="false"
		aria-invalid={!!error}
	/>
	{#snippet note()}
		In <span class="font-mono">{s3Uri(bucket, prefix)}</span>. It stays empty until you upload into
		it.
	{/snippet}
</AnchoredForm>
