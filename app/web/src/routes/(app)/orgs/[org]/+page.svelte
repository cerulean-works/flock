<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import ClockIcon from '@lucide/svelte/icons/clock';
	import HistoryIcon from '@lucide/svelte/icons/history';
	import MailIcon from '@lucide/svelte/icons/mail';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import UserPlusIcon from '@lucide/svelte/icons/user-plus';
	import { enhance } from '$app/forms';
	import Avatar from '#lib/components/Avatar.svelte';
	import AvatarField from '#lib/components/AvatarField.svelte';
	import BillingForm from '#lib/components/BillingForm.svelte';
	import BudgetWarnings from '#lib/components/BudgetWarnings.svelte';
	import CopyBlock from '#lib/components/CopyBlock.svelte';
	import CopyButton from '#lib/components/CopyButton.svelte';
	import DeleteOrgDialog from '#lib/components/DeleteOrgDialog.svelte';
	import DeletedProjectsSpend from '#lib/components/DeletedProjectsSpend.svelte';
	import FormError from '#lib/components/FormError.svelte';
	import MemberMenu from '#lib/components/MemberMenu.svelte';
	import MetaItem from '#lib/components/MetaItem.svelte';
	import MetaRow from '#lib/components/MetaRow.svelte';
	import OrgImport from '#lib/components/OrgImport.svelte';
	import S3Lifetimes from '#lib/components/S3Lifetimes.svelte';
	import SavedTick, { AutoSave } from '#lib/components/SavedTick.svelte';
	import SettingsPage from '#lib/components/SettingsPage.svelte';
	import SettingsSection from '#lib/components/SettingsSection.svelte';
	import DeletedDatasets from '#lib/components/datasets/DeletedDatasets.svelte';
	import AssistantSettings from '#lib/components/groups/AssistantSettings.svelte';
	import DeletedModels from '#lib/components/models/DeletedModels.svelte';
	import ModelRetention from '#lib/components/models/ModelRetention.svelte';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { SETTINGS_SUBHEADING } from '#lib/settings.ts';
	import type { Role } from '#lib/types.ts';
	import { dateTime, duration, formText, orgDisplayNameError, relativeTime } from '#lib/utils.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const admin = $derived(data.org.role === 'admin');
	const ROLES: Record<Role, string> = { admin: 'Admin', member: 'Member' };
	/** The sections this viewer sees, in the sidebar's order; the page shows those alone. */
	const has = (slug: string) => data.sections.some((s) => s.slug === slug);
	const label = (slug: string) => data.sections.find((s) => s.slug === slug)?.label ?? slug;

	// Invitations: the link is shown once, then the form is back for the next person.
	type Created = { url: string; email: string; role: Role; expires_at: number };
	let created = $state<Created | null>(null);
	let role = $state<Role>('member');
	const inviting = new Mutation<{ invite: Created }>({
		validate: (form) => (formText(form, 'email') ? null : 'Enter their email'),
		done: (data) => (created = data.invite)
	});
	const revoking = new Mutation({ toastErrors: true, success: () => 'Invitation withdrawn' });

	// Its display name is free to change; its ID (data.org.name) never does. It saves on Enter
	// or when the field is left (G4).
	const nameSave = new AutoSave();
	const naming = new Mutation({
		validate: (form) => orgDisplayNameError(formText(form, 'display_name')),
		done: nameSave.saved
	});
	let displayName = $derived(data.org.display_name);
	const renamed = $derived(displayName.trim() !== data.org.display_name);
	let nameForm = $state<HTMLFormElement>();
	let deleting = $state(false);
</script>

<svelte:head>
	<title>Settings · {data.org.display_name}</title>
</svelte:head>

