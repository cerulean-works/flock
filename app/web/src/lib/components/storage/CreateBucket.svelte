<script lang="ts">
	import PlusIcon from '@lucide/svelte/icons/plus';
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { bucketNameError } from '#lib/s3.ts';
	import FormError from '../FormError.svelte';

	/** `nameLimit`: the longest name this server allows. */
	let { nameLimit }: { nameLimit: number } = $props();

	let name = $state('');
	// Checked as it's typed; an empty field says nothing until it's sent.
	const invalid = $derived(name ? bucketNameError(name, nameLimit) : null);
	const creating = new Mutation<{ bucket: string }>({
		validate: () => bucketNameError(name, nameLimit),
		success: ({ bucket }) => `Created bucket ${bucket}`,
		done: () => (name = '')
	});
</script>

<form
	method="POST"
	action="?/createBucket"
	class="grid max-w-120 gap-2"
	use:enhance={creating.enhance}
>
	<input type="hidden" name="limit" value={nameLimit} />
	<div class="flex gap-2">
		<Input
			name="name"
			bind:value={name}
			placeholder="e.g. checkpoints"
			aria-label="Bucket name"
			aria-invalid={!!(invalid ?? creating.error)}
			autocomplete="off"
			spellcheck="false"
			maxlength={nameLimit}
			class="font-mono text-[13px]"
		/>
		<Button type="submit" variant="outline" disabled={creating.pending || !!invalid}>
			<PlusIcon />
			{creating.pending ? 'Creating…' : 'Create bucket'}
		</Button>
	</div>
	<FormError error={invalid ?? creating.error} />
	<p class="text-[13px] text-muted-foreground">Bucket names can’t be changed later.</p>
</form>
