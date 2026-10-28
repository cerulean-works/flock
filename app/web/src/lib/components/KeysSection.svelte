<script lang="ts">
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Tabs from '#lib/components/ui/tabs/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { groupKeys } from '#lib/s3.ts';
	import type { KeysTab, S3Config, S3Key, WandbKey } from '#lib/types.ts';
	import { count, dateTime, duration, relativeTime } from '#lib/utils.ts';
	import { withParam } from '#lib/workspace/url.ts';
	import S3KeyCreator from './S3KeyCreator.svelte';
	import WandbKeyCreator from './WandbKeyCreator.svelte';

	interface Props {
		/** The user's wandb keys. */
		keys: WandbKey[];
		/** The user's S3 keys and the orgs they can be for; null when the server has no S3. */
		s3:
			| (S3Config & {
					keys: S3Key[];
					orgs: { name: string; display_name: string; s3_key_max_ttl: number | null }[];
			  })
			| null;
		/** The org a new S3 key is for at first. */
		defaultOrg: string | null;
		/** Seconds since the epoch, for "used 3 days ago". */
		now: number;
	}

	let { keys, s3, defaultOrg, now }: Props = $props();

	const TABS: { value: KeysTab; label: string }[] = [
		{ value: 'wandb', label: 'wandb' },
		{ value: 's3', label: 'S3' }
	];
	// In the URL (`?keys=s3`), so a tab can be linked to; switching never re-runs the load.
	let tabOverride = $state<KeysTab | null>(null);
	const urlTab = $derived<KeysTab>(
		(s3 && TABS.find((t) => t.value === page.url.searchParams.get('keys'))?.value) || 'wandb'
	);
	const tab = $derived(tabOverride ?? urlTab);

	function setTab(t: KeysTab) {
		if (t === tab) return;
		tabOverride = t;
		void goto(withParam(page.url, 'keys', t === 'wandb' ? null : t), {
			replace: true,
			reset: false
		}).finally(() => (tabOverride = null));
	}

	const removal = new Mutation({ success: () => 'wandb key deleted', toastErrors: true });
	const s3Revocation = new Mutation<{ revokedS3: number }>({
		success: ({ revokedS3 }) =>
			revokedS3 === 1 ? 'S3 key revoked' : `${revokedS3} S3 keys revoked`,
		toastErrors: true
	});
	const orgName = (name: string | null) =>
		s3?.orgs.find((o) => o.name === name)?.display_name ?? name ?? 'a deleted organization';
</script>

{#snippet revoke(ids: number[], label: string)}
	<form method="POST" action="?/deleteS3Keys" use:enhance={s3Revocation.enhance}>
		{#each ids as id (id)}<input type="hidden" name="id" value={id} />{/each}
		<Button
			type="submit"
			variant="ghost"
			size="icon-sm"
			class="text-muted-foreground hover:text-destructive"
			aria-label={label}
			title="Revoke this key"
		>
			<Trash2Icon />
		</Button>
	</form>
{/snippet}

{#snippet wandbKeys()}
	<p class="max-w-[62ch] text-muted-foreground">Use these keys with the wandb SDK.</p>
	{#if keys.length}
		<ul class="mt-4 divide-y divide-divider border-t border-divider">
			{#each keys as key (key.id)}
				<li class="flex h-12 items-center gap-4">
					<span class="min-w-0 flex-1 truncate font-medium">{key.name}</span>
					<span class="font-mono text-[12.5px] text-muted-foreground">{key.prefix}…</span>
					<span class="w-24 text-muted-foreground" title={dateTime(key.created_at)}>
						{relativeTime(key.created_at, now)}
					</span>
					<form method="POST" action="?/deleteKey" use:enhance={removal.enhance}>
						<input type="hidden" name="id" value={key.id} />
						<Button
							type="submit"
							variant="ghost"
							size="icon-sm"
							class="text-muted-foreground hover:text-destructive"
							aria-label="Delete key {key.name}"
							title="Delete this key"
						>
							<Trash2Icon />
						</Button>
					</form>
				</li>
			{/each}
		</ul>
	{/if}
	<div class="mt-4">
		<WandbKeyCreator named />
	</div>
{/snippet}

<section class="mt-10" id="keys">
	<h2 class="text-2xl tracking-[-0.01em]">Keys</h2>
	{#if s3}
		{@const groups = groupKeys(s3.keys)}
		<Tabs.Root value={tab} onValueChange={(v) => setTab(v as KeysTab)} class="mt-4 gap-4">
			<Tabs.List aria-label="Keys">
				{#each TABS as t (t.value)}
					<Tabs.Trigger value={t.value} class="px-3">{t.label}</Tabs.Trigger>
				{/each}
			</Tabs.List>
			<Tabs.Content value="wandb">
				{@render wandbKeys()}
			</Tabs.Content>
			<Tabs.Content value="s3">
				<p class="text-muted-foreground">
					Persistent S3 keys should only be used with jobs that can’t use ephemeral keys generated
					from <code class="font-mono text-[0.9em] whitespace-nowrap">flock s3 credentials</code>.
				</p>
				{#if s3.keys.length}
					<ul class="mt-4 border-t border-divider">
						{#each groups.longLived as key (key.id)}
							<li class="flex min-h-12 items-center gap-4 border-b border-divider py-2">
								<div class="min-w-0 flex-1">
									<p class="truncate font-medium">{key.name}</p>
									<p class="truncate text-[13px] text-muted-foreground">
										<span class="font-mono text-[12.5px]">{key.access_key_id}</span>
										· {orgName(key.org)}{key.read_only ? ' · read-only' : ''}
									</p>
								</div>
								<span class="hidden w-36 text-[13px] text-muted-foreground sm:block">
									{key.expires_at === null
										? 'Never expires'
										: `${duration(key.expires_at - now)} left`}
									<br />
									{key.last_used_at === null
										? 'Not used yet'
										: `Used ${relativeTime(key.last_used_at, now)}`}
								</span>
								{@render revoke([key.id], `Revoke S3 key ${key.name}`)}
							</li>
						{/each}
						{#each groups.ephemeral as group (group.id)}
							{@const used = Math.max(...group.keys.map((k) => k.last_used_at ?? -Infinity))}
							<li class="flex min-h-12 items-center gap-4 border-b border-divider py-2">
								<div class="min-w-0 flex-1">
									<p class="truncate">
										{count(group.keys.length, 'ephemeral key')} from
										<span class="font-medium">{group.source}</span>
									</p>
									<p class="truncate text-[13px] text-muted-foreground">
										{group.kind === 'machine'
											? 'Issued to a command line sign-in'
											: 'Issued to an API key'} ·
										{[...new Set(group.keys.map((k) => orgName(k.org)))].join(', ')}
									</p>
								</div>
								<span class="hidden w-36 text-[13px] text-muted-foreground sm:block">
									{Number.isFinite(used) ? `Used ${relativeTime(used, now)}` : 'Not used yet'}
								</span>
								{@render revoke(
									group.keys.map((k) => k.id),
									`Revoke the ephemeral keys from ${group.source}`
								)}
							</li>
						{/each}
					</ul>
				{/if}
				<div class="mt-4">
					{#if s3.orgs.length}
						<S3KeyCreator orgs={s3.orgs} initialOrg={defaultOrg} />
					{:else}
						<p class="text-muted-foreground">Join an organization to use its buckets.</p>
					{/if}
				</div>
			</Tabs.Content>
		</Tabs.Root>
	{:else}
		<!-- Without S3 on this server, wandb keys are the only kind. -->
		<div class="mt-4">{@render wandbKeys()}</div>
	{/if}
</section>
