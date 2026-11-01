<script lang="ts">
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import HardDriveIcon from '@lucide/svelte/icons/hard-drive';
	import HistoryIcon from '@lucide/svelte/icons/history';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import BucketHistory from '#lib/components/storage/BucketHistory.svelte';
	import ConnectPanel from '#lib/components/storage/ConnectPanel.svelte';
	import CreateBucket from '#lib/components/storage/CreateBucket.svelte';
	import DeleteBucketDialog from '#lib/components/storage/DeleteBucketDialog.svelte';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { datasetsHref } from '#lib/datasets.ts';
	import { objectHref } from '#lib/storage.ts';
	import { count, dateTime, relativeTime } from '#lib/utils.ts';
	import Page from '#lib/components/Page.svelte';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();

	const admin = $derived(data.shell.admin);
	const live = $derived(data.buckets.filter((b) => b.deleted_at === undefined));

	let history = $state<string | null>(null);
	let deleting = $state<string | null>(null);
</script>

<svelte:head>
	<title>Storage · {data.shell.orgName}</title>
</svelte:head>

<Page width="home">
	<h1 class="pt-8 text-[34px] leading-[1.15] tracking-[-0.025em]">Storage</h1>
	<p class="mt-1 max-w-[62ch] text-muted-foreground">
		S3 buckets shared by everyone in {data.shell.orgName}.
	</p>

	<section class="mt-8" aria-labelledby="buckets-title">
		<div class="flex items-baseline gap-3">
			<h2 id="buckets-title" class="text-2xl tracking-[-0.01em]">Buckets</h2>
			<span class="text-[13px] text-muted-foreground tabular-nums">
				{count(live.length, 'bucket')}
			</span>
		</div>
		<div class="mt-3"><CreateBucket nameLimit={data.nameLimit} /></div>

		{#if data.buckets.length}
			<table class="mt-5 w-full table-fixed border-collapse text-[13.5px]">
				<thead>
					<tr class="h-10 border-b text-left text-xs font-medium text-muted-foreground">
						<th class="px-2 font-medium">Name</th>
						<th class="hidden w-44 px-2 font-medium sm:table-cell">Created</th>
						<th class="hidden w-44 px-2 font-medium md:table-cell">By</th>
						<th class="w-10"><span class="sr-only">Actions</span></th>
					</tr>
				</thead>
				<tbody>
					{#each data.buckets as bucket (`${bucket.name}:${bucket.deleted_at ?? ''}`)}
						{@const gone = bucket.deleted_at !== undefined}
						<tr
							class={[
								'group/row h-10 border-b border-divider',
								gone ? 'text-faint' : 'hover:bg-muted'
							]}
						>
							<td class="truncate px-2">
								{#if gone}
									<span
										class="flex items-center gap-2"
										title="This bucket is being emptied and removed. Its name can be used again once that’s done."
									>
										<HardDriveIcon class="size-4 flex-none" />
										<span class="truncate font-mono text-[13px]">{bucket.name}</span>
										<span class="flex-none text-[12px]">being deleted</span>
									</span>
								{:else}
									<a
										class="flex items-center gap-2 underline-offset-3 hover:text-link hover:underline"
										href={objectHref(params.org, bucket.name)}
									>
										<HardDriveIcon class="size-4 flex-none text-muted-foreground" />
										<span class="truncate font-mono text-[13px]">{bucket.name}</span>
										{#if bucket.managed}
											<Badge
												variant="secondary"
												class="text-muted-foreground"
												title="Flock created this bucket for the Datasets tab"
											>
												Managed by Flock
											</Badge>
										{/if}
									</a>
								{/if}
							</td>
							<td
								class="hidden px-2 text-muted-foreground tabular-nums sm:table-cell"
								title={dateTime(bucket.created_at)}
							>
								{relativeTime(bucket.created_at, data.now)}
							</td>
							<td class="hidden truncate px-2 text-muted-foreground md:table-cell">
								{bucket.managed ? 'Flock' : (bucket.created_by?.name ?? 'A removed user')}
							</td>
							<td class="text-right">
								{#if !gone}
									<DropdownMenu.Root>
										<DropdownMenu.Trigger
											class="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-[color-mix(in_srgb,var(--muted),var(--foreground)_6%)] hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
											aria-label="Actions for {bucket.name}"
										>
											<EllipsisIcon class="size-4" />
										</DropdownMenu.Trigger>
										<DropdownMenu.Content align="end" class="w-44">
											{#if bucket.managed === 'datasets'}
												<DropdownMenu.Item>
													{#snippet child({ props })}
														<a {...props} href={datasetsHref(params.org)}>
															<DatabaseIcon /> Open in Datasets
														</a>
													{/snippet}
												</DropdownMenu.Item>
											{/if}
											<DropdownMenu.Item onSelect={() => (history = bucket.name)}>
												<HistoryIcon /> History
											</DropdownMenu.Item>
											{#if admin && !bucket.managed}
												<DropdownMenu.Separator />
												<DropdownMenu.Item
													variant="destructive"
													onSelect={() => (deleting = bucket.name)}
												>
													<Trash2Icon /> Delete bucket…
												</DropdownMenu.Item>
											{/if}
										</DropdownMenu.Content>
									</DropdownMenu.Root>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{:else}
			<p class="mt-5 text-muted-foreground">No buckets yet. Create the first one above.</p>
		{/if}
	</section>

	<section class="mt-10" aria-labelledby="connect-title">
		<h2 id="connect-title" class="text-2xl tracking-[-0.01em]">Connect</h2>
		<p class="mt-1 text-muted-foreground">
			Any S3 client can connect to this endpoint with path-style addressing. The commands below sign
			in with your Flock account and renew their keys automatically.
		</p>
		<div class="mt-3">
			<ConnectPanel
				org={params.org}
				endpoint={data.s3.endpoint}
				region={data.s3.region}
				bucket={live.find((b) => !b.managed)?.name ?? live[0]?.name ?? 'my-bucket'}
			/>
		</div>
	</section>
</Page>

<BucketHistory
	bind:open={() => history !== null, (o) => (history = o ? history : null)}
	org={params.org}
	bucket={history ?? ''}
/>
{#if admin}
	<DeleteBucketDialog
		bind:open={() => deleting !== null, (o) => (deleting = o ? deleting : null)}
		bucket={deleting ?? ''}
	/>
{/if}
