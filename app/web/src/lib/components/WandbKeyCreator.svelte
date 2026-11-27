<script lang="ts">
	import KeyRoundIcon from '@lucide/svelte/icons/key-round';
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import AnchoredForm from './AnchoredForm.svelte';
	import FormError from './FormError.svelte';
	import SecretField from './SecretField.svelte';

	/**
	 * `named` asks what the key is for, in a small form opened from the button (G4); otherwise
	 * the button makes one called "default" at once.
	 */
	let { named = false }: { named?: boolean } = $props();

	// Shown once: Flock keeps only a hash of it. Shown as the server gives it, never parsed.
	let key = $state<string | null>(null);
	let open = $state(false);
	const mutation = new Mutation<{ key: string; name: string }>({
		success: ({ name }) => `Created wandb key “${name}”`,
		done: (data) => {
			key = data.key;
			open = false;
		}
	});
</script>

{#if key}
	<div class="grid gap-2">
		<SecretField label="wandb key" value={key} copy labelled={false} />
		<p class="text-[13px] text-muted-foreground">Copy it now. It won’t be shown again.</p>
	</div>
{:else if named}
	<AnchoredForm
		bind:open
		title="New wandb key"
		submit="Create key"
		action="/account?/createKey"
		enhance={mutation.enhance}
		pending={mutation.pending}
		error={mutation.error}
		align="start"
		onclosed={() => mutation.reset()}
	>
		{#snippet trigger(props)}
			<Button {...props} variant="outline"><KeyRoundIcon /> Create wandb key</Button>
		{/snippet}
		<Input
			name="name"
			placeholder="Name, e.g., cluster"
			class="h-8"
			aria-label="Key name"
			autocomplete="off"
			maxlength={128}
		/>
	</AnchoredForm>
{:else}
	<form
		method="POST"
		action="/account?/createKey"
		class="flex flex-wrap items-center gap-2"
		use:enhance={mutation.enhance}
	>
		<Button type="submit" variant="outline" pending={mutation.pending}>
			<KeyRoundIcon />
			Create wandb key
		</Button>
		<FormError error={mutation.error} />
	</form>
{/if}
