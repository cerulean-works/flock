<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { SETTINGS_HEADING } from '#lib/settings.ts';
	import type { UserInfo } from '#lib/types.ts';
	import { formText } from '#lib/utils.ts';
	import AvatarField from './AvatarField.svelte';
	import FormError from './FormError.svelte';
	import PasswordDialog from './PasswordDialog.svelte';
	import SavedTick, { AutoSave } from './SavedTick.svelte';

	let { profile }: { profile: UserInfo } = $props();

	// The name saves on Enter or when the field is left (G4); the picture as soon as it's picked.
	const nameSave = new AutoSave();
	const naming = new Mutation({
		validate: (form) => (formText(form, 'name') ? null : 'Enter your name'),
		done: nameSave.saved
	});
	let name = $derived(profile.name);
	const renamed = $derived(name.trim() !== profile.name);
	let form = $state<HTMLFormElement>();
	let changingPassword = $state(false);
</script>

<!-- The account's Profile section: the picture, the name, the email and the password. -->
<section>
	<div>
		<AvatarField kind="user" name={profile.name} src={profile.avatar_url} />
	</div>
	<form
		method="POST"
		action="?/profile"
		class="mt-8 grid gap-2"
		bind:this={form}
		use:enhance={nameSave.only(() => renamed, naming.enhance)}
	>
		<Label for="profile-name">Name</Label>
		<div class="relative">
			<Input
				id="profile-name"
				name="name"
				bind:value={name}
				autocomplete="name"
				maxlength={128}
				class="pr-20"
				aria-invalid={!!naming.error}
				onblur={() => nameSave.save(form, renamed)}
				onkeydown={(e) => {
					// Escape puts back what's saved, as Linear does; leaving then sends nothing.
					if (e.key === 'Escape') {
						name = profile.name;
						e.currentTarget.blur();
					}
				}}
			/>
			<SavedTick saving={nameSave} pending={naming.pending} overlay />
		</div>
		<FormError error={naming.error} />
	</form>
	<div class="mt-8 grid gap-2">
		<Label for="profile-email">Email</Label>
		<Input id="profile-email" value={profile.email} readonly />
	</div>
</section>

<section class="mt-12">
	<h2 class={SETTINGS_HEADING}>Password</h2>
	<div class="mt-4">
		<Button variant="outline" onclick={() => (changingPassword = true)}>
			{profile.has_password ? 'Change password' : 'Set password'}
		</Button>
	</div>
</section>

<PasswordDialog bind:open={changingPassword} {profile} />
