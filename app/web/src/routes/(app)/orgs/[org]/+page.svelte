<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import ClockIcon from '@lucide/svelte/icons/clock';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import HistoryIcon from '@lucide/svelte/icons/history';
	import ShieldIcon from '@lucide/svelte/icons/shield';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import UserIcon from '@lucide/svelte/icons/user';
	import UserPlusIcon from '@lucide/svelte/icons/user-plus';
	import UsersIcon from '@lucide/svelte/icons/users';
	import { enhance } from '$app/forms';
	import Avatar from '#lib/components/Avatar.svelte';
	import AvatarField from '#lib/components/AvatarField.svelte';
	import OrgMark from '#lib/components/shell/OrgMark.svelte';
	import BillingForm from '#lib/components/BillingForm.svelte';
	import CopyBlock from '#lib/components/CopyBlock.svelte';
	import CopyButton from '#lib/components/CopyButton.svelte';
	import DeleteOrgDialog from '#lib/components/DeleteOrgDialog.svelte';
	import BudgetWarnings from '#lib/components/BudgetWarnings.svelte';
	import DeletedDatasets from '#lib/components/datasets/DeletedDatasets.svelte';
	import DeletedModels from '#lib/components/models/DeletedModels.svelte';
	import ModelRetention from '#lib/components/models/ModelRetention.svelte';
	import AssistantSettings from '#lib/components/groups/AssistantSettings.svelte';
	import DeletedProjectsSpend from '#lib/components/DeletedProjectsSpend.svelte';
	import FormError from '#lib/components/FormError.svelte';
	import MemberMenu from '#lib/components/MemberMenu.svelte';
	import MetaItem from '#lib/components/MetaItem.svelte';
	import MetaRow from '#lib/components/MetaRow.svelte';
	import OrgImport, { importShown } from '#lib/components/OrgImport.svelte';
	import S3Lifetimes from '#lib/components/S3Lifetimes.svelte';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { Role } from '#lib/types.ts';
	import {
		count,
		dateTime,
		duration,
		formText,
		orgDisplayNameError,
		relativeTime
	} from '#lib/utils.ts';
	import { entityHref } from '#lib/workspace/url.ts';
	import Page from '#lib/components/Page.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const admin = $derived(data.org.role === 'admin');
	const ROLES: Record<Role, string> = { admin: 'Admin', member: 'Member' };

	// Invitations: the link is shown once, then the form is back for the next person.
	type Created = { url: string; email: string; role: Role; expires_at: number };
	let created = $state<Created | null>(null);
	let role = $state<Role>('member');
	const inviting = new Mutation<{ invite: Created }>({
		validate: (form) => (formText(form, 'email') ? null : 'Enter their email'),
		done: (data) => (created = data.invite)
	});
	const revoking = new Mutation({ toastErrors: true, success: () => 'Invitation withdrawn' });

	// Its display name is free to change; its ID (data.org.name) never does.
	const naming = new Mutation({
		validate: (form) => orgDisplayNameError(formText(form, 'display_name')),
		success: () => 'Display name saved'
	});
	let displayName = $derived(data.org.display_name);
	let deleting = $state(false);
	const isMember = $derived(data.members.some((m) => m.id === data.me.id));
</script>

<svelte:head>
	<title>Settings · {data.org.display_name}</title>
</svelte:head>

