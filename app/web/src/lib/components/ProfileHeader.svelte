<script lang="ts">
	import Avatar from '#lib/components/Avatar.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import type { UserInfo } from '#lib/types.ts';
	import PasswordDialog from './PasswordDialog.svelte';
	import ProfileDialog from './ProfileDialog.svelte';

	let { profile }: { profile: UserInfo } = $props();

	let editing = $state(false);
	let changingPassword = $state(false);
</script>

<!-- The account page's header: the picture, then who this is and the two ways to change it. -->
<header class="flex items-center gap-5 pt-8">
	<Avatar name={profile.name} src={profile.avatar_url} class="size-24 text-[32px]" />
	<div class="grid min-w-0 gap-1">
		<h1 class="truncate text-[34px] leading-[1.15] tracking-[-0.025em]">{profile.name}</h1>
		<p class="truncate text-muted-foreground">
			{profile.email}
			{#if profile.is_superadmin}<span>· Super admin</span>{/if}
		</p>
		<div class="mt-2 flex flex-wrap gap-2">
			<Button variant="outline" onclick={() => (editing = true)}>Change profile</Button>
			<Button variant="outline" onclick={() => (changingPassword = true)}>
				{profile.has_password ? 'Change password' : 'Set password'}
			</Button>
		</div>
	</div>
</header>

<ProfileDialog bind:open={editing} {profile} />
<PasswordDialog bind:open={changingPassword} {profile} />
