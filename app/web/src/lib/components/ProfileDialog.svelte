<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { UserInfo } from '#lib/types.ts';
	import AvatarField from './AvatarField.svelte';
	import FormError from './FormError.svelte';

	interface Props {
		open: boolean;
		profile: Pick<UserInfo, 'name' | 'avatar_url'>;
	}

	let { open = $bindable(), profile }: Props = $props();

	const saving = new Mutation({ success: () => 'Name saved', done: () => (open = false) });
</script>

<!-- The picture changes as soon as one is picked or removed; the name with Save. -->
<Dialog.Root bind:open onOpenChangeComplete={(o) => o || saving.reset()}>
	<Dialog.Content class="sm:max-w-md">
		<Dialog.Header>
			<Dialog.Title>Change profile</Dialog.Title>
			<Dialog.Description>What others see on your runs and in member lists.</Dialog.Description>
		</Dialog.Header>
		<AvatarField kind="user" name={profile.name} src={profile.avatar_url} />
		<form method="POST" action="?/profile" class="grid gap-4" use:enhance={saving.enhance}>
			<div class="grid gap-2">
				<Label for="profile-name">Name</Label>
				<Input
					id="profile-name"
					name="name"
					value={profile.name}
					autocomplete="name"
					maxlength={128}
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
					{saving.pending ? 'Saving…' : 'Save'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
