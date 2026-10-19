<script lang="ts">
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import KeyRoundIcon from '@lucide/svelte/icons/key-round';
	import LogOutIcon from '@lucide/svelte/icons/log-out';
	import UserMinusIcon from '@lucide/svelte/icons/user-minus';
	import UserCogIcon from '@lucide/svelte/icons/user-cog';
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { Member } from '#lib/types.ts';
	import ConfirmDialog from './ConfirmDialog.svelte';
	import ResetLinkDialog from './ResetLinkDialog.svelte';

	interface Props {
		org: string;
		member: Member;
		/** The member is the signed-in user. */
		self: boolean;
		/** The signed-in user administers the org. */
		admin: boolean;
	}

	let { org, member, self, admin }: Props = $props();

	const role = $derived(member.role === 'admin' ? 'member' : 'admin');
	// The role asked for: by the time the toast shows, the reloaded member already has it.
	let asked = 'member';
	const changing = new Mutation({
		toastErrors: true,
		submit: (form) => (asked = String(form.get('role'))),
		success: () => {
			const as = asked === 'admin' ? 'an admin' : 'a member';
			return self ? `You’re now ${as} of ${org}` : `${member.name} is now ${as}`;
		}
	});
	let roleForm = $state<HTMLFormElement>();

	let resetting = $state(false);
	let removing = $state(false);
</script>

<!-- Members can only leave; admins change roles, reset passwords and remove members. -->
{#if admin || self}
	<DropdownMenu.Root>
		<DropdownMenu.Trigger>
			{#snippet child({ props })}
				<Button
					{...props}
					variant="ghost"
					size="icon-sm"
					aria-label="{member.name} actions"
					disabled={changing.pending}
				>
					<EllipsisIcon />
				</Button>
			{/snippet}
		</DropdownMenu.Trigger>
		<DropdownMenu.Content align="end" class="w-56">
			{#if admin}
				<DropdownMenu.Item onSelect={() => roleForm?.requestSubmit()}>
					<UserCogIcon />
					{#if self}
						{role === 'admin' ? 'Make me an admin' : 'Step down to member'}
					{:else}
						{role === 'admin' ? 'Make admin' : 'Make member'}
					{/if}
				</DropdownMenu.Item>
				{#if !self}
					<DropdownMenu.Item onSelect={() => (resetting = true)}>
						<KeyRoundIcon /> Password reset link…
					</DropdownMenu.Item>
				{/if}
				<DropdownMenu.Separator />
			{/if}
			{#if self}
				<DropdownMenu.Item variant="destructive" onSelect={() => (removing = true)}>
					<LogOutIcon /> Leave {org}…
				</DropdownMenu.Item>
			{:else}
				<DropdownMenu.Item variant="destructive" onSelect={() => (removing = true)}>
					<UserMinusIcon /> Remove from {org}…
				</DropdownMenu.Item>
			{/if}
		</DropdownMenu.Content>
	</DropdownMenu.Root>

	<form method="POST" action="?/role" bind:this={roleForm} use:enhance={changing.enhance} hidden>
		<input type="hidden" name="user" value={member.id} />
		<input type="hidden" name="role" value={role} />
	</form>
	<ResetLinkDialog bind:open={resetting} user={member} action="?/reset" />
	<ConfirmDialog
		bind:open={removing}
		action="?/remove"
		fields={{ user: member.id }}
		title={self ? `Leave ${org}?` : `Remove ${member.name} from ${org}?`}
		description={self
			? `You lose access to its projects; your runs stay. Rejoining needs a new invite.`
			: `They lose access to its projects; their runs stay.`}
		confirm={self ? 'Leave' : 'Remove'}
		pending={self ? 'Leaving…' : 'Removing…'}
		success={self ? `You left ${org}` : `Removed ${member.name}`}
	/>
{/if}
