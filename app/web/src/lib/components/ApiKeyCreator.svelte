<script lang="ts">
	import KeyRoundIcon from '@lucide/svelte/icons/key-round';
	import { enhance } from '$app/forms';
	import { page } from '$app/state';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import CopyBlock from './CopyBlock.svelte';
	import FormError from './FormError.svelte';

	/** `named` asks what the key is for; otherwise it is called "default". */
	let { named = false }: { named?: boolean } = $props();

	// Shown once: Flock keeps only a hash of it.
	let key = $state<string | null>(null);
	const mutation = new Mutation<{ key: string; name: string }>({
		success: ({ name }) => `Created API key “${name}”`,
		done: (data) => (key = data.key)
	});
</script>

{#if key}
	<div class="grid gap-2">
		<CopyBlock code="wandb login --host {page.data.flockUrl} {key}" />
		<p class="text-[13px] text-muted-foreground">
			Copy it now: it isn’t shown again. Or set <code class="font-mono text-[12.5px]"
				>WANDB_BASE_URL</code
			>
			and <code class="font-mono text-[12.5px]">WANDB_API_KEY</code>.
		</p>
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
			{mutation.pending ? 'Creating…' : 'Create API key'}
		</Button>
		<FormError error={mutation.error} />
	</form>
{/if}
