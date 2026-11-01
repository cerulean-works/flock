<script lang="ts">
	import KeyRoundIcon from '@lucide/svelte/icons/key-round';
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import FormError from './FormError.svelte';
	import SecretField from './SecretField.svelte';

	/** `named` asks what the key is for; otherwise it is called "default". */
	let { named = false }: { named?: boolean } = $props();

	// Shown once: Flock keeps only a hash of it. Shown as the server gives it, never parsed.
	let key = $state<string | null>(null);
	const mutation = new Mutation<{ key: string; name: string }>({
		success: ({ name }) => `Created wandb key “${name}”`,
		done: (data) => (key = data.key)
	});
</script>

{#if key}
	<div class="grid gap-2">
		<SecretField label="wandb key" value={key} copy labelled={false} />
		<p class="text-[13px] text-muted-foreground">Copy it now. It won’t be shown again.</p>
	</div>
{:else}
	<form
		method="POST"
		action="/account?/createKey"
		class="flex flex-wrap items-center gap-2"
		use:enhance={mutation.enhance}
	>
		{#if named}
			<Input
				name="name"
				placeholder="Name, e.g. cluster"
				class="h-8 w-64"
				aria-label="Key name"
				autocomplete="off"
			/>
		{/if}
		<Button type="submit" variant="outline" disabled={mutation.pending}>
			<KeyRoundIcon />
			{mutation.pending ? 'Creating…' : 'Create wandb key'}
		</Button>
		<FormError error={mutation.error} />
	</form>
{/if}