<SettingsPage title="Settings" org>
	{#if has('general')}
		<SettingsSection id="general" title={label('general')}>
			<section>
				<div>
					<AvatarField kind="org" name={data.org.display_name} src={data.org.avatar_url} />
				</div>
				<form
					method="POST"
					action="?/displayName"
					class="mt-8 grid gap-2"
					bind:this={nameForm}
					use:enhance={nameSave.only(() => renamed, naming.enhance)}
				>
					<Label for="org-display-name">Display name</Label>
					<div class="relative">
						<Input
							id="org-display-name"
							name="display_name"
							bind:value={displayName}
							autocomplete="off"
							maxlength={128}
							class="pr-20"
							aria-invalid={!!naming.error}
							onblur={() => nameSave.save(nameForm, renamed)}
							onkeydown={(e) => {
								// Escape puts back what's saved, as Linear does; leaving then sends nothing.
								if (e.key === 'Escape') {
									displayName = data.org.display_name;
									e.currentTarget.blur();
								}
							}}
						/>
						<SavedTick saving={nameSave} pending={naming.pending} overlay />
					</div>
					<FormError error={naming.error} />
				</form>

				<div class="mt-8 grid gap-2">
					<Label for="org-id">ID</Label>
					<!-- The copy button sits in the field's end, so the field keeps the column's width. -->
					<div class="relative">
						<Input id="org-id" value={data.org.name} readonly class="pr-10 font-mono text-[13px]" />
						<CopyButton
							text={data.org.name}
							label="Copy ID"
							class="absolute top-1/2 right-1 size-7 -translate-y-1/2"
						/>
					</div>
					<p class="text-[13px] text-muted-foreground">
						Used in links and by the wandb SDK, as in <code class="font-mono text-[12.5px]"
							>wandb.init(entity="{data.org.name}")</code
						>. It can’t be changed.
					</p>
				</div>
			</section>

			<section class="mt-12">
				<h3 class={SETTINGS_SUBHEADING}>Delete organization</h3>
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
		</SettingsSection>
	{/if}
	{#if has('members')}
		<SettingsSection id="members" title={label('members')} count={data.members.length}>
			<section>
				<ul class="border-t border-divider">
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
				<section class="mt-12">
					<h3 class={SETTINGS_SUBHEADING}>Invite people</h3>
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
								<!-- A member row's columns: the mark, who, the role, the row's action. -->
								<li class="flex min-h-14 items-center gap-3 border-b border-divider py-2">
									<span
										class="grid size-7 flex-none place-items-center rounded-full border border-dashed border-border text-muted-foreground"
										aria-hidden="true"><MailIcon class="size-3.5" /></span
									>
									<div class="min-w-0 flex-1">
										<p class="truncate font-medium">{invite.email}</p>
										<MetaRow class="flex-nowrap">
											<MetaItem icon={HistoryIcon} title="Invited {dateTime(invite.created_at)}">
												{relativeTime(invite.created_at, data.now)}
											</MetaItem>
											<MetaItem icon={ClockIcon} title="Expires {dateTime(invite.expires_at)}">
												{duration(invite.expires_at - data.now)} left
											</MetaItem>
										</MetaRow>
									</div>
									<Badge variant="secondary" class="text-muted-foreground">
										{ROLES[invite.role]}
									</Badge>
									<form
										method="POST"
										action="?/revoke"
										class="flex w-8 flex-none justify-end"
										use:enhance={revoking.enhance}
									>
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
		</SettingsSection>
	{/if}
	{#if has('budget')}
		<SettingsSection id="budget" title={label('budget')}>
			<section>
				<BillingForm billing={data.billing} {admin} />
			</section>

			{#if admin && data.warnings}
				<section class="mt-12 scroll-mt-8" id="budget-warnings">
					<h3 class={SETTINGS_SUBHEADING}>Budget warnings</h3>
					<BudgetWarnings org={data.org.name} warnings={data.warnings} />
				</section>
			{/if}

			{#if admin && data.deletedProjects}
				<section class="mt-12 scroll-mt-8" id="deleted-projects">
					<h3 class={SETTINGS_SUBHEADING}>Spend of deleted projects</h3>
					<DeletedProjectsSpend projects={data.deletedProjects} currency={data.billing.currency} />
				</section>
			{/if}
		</SettingsSection>
	{/if}
	{#if has('storage')}
		<SettingsSection id="storage" title={label('storage')}>
			<section class="scroll-mt-8" id="s3-keys">
				<h3 class={SETTINGS_SUBHEADING}>S3 key lifetimes</h3>
				<S3Lifetimes org={data.org} />
			</section>
			<section class="mt-12 scroll-mt-8" id="model-retention">
				<h3 class={SETTINGS_SUBHEADING}>Model retention</h3>
				<ModelRetention org={data.org} />
			</section>
		</SettingsSection>
	{/if}
	{#if has('assistant')}
		<SettingsSection id="assistant" title={label('assistant')}>
			<section>
				<AssistantSettings org={data.org} />
			</section>
		</SettingsSection>
	{/if}
	{#if has('deleted')}
		<SettingsSection id="deleted" title={label('deleted')}>
			{#if data.deletedDatasets}
				<section class="scroll-mt-8" id="deleted-datasets">
					<h3 class={SETTINGS_SUBHEADING}>Deleted datasets</h3>
					<DeletedDatasets org={data.org.name} entries={data.deletedDatasets} now={data.now} />
				</section>
			{/if}
			{#if data.deletedModels}
				<section class={['scroll-mt-8', data.deletedDatasets && 'mt-12']} id="deleted-models">
					<h3 class={SETTINGS_SUBHEADING}>Deleted models</h3>
					<DeletedModels org={data.org.name} entries={data.deletedModels} now={data.now} />
				</section>
			{/if}
		</SettingsSection>
	{/if}
	{#if has('import')}
		<SettingsSection id="import" title={label('import')}>
			<section>
				<OrgImport
					org={data.org.name}
					label={data.org.display_name}
					job={data.import}
					superadmin={data.me.is_superadmin}
					now={data.now}
				/>
			</section>
		</SettingsSection>
	{/if}
</SettingsPage>

{#if admin}<DeleteOrgDialog bind:open={deleting} org={data.org} />{/if}