<Page width="home">
	<!-- One column for every section, so text, forms and lists share an edge. -->
	<div class="mx-auto max-w-3xl">
		<h1 class="flex items-center gap-3 pt-8 text-[34px] leading-[1.15] tracking-[-0.025em]">
			<OrgMark name={data.org.display_name} src={data.org.avatar_url} class="size-10 text-[20px]" />
			<span class="min-w-0 truncate">{data.org.display_name}</span>
		</h1>
		<MetaRow class="mt-3 gap-x-5 text-[14px]">
			<MetaItem icon={UsersIcon}>{count(data.org.member_count, 'member')}</MetaItem>
			<MetaItem icon={FolderIcon} href={entityHref(data.org.name)}>
				{count(data.org.project_count, 'project')}
			</MetaItem>
			<MetaItem icon={admin ? ShieldIcon : UserIcon}>
				{#if isMember}You’re {admin ? 'an admin' : 'a member'}{:else}You’re a super admin{/if}
			</MetaItem>
		</MetaRow>

		{#if admin}
			<section class="mt-10">
				<h2 class="text-2xl tracking-[-0.01em]">Organization</h2>
				<div class="mt-4">
					<AvatarField kind="org" name={data.org.display_name} src={data.org.avatar_url} />
				</div>
				<form
					method="POST"
					action="?/displayName"
					class="mt-8 grid gap-2"
					use:enhance={naming.enhance}
				>
					<Label for="org-display-name">Display name</Label>
					<div class="flex gap-2">
						<Input
							id="org-display-name"
							name="display_name"
							bind:value={displayName}
							autocomplete="off"
							maxlength={128}
							aria-invalid={!!naming.error}
						/>
						<Button
							type="submit"
							variant="outline"
							disabled={displayName.trim() === data.org.display_name || naming.pending}
							pending={naming.pending}
						>
							Save
						</Button>
					</div>
					<FormError error={naming.error} />
					<p class="text-[13px] text-muted-foreground">
						Links and scripts use the ID, so changing this doesn’t break them.
					</p>
				</form>

				<div class="mt-8 grid gap-2">
					<Label for="org-id">ID</Label>
					<div class="flex items-center gap-2">
						<Input
							id="org-id"
							value={data.org.name}
							readonly
							class="bg-muted font-mono text-[13px] text-muted-foreground focus-visible:text-foreground"
						/>
						<CopyButton text={data.org.name} label="Copy ID" />
					</div>
					<p class="text-[13px] text-muted-foreground">
						Used in links and by the wandb SDK, as in <code class="font-mono text-[12.5px]"
							>wandb.init(entity="{data.org.name}")</code
						>. It can’t be changed.
					</p>
				</div>
			</section>
		{/if}

		<section class="mt-10">
			<h2 class="text-2xl tracking-[-0.01em]">Members</h2>
			<ul class="mt-4 border-t border-divider">
				{#each data.members as member (member.id)}
					{@const self = member.id === data.me.id}
					<li class="flex min-h-14 items-center gap-3 border-b border-divider py-2">
						<Avatar name={member.name} src={member.avatar_url} />
						<div class="min-w-0 flex-1">
							<p class="truncate font-medium">
								{member.name}{#if self}<span
										class="ml-2 text-[13px] font-normal text-muted-foreground">(you)</span
									>{/if}
							</p>
							<p class="truncate text-[13px] text-muted-foreground">{member.email}</p>
						</div>
						<Badge
							variant="secondary"
							class={member.role === 'admin' ? 'text-foreground' : 'text-muted-foreground'}
						>
							{ROLES[member.role]}
						</Badge>
						<span class="flex w-8 flex-none justify-end">
							<MemberMenu org={data.org.display_name} {member} {self} {admin} />
						</span>
					</li>
				{:else}
					<li class="border-b border-divider py-4 text-muted-foreground">No members yet.</li>
				{/each}
			</ul>
		</section>

		{#if admin && data.invites}
			<section class="mt-10">
				<h2 class="text-2xl tracking-[-0.01em]">Invite people</h2>
				<p class="mt-2 text-muted-foreground">
					Send the invite link to them yourself. It works once, only for that address.
				</p>

				{#if created}
					<div class="mt-4 grid gap-3 rounded-lg border border-divider p-4">
						<p>
							Invite link for <span class="font-medium">{created.email}</span> to join as
							{created.role === 'admin' ? 'an admin' : 'a member'}
						</p>
						<CopyBlock code={created.url} />
						<p class="text-[13px] text-muted-foreground">
							This link won’t be shown again. It expires {dateTime(created.expires_at)}.
						</p>
						<div>
							<Button variant="outline" onclick={() => (created = null)}>
								<UserPlusIcon /> Invite someone else
							</Button>
						</div>
					</div>
				{:else}
					<form
						method="POST"
						action="?/invite"
						class="mt-4 grid gap-2"
						use:enhance={inviting.enhance}
					>
						<div class="flex flex-wrap items-end gap-2">
							<div class="grid min-w-56 flex-1 gap-2">
								<Label for="invite-email">Email</Label>
								<Input
									id="invite-email"
									name="email"
									type="email"
									placeholder="e.g., name@example.com"
									autocomplete="off"
									required
								/>
							</div>
							<div class="grid gap-2">
								<Label for="invite-role">Role</Label>
								<input type="hidden" name="role" value={role} />
								<Select.Root type="single" bind:value={role}>
									<Select.Trigger id="invite-role" class="w-32">{ROLES[role]}</Select.Trigger>
									<Select.Content>
										<Select.Item value="member" label="Member">Member</Select.Item>
										<Select.Item value="admin" label="Admin">Admin</Select.Item>
									</Select.Content>
								</Select.Root>
							</div>
							<Button type="submit" pending={inviting.pending}>
								<UserPlusIcon />
								Create invite link
							</Button>
						</div>
						<FormError error={inviting.error} />
					</form>
				{/if}

				{#if data.invites.length}
					<h3 class="mt-8 text-[13px] font-medium text-muted-foreground">Pending</h3>
					<ul class="mt-2 border-t border-divider">
						{#each data.invites as invite (invite.id)}
							<li class="flex h-12 items-center gap-3 border-b border-divider">
								<span class="min-w-0 flex-1 truncate">{invite.email}</span>
								<Badge variant="secondary" class="text-muted-foreground">
									{ROLES[invite.role]}
								</Badge>
								<MetaRow class="hidden flex-none flex-nowrap sm:flex">
									<MetaItem icon={HistoryIcon} title="Invited {dateTime(invite.created_at)}">
										{relativeTime(invite.created_at, data.now)}
									</MetaItem>
									<MetaItem icon={ClockIcon} title="Expires {dateTime(invite.expires_at)}">
										{duration(invite.expires_at - data.now)} left
									</MetaItem>
								</MetaRow>
								<form method="POST" action="?/revoke" use:enhance={revoking.enhance}>
									<input type="hidden" name="invite" value={invite.id} />
									<Button
										type="submit"
										variant="ghost"
										size="icon-sm"
										class="text-muted-foreground hover:text-destructive"
										aria-label="Withdraw invite for {invite.email}"
										{@attach hint('Withdraw invite')}
									>
										<Trash2Icon />
									</Button>
								</form>
							</li>
						{/each}
					</ul>
				{/if}
			</section>
		{/if}

		<section class="mt-10 scroll-mt-6" id="budget">
			<h2 class="text-2xl tracking-[-0.01em]">Budget</h2>
			<BillingForm billing={data.billing} {admin} />
		</section>

		{#if admin && data.warnings}
			<section class="mt-10 scroll-mt-6" id="budget-warnings">
				<h2 class="text-2xl tracking-[-0.01em]">Budget warnings</h2>
				<BudgetWarnings org={data.org.name} warnings={data.warnings} />
			</section>
		{/if}

		{#if admin && data.deletedProjects}
			<section class="mt-10 scroll-mt-6" id="deleted-projects">
				<h2 class="text-2xl tracking-[-0.01em]">Spend of deleted projects</h2>
				<DeletedProjectsSpend projects={data.deletedProjects} currency={data.billing.currency} />
			</section>
		{/if}

		{#if admin && data.s3}
			<section class="mt-10 scroll-mt-6" id="s3-keys">
				<h2 class="text-2xl tracking-[-0.01em]">S3 key lifetimes</h2>
				<S3Lifetimes org={data.org} />
			</section>
			<section class="mt-10 scroll-mt-6" id="model-retention">
				<h2 class="text-2xl tracking-[-0.01em]">Model retention</h2>
				<ModelRetention org={data.org} />
			</section>
		{/if}

		{#if admin}
			<section class="mt-10 scroll-mt-6" id="assistant">
				<h2 class="text-2xl tracking-[-0.01em]">Run groups assistant</h2>
				<AssistantSettings org={data.org} />
			</section>
		{/if}

		{#if admin && data.deletedDatasets}
			<section class="mt-10 scroll-mt-6" id="deleted-datasets">
				<h2 class="text-2xl tracking-[-0.01em]">Deleted datasets</h2>
				<DeletedDatasets org={data.org.name} entries={data.deletedDatasets} now={data.now} />
			</section>
		{/if}

		{#if admin && data.deletedModels}
			<section class="mt-10 scroll-mt-6" id="deleted-models">
				<h2 class="text-2xl tracking-[-0.01em]">Deleted models</h2>
				<DeletedModels org={data.org.name} entries={data.deletedModels} now={data.now} />
			</section>
		{/if}

		{#if data.me.is_superadmin || importShown(data.import, data.now)}
			<section class="mt-10 scroll-mt-6" id="import">
				<h2 class="text-2xl tracking-[-0.01em]">Import from W&B</h2>
				<OrgImport
					org={data.org.name}
					label={data.org.display_name}
					job={data.import}
					superadmin={data.me.is_superadmin}
					now={data.now}
				/>
			</section>
		{/if}

		{#if admin}
			<section class="mt-10">
				<h2 class="text-2xl tracking-[-0.01em]">Delete organization</h2>
				<p class="mt-2 text-muted-foreground">
					Deletes its projects and their runs. Members keep their accounts.
				</p>
				<div class="mt-4">
					<Button
						variant="outline"
						class="text-destructive hover:text-destructive"
						onclick={() => (deleting = true)}
					>
						<Trash2Icon /> Delete organization…
					</Button>
				</div>
			</section>
		{/if}
	</div>
</Page>

{#if admin}<DeleteOrgDialog bind:open={deleting} org={data.org} />{/if}
