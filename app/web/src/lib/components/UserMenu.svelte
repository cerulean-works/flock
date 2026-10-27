<script lang="ts">
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import KeyRoundIcon from '@lucide/svelte/icons/key-round';
	import ShieldIcon from '@lucide/svelte/icons/shield';
	import ShieldOffIcon from '@lucide/svelte/icons/shield-off';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { UserInfo } from '#lib/types.ts';
	import ConfirmDialog from './ConfirmDialog.svelte';
	import ResetLinkDialog from './ResetLinkDialog.svelte';

	/** A super admin's actions on an account; `self` is the signed-in user's own. */
	let { user, self }: { user: UserInfo; self: boolean } = $props();

	const promoting = new Mutation({
		toastErrors: true,
		success: () =>
			user.is_superadmin
				? `${user.name} is now a super admin`
				: `${user.name} is no longer a super admin`
	});
	let promote = $state<HTMLFormElement>();
	let demotingSelf = $state(false);
	let resetting = $state(false);
	let deleting = $state(false);
</script>

<DropdownMenu.Root>
	<DropdownMenu.Trigger>
		{#snippet child({ props })}
			<Button
				{...props}
				variant="ghost"
				size="icon-sm"
				aria-label="{user.name} actions"
				disabled={promoting.pending}
			>
				<EllipsisIcon />
			</Button>
		{/snippet}
	</DropdownMenu.Trigger>
	<DropdownMenu.Content align="end" class="w-56">
		{#if !user.is_superadmin}
			<DropdownMenu.Item onSelect={() => promote?.requestSubmit()}>
				<ShieldIcon /> Make super admin
			</DropdownMenu.Item>
		{:else if self}
			<DropdownMenu.Item onSelect={() => (demotingSelf = true)}>
				<ShieldOffIcon /> Stop being a super admin…
			</DropdownMenu.Item>
		{:else}
			<DropdownMenu.Item onSelect={() => promote?.requestSubmit()}>
				<ShieldOffIcon /> Remove super admin
			</DropdownMenu.Item>
		{/if}
		{#if !self}
			<DropdownMenu.Item onSelect={() => (resetting = true)}>
				<KeyRoundIcon /> Password reset link…
			</DropdownMenu.Item>
			<DropdownMenu.Separator />
			<DropdownMenu.Item variant="destructive" onSelect={() => (deleting = true)}>
				<Trash2Icon /> Delete account…
			</DropdownMenu.Item>
		{/if}
	</DropdownMenu.Content>
</DropdownMenu.Root>

<form
	method="POST"
	action="?/superadmin"
	bind:this={promote}
	use:enhance={promoting.enhance}
	hidden
>
	<input type="hidden" name="user" value={user.id} />
	<input type="hidden" name="value" value={String(!user.is_superadmin)} />
</form>
{#if self}
	<ConfirmDialog
		bind:open={demotingSelf}
		action="?/superadmin"
		fields={{ user: user.id, value: 'false' }}
		title="Stop being a super admin?"
		description="You keep your organization roles but lose this page. Only another super admin can undo this."
		confirm="Stop being a super admin"
		pending="Changing…"
		success="You’re no longer a super admin"
	/>
{:else}
	<ResetLinkDialog bind:open={resetting} {user} action="?/reset" />
	<ConfirmDialog
		bind:open={deleting}
		action="?/deleteUser"
		fields={{ user: user.id }}
		title="Delete {user.name}’s account?"
		description="They can’t sign in and their wandb keys stop working. Their runs stay. There is no undo."
		confirm="Delete account"
		pending="Deleting…"
		success="Deleted {user.name}’s account"
	/>
{/if}
