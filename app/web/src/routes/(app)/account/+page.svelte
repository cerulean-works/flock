<script lang="ts">
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { enhance } from '$app/forms';
	import ApiKeyCreator from '#lib/components/ApiKeyCreator.svelte';
	import AvatarField from '#lib/components/AvatarField.svelte';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { dateTime, relativeTime } from '#lib/utils.ts';
	import { entityHref } from '#lib/workspace/url.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const removal = new Mutation({ success: () => 'API key deleted', toastErrors: true });
</script>

<svelte:head>
	<title>Account · Flock</title>
</svelte:head>

<div class="flex min-h-0 flex-1 flex-col">
	<main class="min-h-0 flex-1 overflow-y-auto px-5 pb-16">
		<div class="mx-auto max-w-180">
			<h1 class="pt-8 font-serif text-[34px] leading-[1.15] tracking-[-0.02em]">
				{data.profile.name}
			</h1>
			<p class="mt-1 text-muted-foreground">
				{data.profile.email}
				{#if data.profile.is_superadmin}<span>· Super admin</span>{/if}
			</p>

			<section class="mt-10">
				<h2 class="font-serif text-2xl tracking-[-0.01em]">Picture</h2>
				<div class="mt-4">
					<AvatarField
						kind="user"
						name={data.profile.name}
						src={data.profile.avatar_url}
						gravatar={data.profile.avatar_source !== 'gravatar'}
					/>
				</div>
			</section>

			<section class="mt-10">
				<h2 class="font-serif text-2xl tracking-[-0.01em]">Organizations</h2>
				{#if data.profile.orgs.length}
					<ul class="mt-3 border-t border-divider">
						{#each data.profile.orgs as org (org.name)}
							<li class="flex h-12 items-center gap-3 border-b border-divider">
								<a class="font-medium hover:text-link" href={entityHref(org.name)}>{org.name}</a>
								<Badge variant="secondary" class="text-muted-foreground">
									{org.role === 'admin' ? 'Admin' : 'Member'}
								</Badge>
								{#if org.name === data.profile.default_org}
									<span class="text-xs text-muted-foreground">
										default for <code class="font-mono">wandb.init</code>
									</span>
								{/if}
							</li>
						{/each}
					</ul>
				{:else}
					<p class="mt-3 text-muted-foreground">None yet.</p>
				{/if}
			</section>

			<section class="mt-10">
				<h2 class="font-serif text-2xl tracking-[-0.01em]">API keys</h2>
				<p class="mt-2 max-w-[62ch] text-muted-foreground">Keys work until deleted.</p>
				{#if data.keys.length}
					<ul class="mt-4 border-t border-divider">
						{#each data.keys as key (key.id)}
							<li class="flex h-12 items-center gap-4 border-b border-divider">
								<span class="min-w-0 flex-1 truncate font-medium">{key.name}</span>
								<span class="font-mono text-[12.5px] text-muted-foreground">{key.prefix}…</span>
								<span class="w-24 text-muted-foreground" title={dateTime(key.created_at)}>
									{relativeTime(key.created_at, data.now)}
								</span>
								<form method="POST" action="?/deleteKey" use:enhance={removal.enhance}>
									<input type="hidden" name="id" value={key.id} />
									<Button
										type="submit"
										variant="ghost"
										size="icon-sm"
										class="text-muted-foreground hover:text-destructive"
										aria-label="Delete key {key.name}"
										title="Delete: scripts using it stop working"
									>
										<Trash2Icon />
									</Button>
								</form>
							</li>
						{/each}
					</ul>
				{/if}
				<div class="mt-4">
					<ApiKeyCreator named />
				</div>
			</section>

			<section class="mt-10">
				<form method="POST" action="/logout">
					<Button type="submit" variant="outline">Sign out</Button>
				</form>
			</section>
		</div>
	</main>
</div>
