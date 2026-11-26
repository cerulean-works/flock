<script lang="ts">
	import PlusIcon from '@lucide/svelte/icons/plus';
	import { enhance } from '$app/forms';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { bucketNameError } from '#lib/s3.ts';
	import FormError from '../FormError.svelte';

	/** `nameLimit`: the longest name this server allows. */
	let { nameLimit }: { nameLimit: number } = $props();

	let open = $state(false);
	let name = $state('');
	// Checked as it's typed; an empty field says nothing until it's sent.
	const invalid = $derived(name ? bucketNameError(name, nameLimit) : null);
	const creating = new Mutation<{ bucket: string }>({
		validate: () => bucketNameError(name, nameLimit),
		success: ({ bucket }) => `Created bucket ${bucket}`,
		done: () => (open = false)
	});
</script>

<!-- A button for the section header, opening a small dialog that names the new bucket. -->
<Dialog.Root
	bind:open
	onOpenChangeComplete={(o) => {
		if (o) return;
		name = '';
		creating.reset();
	}}
>
	<Dialog.Trigger>
		{#snippet child({ props })}
			<Button {...props} variant="quiet" size="bar"><PlusIcon /> Create bucket</Button>
		{/snippet}
	</Dialog.Trigger>
	<Dialog.Content class="sm:max-w-md">
		<Dialog.Header>
			<Dialog.Title>Create bucket</Dialog.Title>
			<Dialog.Description>Bucket names can’t be changed later.</Dialog.Description>
		</Dialog.Header>
		<form method="POST" action="?/createBucket" class="grid gap-4" use:enhance={creating.enhance}>
			<input type="hidden" name="limit" value={nameLimit} />
			<div class="grid gap-2">
				<Label for="bucket-name">Name</Label>
				<Input
					id="bucket-name"
					name="name"
					bind:value={name}
					placeholder="e.g., checkpoints"
					aria-invalid={!!(invalid ?? creating.error)}
					autocomplete="off"
					spellcheck="false"
					autofocus
					maxlength={nameLimit}
					class="font-mono text-[13px] placeholder:font-sans"
				/>
				<p class="text-[13px] text-muted-foreground">
					Lowercase letters, digits, hyphens and dots, up to {nameLimit} characters.
				</p>
			</div>
			<FormError error={invalid ?? creating.error} />
			<Dialog.Footer>
				<Button type="submit" disabled={creating.pending || !!invalid} pending={creating.pending}>
					Create bucket
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
