<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { dateTime } from '#lib/utils.ts';
	import CopyBlock from './CopyBlock.svelte';
	import FormError from './FormError.svelte';

	interface Props {
		open: boolean;
		user: { id: number; name: string; email: string };
		/** The page action creating the link (`?/reset`). */
		action: string;
	}

	let { open = $bindable(), user, action }: Props = $props();

	// Flock shows the link only once.
	let link = $state<{ url: string; expires_at: number } | null>(null);
	const mutation = new Mutation<{ reset: { url: string; expires_at: number } }>({
		done: (data) => (link = data.reset)
	});
</script>

<Dialog.Root
	bind:open
	onOpenChangeComplete={(o) => {
		if (!o) {
			link = null;
			mutation.reset();
		}
	}}
>
	<Dialog.Content class="sm:max-w-md">
		<Dialog.Header>
			<Dialog.Title>Reset {user.name}’s password</Dialog.Title>
			<Dialog.Description>
				{#if link}
					Send it to {user.email}: it isn’t shown again. Works once, until {dateTime(
						link.expires_at
					)}.
				{:else}
					A one-time link for {user.email} to set a new password. Flock sends no email: pass it on yourself.
				{/if}
			</Dialog.Description>
		</Dialog.Header>
		{#if link}
			<CopyBlock code={link.url} />
			<Dialog.Footer>
				<Dialog.Close>
					{#snippet child({ props })}
						<Button {...props}>Done</Button>
					{/snippet}
				</Dialog.Close>
			</Dialog.Footer>
		{:else}
			<form method="POST" {action} class="grid gap-4" use:enhance={mutation.enhance}>
				<input type="hidden" name="user" value={user.id} />
				<FormError error={mutation.error} />
				<Dialog.Footer>
					<Dialog.Close>
						{#snippet child({ props })}
							<Button {...props} variant="outline">Cancel</Button>
						{/snippet}
					</Dialog.Close>
					<Button type="submit" disabled={mutation.pending}>
						{mutation.pending ? 'Creating…' : 'Create reset link'}
					</Button>
				</Dialog.Footer>
			</form>
		{/if}
	</Dialog.Content>
</Dialog.Root>
