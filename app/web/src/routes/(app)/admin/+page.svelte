<script lang="ts">
	import PlusIcon from '@lucide/svelte/icons/plus';
	import { enhance } from '$app/forms';
	import { page } from '$app/state';
	import Avatar from '#lib/components/Avatar.svelte';
	import OrgMark from '#lib/components/shell/OrgMark.svelte';
	import FormError from '#lib/components/FormError.svelte';
	import OrgIdentityFields from '#lib/components/OrgIdentityFields.svelte';
	import UserMenu from '#lib/components/UserMenu.svelte';
	import WandbImportFields from '#lib/components/WandbImportFields.svelte';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import {
		count,
		dateTime,
		formText,
		orgDisplayNameError,
		orgIdError,
		relativeTime
	} from '#lib/utils.ts';
	import { wandbImport } from '#lib/wandb.ts';
	import { entityHref, orgHref } from '#lib/workspace/url.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const me = $derived(page.data.shell?.me.id ?? page.data.user?.id);

	let creating = $state(false);
	let displayName = $state('');
	let orgId = $state('');
	let importing = $state(false);
	const creation = new Mutation({
		validate: (form) => {
			const name = formText(form, 'id');
			const wandb = wandbImport(form, name);
			return (
				orgDisplayNameError(formText(form, 'display_name')) ??
				orgIdError(name) ??
				(wandb && 'error' in wandb ? wandb.error : null)
			);
		},
		success: () => (importing ? 'Organization created, importing from W&B' : 'Organization created')
	});

	// One fixed action column, so the header and every row agree on where the others fall.
	const ORG_GRID =
		'grid grid-cols-[minmax(0,1fr)_80px] sm:grid-cols-[minmax(0,1fr)_88px_88px_96px_80px] items-center gap-x-4';
</script>

<svelte:head>
	<title>Super admin · Flock</title>
</svelte:head>

<div class="flex min-h-0 flex-1 flex-col">
	<main class="min-h-0 flex-1 overflow-y-auto px-5 pb-16">
		<div class="mx-auto max-w-180">
			<h1 class="pt-8 font-serif text-[34px] leading-[1.15] tracking-[-0.02em]">Super admin</h1>
			<p class="mt-1 text-muted-foreground">
				{count(data.orgs.length, 'organization')} · {count(data.users.length, 'account')}
			</p>

			<section class="mt-10">
				<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
					<h2 class="font-serif text-2xl tracking-[-0.01em]">Organizations</h2>
					<Button variant="outline" class="ml-auto" onclick={() => (creating = true)}>
						<PlusIcon /> New organization
					</Button>
				</div>
				<div class="mt-4 border-t border-divider">
					<div
						class={[
							ORG_GRID,
							'hidden h-10 border-b border-divider text-xs font-medium text-muted-foreground sm:grid'
						]}
					>
						<span>Name</span>
						<span class="text-right">Projects</span>
						<span class="text-right">Members</span>
						<span>Created</span>
						<span></span>
					</div>
					<ul>
						{#each data.orgs as org (org.name)}
							<li class={[ORG_GRID, 'min-h-12 border-b border-divider py-2']}>
								<a
									class="flex min-w-0 items-center gap-2.5 font-medium hover:text-link"
									href={entityHref(org.name)}
								>
									<OrgMark
										name={org.display_name}
										src={org.avatar_url}
										class="size-6 text-[13px]"
									/>
									<span class="truncate">{org.display_name}</span>
									{#if org.display_name !== org.name}
										<span class="truncate font-mono text-[12px] font-normal text-muted-foreground">
											{org.name}
										</span>
									{/if}
								</a>
								<span class="hidden text-right tabular-nums sm:block">
									{org.project_count.toLocaleString('en-US')}
								</span>
								<span class="hidden text-right tabular-nums sm:block">
									{org.member_count.toLocaleString('en-US')}
								</span>
								<span
									class="hidden text-muted-foreground sm:block"
									title={dateTime(org.created_at)}
								>
									{relativeTime(org.created_at, data.now)}
								</span>
								<Button variant="ghost" size="sm" class="justify-self-end" href={orgHref(org.name)}>
									Members
								</Button>
							</li>
						{:else}
							<li class="border-b border-divider py-4 text-muted-foreground">
								No organizations yet.
							</li>
						{/each}
					</ul>
				</div>
			</section>

			<section class="mt-10">
				<h2 class="font-serif text-2xl tracking-[-0.01em]">Accounts</h2>
				<ul class="mt-4 border-t border-divider">
					{#each data.users as user (user.id)}
						<li class="flex min-h-14 items-center gap-3 border-b border-divider py-2">
							<Avatar name={user.name} src={user.avatar_url} />
							<div class="min-w-0 flex-1">
								<p class="flex items-center gap-2">
									<span class="truncate font-medium">{user.name}</span>
									{#if user.id === me}<span class="text-muted-foreground">· you</span>{/if}
									{#if user.is_superadmin}
										<Badge variant="secondary" class="text-foreground">Super admin</Badge>
									{/if}
								</p>
								<p class="truncate text-[13px] text-muted-foreground">
									{user.email}{#if user.google}<span> · Google</span>{/if}
								</p>
							</div>
							<p
								class="hidden w-48 truncate text-[13px] text-muted-foreground md:block"
								title={user.orgs.map((o) => `${o.name} (${o.role})`).join(', ')}
							>
								<!-- Spaced with margins: Svelte trims whitespace at the edges of tags. -->
								{#each user.orgs as org, i (org.name)}
									<span class={[i > 0 && 'ml-1']}
										><a class="hover:text-link" href={orgHref(org.name)}>{org.name}</a
										>{#if org.role === 'admin'}<span class="ml-1 text-faint">(admin)</span
											>{/if}{#if i < user.orgs.length - 1},{/if}</span
									>
								{:else}
									<span class="text-faint">No organizations</span>
								{/each}
							</p>
							<span
								class="hidden w-20 text-[13px] text-muted-foreground sm:block"
								title="Joined {dateTime(user.created_at)}"
							>
								{relativeTime(user.created_at, data.now)}
							</span>
							<span class="flex w-8 flex-none justify-end">
								<UserMenu {user} self={user.id === me} />
							</span>
						</li>
					{/each}
				</ul>
			</section>
		</div>
	</main>
</div>

<Dialog.Root
	bind:open={creating}
	onOpenChange={(o) => {
		if (o) return;
		creation.reset();
		displayName = '';
		orgId = '';
		importing = false;
	}}
>
	<Dialog.Content class="sm:max-w-md">
		<Dialog.Header>
			<Dialog.Title>New organization</Dialog.Title>
			<Dialog.Description>You become its first admin.</Dialog.Description>
		</Dialog.Header>
		<form method="POST" action="?/createOrg" class="grid gap-4" use:enhance={creation.enhance}>
			<OrgIdentityFields
				prefix="new-org"
				bind:displayName
				bind:id={orgId}
				invalid={!!creation.error}
			/>
			<WandbImportFields id="new-org-wandb" org={orgId.trim()} bind:on={importing} />
			<FormError error={creation.error} />
			<Dialog.Footer>
				<Dialog.Close>
					{#snippet child({ props })}
						<Button {...props} variant="outline">Cancel</Button>
					{/snippet}
				</Dialog.Close>
				<Button type="submit" disabled={creation.pending}>
					{creation.pending ? 'Creating…' : 'Create organization'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
