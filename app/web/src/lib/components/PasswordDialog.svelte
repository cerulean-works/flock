<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { UserInfo } from '#lib/types.ts';
	import FormError from './FormError.svelte';

	interface Props {
		open: boolean;
		profile: Pick<UserInfo, 'email' | 'has_password' | 'google'>;
	}

	let { open = $bindable(), profile }: Props = $props();

	// Whether there was a password as the form was sent: the reloaded profile always has one.
	let had = false;
	const saving = new Mutation({
		submit: () => (had = profile.has_password),
		success: () => (had ? 'Password changed' : 'Password set'),
		done: () => (open = false)
	});
</script>

<!-- Flock ends the user's other sessions when the password changes; this one continues. Closing
     the dialog drops what was typed. -->
<Dialog.Root bind:open onOpenChangeComplete={(o) => o || saving.reset()}>
	<Dialog.Content class="sm:max-w-md">
		<Dialog.Header>
			<Dialog.Title>{profile.has_password ? 'Change password' : 'Set password'}</Dialog.Title>
			<Dialog.Description>
				{#if profile.has_password}
					Changing it signs you out everywhere else.
				{:else if profile.google}
					You sign in with Google. Set a password to sign in with your email too.
				{:else}
					Set a password to sign in with your email.
				{/if}
			</Dialog.Description>
		</Dialog.Header>
		<form method="POST" action="?/password" class="grid gap-4" use:enhance={saving.enhance}>
			<!-- For password managers: the account the password belongs to. -->
			<input
				type="email"
				name="email"
				value={profile.email}
				autocomplete="username"
				hidden
				readonly
			/>
			{#if profile.has_password}
				<div class="grid gap-2">
					<Label for="current-password">Current password</Label>
					<Input
						id="current-password"
						name="current"
						type="password"
						autocomplete="current-password"
						required
					/>
				</div>
			{/if}
			<div class="grid gap-2">
				<Label for="new-password">New password</Label>
				<Input
					id="new-password"
					name="new"
					type="password"
					autocomplete="new-password"
					minlength={8}
					required
				/>
				<p class="text-xs text-muted-foreground">At least 8 characters.</p>
			</div>
			<div class="grid gap-2">
				<Label for="confirm-password">Confirm new password</Label>
				<Input
					id="confirm-password"
					name="confirm"
					type="password"
					autocomplete="new-password"
					required
				/>
			</div>
			<FormError error={saving.error} />
			<Dialog.Footer>
				<Dialog.Close>
					{#snippet child({ props })}
						<Button {...props} variant="outline">Cancel</Button>
					{/snippet}
				</Dialog.Close>
				<Button type="submit" disabled={saving.pending}>
					{saving.pending ? 'Saving…' : profile.has_password ? 'Change password' : 'Set password'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
