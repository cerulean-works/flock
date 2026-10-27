<script lang="ts">
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import HardDriveIcon from '@lucide/svelte/icons/hard-drive';
	import HistoryIcon from '@lucide/svelte/icons/history';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import BucketHistory from '#lib/components/storage/BucketHistory.svelte';
	import Breadcrumbs from '#lib/components/storage/Breadcrumbs.svelte';
	import ConnectPanel from '#lib/components/storage/ConnectPanel.svelte';
	import CreateBucket from '#lib/components/storage/CreateBucket.svelte';
	import DeleteBucketDialog from '#lib/components/storage/DeleteBucketDialog.svelte';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { crumbs, objectHref } from '#lib/storage.ts';
	import { count, dateTime, relativeTime } from '#lib/utils.ts';
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

<div class="flex min-h-0 flex-1 flex-col">
	<main class="min-h-0 flex-1 overflow-y-auto px-5 pb-16">
		<div class="mx-auto max-w-240">
			<div class="pt-5">
				<Breadcrumbs crumbs={crumbs(params.org, data.shell.orgName ?? params.org)} />
			</div>
			<h1 class="pt-3 text-[34px] leading-[1.15] tracking-[-0.025em]">Storage</h1>
			<p class="mt-1 max-w-[62ch] text-muted-foreground">
				S3 buckets shared by everyone in {data.shell.orgName}. Members read, write and delete
				objects; only admins delete buckets.
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
												title="Being emptied and removed: its name can be used again once that’s done"
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
										{bucket.created_by?.name ?? 'Someone since removed'}
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
													<DropdownMenu.Item onSelect={() => (history = bucket.name)}>
														<HistoryIcon /> History
													</DropdownMenu.Item>
													{#if admin}
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
					<p class="mt-5 text-muted-foreground">No buckets yet: create the first one above.</p>
				{/if}
			</section>

			<section class="mt-10" aria-labelledby="connect-title">
				<h2 id="connect-title" class="text-2xl tracking-[-0.01em]">Connect</h2>
				<p class="mt-1 max-w-[62ch] text-muted-foreground">
					Any S3 client works, path-style, at this endpoint. These use your Flock sign-in for keys
					that renew themselves.
				</p>
				<div class="mt-3">
					<ConnectPanel
						org={params.org}
						endpoint={data.s3.endpoint}
						region={data.s3.region}
						bucket={live[0]?.name ?? 'my-bucket'}
					/>
				</div>
			</section>
		</div>
	</main>
</div>

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
