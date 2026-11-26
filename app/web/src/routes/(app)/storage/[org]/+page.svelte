<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import BoxIcon from '@lucide/svelte/icons/box';
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import HardDriveIcon from '@lucide/svelte/icons/hard-drive';
	import HistoryIcon from '@lucide/svelte/icons/history';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import BucketHistory from '#lib/components/storage/BucketHistory.svelte';
	import ConnectDialog from '#lib/components/storage/ConnectDialog.svelte';
	import CreateBucket from '#lib/components/storage/CreateBucket.svelte';
	import DeleteBucketDialog from '#lib/components/storage/DeleteBucketDialog.svelte';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { datasetsHref } from '#lib/datasets.ts';
	import { modelsHref } from '#lib/models.ts';
	import { objectHref } from '#lib/storage.ts';
	import { dateTime, relativeTime } from '#lib/utils.ts';
	import ListPage from '#lib/components/ListPage.svelte';
	import DataTable, { type Column, ROW_LINK } from '#lib/components/DataTable.svelte';
	import type { Bucket } from '#lib/types.ts';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();

	const admin = $derived(data.shell.access === 'admin');
	const live = $derived(data.buckets.filter((b) => b.deleted_at === undefined));

	// Narrower windows drop who made a bucket, then when.
	const COLUMNS: Column[] = [
		{ id: 'name', label: 'Name' },
		{ id: 'by', label: 'Created by', from: 'md' },
		{ id: 'created', label: 'Created', align: 'end', from: 'sm' }
	];

	let history = $state<string | null>(null);
	let deleting = $state<string | null>(null);
</script>

<svelte:head>
	<title>Storage · {data.shell.orgName}</title>
</svelte:head>

{#snippet actions()}
	<ConnectDialog
		org={params.org}
		endpoint={data.s3.endpoint}
		region={data.s3.region}
		bucket={live.find((b) => !b.managed)?.name ?? live[0]?.name ?? 'my-bucket'}
	/>
	<CreateBucket nameLimit={data.nameLimit} />
{/snippet}

<ListPage title="Storage" org={false} {actions}>
	<DataTable
		columns={COLUMNS}
		rows={data.buckets}
		key={(b) => `${b.name}:${b.deleted_at ?? ''}`}
		name={(b) => b.name}
		label="Buckets"
		dense
		empty={none}
		{menu}
	>
		{#snippet lead(bucket)}<HardDriveIcon
				class={bucket.deleted_at === undefined ? undefined : 'text-faint'}
			/>{/snippet}
		{#snippet cell(bucket, column)}
			{@const gone = bucket.deleted_at !== undefined}
			{#if column === 'name'}
				{#if gone}
					<span
						class="flex items-center gap-2 text-faint"
						{@attach hint(
							'This bucket is being emptied and removed. Its name can be used again once that’s done.'
						)}
					>
						<span class="truncate font-mono text-[13px]">{bucket.name}</span>
						<span class="flex-none text-[12px]">being deleted</span>
					</span>
				{:else}
					<span class="flex min-w-0 items-center gap-2">
						<a
							class={['truncate font-mono text-[13px] hover:text-link', ROW_LINK]}
							href={objectHref(params.org, bucket.name)}
						>
							{bucket.name}
						</a>
						{#if bucket.managed}
							<Badge
								variant="secondary"
								class="text-muted-foreground"
								{@attach hint(
									bucket.managed === 'models'
										? 'Flock created this bucket for the Models tab'
										: 'Flock created this bucket for the Datasets tab'
								)}
							>
								Managed by Flock
							</Badge>
						{/if}
					</span>
				{/if}
			{:else if column === 'created'}
				<span
					class={['block truncate', gone ? 'text-faint' : 'text-muted-foreground']}
					{@attach hint(dateTime(bucket.created_at))}
				>
					{relativeTime(bucket.created_at, data.now)}
				</span>
			{:else}
				<span class={['block truncate', gone ? 'text-faint' : 'text-muted-foreground']}>
					{bucket.managed ? 'Flock' : (bucket.created_by?.name ?? 'A removed user')}
				</span>
			{/if}
		{/snippet}
	</DataTable>
</ListPage>

{#snippet none()}No buckets yet. Create one to start uploading.{/snippet}

{#snippet menu(bucket: Bucket)}
	{#if bucket.deleted_at === undefined}
		<DropdownMenu.Root>
			<DropdownMenu.Trigger
				class="grid size-6 place-items-center rounded-md text-muted-foreground opacity-0 group-focus-within/row:opacity-100 group-hover/row:opacity-100 hover:bg-[color-mix(in_srgb,var(--muted),var(--foreground)_6%)] hover:text-foreground focus-visible:opacity-100 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none data-[state=open]:opacity-100 pointer-coarse:opacity-100"
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
				{:else if bucket.managed === 'models'}
					<DropdownMenu.Item>
						{#snippet child({ props })}
							<a {...props} href={modelsHref(params.org)}>
								<BoxIcon /> Open in Models
							</a>
						{/snippet}
					</DropdownMenu.Item>
				{/if}
				<DropdownMenu.Item onSelect={() => (history = bucket.name)}>
					<HistoryIcon /> History
				</DropdownMenu.Item>
				{#if admin && !bucket.managed}
					<DropdownMenu.Separator />
					<DropdownMenu.Item variant="destructive" onSelect={() => (deleting = bucket.name)}>
						<Trash2Icon /> Delete bucket…
					</DropdownMenu.Item>
				{/if}
			</DropdownMenu.Content>
		</DropdownMenu.Root>
	{/if}
{/snippet}

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
