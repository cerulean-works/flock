<script lang="ts">
	import PlusIcon from '@lucide/svelte/icons/plus';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { bucketNameError } from '#lib/s3.ts';
	import AnchoredForm from '../AnchoredForm.svelte';

	/** `nameLimit`: the longest name this server allows. */
	let { nameLimit }: { nameLimit: number } = $props();

	let open = $state(false);
	let button = $state<HTMLElement | null>(null);
	let name = $state('');
	// Checked as it's typed; an empty field says nothing until it's sent.
	const invalid = $derived(name ? bucketNameError(name, nameLimit) : null);
	const creating = new Mutation<{ bucket: string }>({
		validate: () => bucketNameError(name, nameLimit),
		success: ({ bucket }) => `Created bucket ${bucket}`,
		done: () => (open = false)
	});
</script>

<!-- A button for the section header, opening a small form under it that names the new bucket. -->
<Button
	variant={open ? 'quiet-on' : 'quiet'}
	size="bar"
	bind:ref={button}
	onclick={() => (open = !open)}
>
	<PlusIcon /> Create bucket
</Button>
<AnchoredForm
	bind:open
	anchor={button}
	align="end"
	title="Create bucket"
	submit="Create bucket"
	action="?/createBucket"
	enhance={creating.enhance}
	pending={creating.pending}
	error={invalid ?? creating.error}
	disabled={!!invalid}
	onclosed={() => {
		name = '';
		creating.reset();
	}}
>
	<input type="hidden" name="limit" value={nameLimit} />
	<Input
		name="name"
		bind:value={name}
		aria-label="Name"
		placeholder="e.g., checkpoints"
		aria-invalid={!!(invalid ?? creating.error)}
		autocomplete="off"
		spellcheck="false"
		maxlength={nameLimit}
		class="font-mono text-[13px] placeholder:font-sans"
	/>
	{#snippet note()}
		Lowercase letters, digits, hyphens and dots, up to {nameLimit} characters. Names can’t be changed
		later.
	{/snippet}
</AnchoredForm>
