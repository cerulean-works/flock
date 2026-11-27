<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import LinkIcon from '@lucide/svelte/icons/link';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import DetailPage from '#lib/components/DetailPage.svelte';
	import Properties from '#lib/components/Properties.svelte';
	import Property from '#lib/components/Property.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { bucketCall, nameOf, objectHref, parentOf, s3Uri, type Crumb } from '#lib/storage.ts';
	import type { ObjectDetails, Presigned } from '#lib/types.ts';
	import { MODELS_BUCKET } from '#lib/models.ts';
	import { bytes, dateTime } from '#lib/utils.ts';
	import CopyButton from '../CopyButton.svelte';
	import DeleteObjectsDialog from './DeleteObjectsDialog.svelte';
	import ObjectPreview from './ObjectPreview.svelte';

	interface Props {
		org: string;
		bucket: string;
		object: ObjectDetails;
		/** The header's steps before the object's name: Storage › bucket › folders. */
		trail?: Crumb[];
	}

	let { org, bucket, object, trail = [] }: Props = $props();

	const presign = (link = false) =>
		bucketCall<Presigned>(org, bucket, 'presign', { method: 'get', key: object.key, link });

	async function download() {
		try {
			window.location.assign((await presign()).url!);
		} catch (e) {
			toast.error((e as Error).message);
		}
	}

	async function copyLink() {
		try {
			const { url } = await presign(true);
			await navigator.clipboard.writeText(url!);
			toast.success('Copied a link that expires in one hour.');
		} catch (e) {
			toast.error((e as Error).message);
		}
	}

	let deleting = $state(false);

	const meta = $derived(Object.entries(object.metadata));
	const uri = $derived(s3Uri(bucket, object.key));
</script>

{#snippet controls()}
	<Button
		variant="quiet"
		size="icon-bar"
		aria-label="Download"
		{@attach hint('Download')}
		onclick={download}
	>
		<DownloadIcon />
	</Button>
	<Button
		variant="quiet"
		size="icon-bar"
		aria-label="Copy link"
		{@attach hint('Copy a link that works for 1 hour')}
		onclick={copyLink}
	>
		<LinkIcon />
	</Button>
	{#if bucket !== MODELS_BUCKET}
		<DropdownMenu.Root>
			<DropdownMenu.Trigger>
				{#snippet child({ props })}
					<Button {...props} variant="quiet" size="icon-bar" aria-label="More object actions">
						<EllipsisIcon />
					</Button>
				{/snippet}
			</DropdownMenu.Trigger>
			<DropdownMenu.Content align="end" class="w-40">
				<DropdownMenu.Item variant="destructive" onSelect={() => (deleting = true)}>
					<Trash2Icon /> Delete…
				</DropdownMenu.Item>
			</DropdownMenu.Content>
		</DropdownMenu.Root>
	{/if}
{/snippet}

{#snippet properties()}
	<Properties label="Object">
		<Property label="S3 URI" stack>
			<span class="flex items-start gap-1">
				<!-- Wrapping at its slashes, and anywhere only when a part is longer than the line. -->
				<span class="min-w-0 pt-1 font-mono text-[12px] wrap-anywhere"
					>{#each uri.split('/') as part, i (i)}{#if i}/<wbr />{/if}{part}{/each}</span
				>
				<CopyButton text={uri} label="Copy S3 URI" />
			</span>
		</Property>
		<Property label="Size">
			<span {@attach hint(`${object.size.toLocaleString('en-US')} bytes`)}
				>{bytes(object.size)}</span
			>
		</Property>
		{#if object.last_modified !== null}
			<Property label="Modified">{dateTime(object.last_modified)}</Property>
		{/if}
		{#if object.content_type}<Property label="Type">{object.content_type}</Property>{/if}
		{#if object.etag}
			<Property label="ETag" mono><span class="break-all">{object.etag}</span></Property>
		{/if}
	</Properties>
	{#if meta.length}
		<Properties label="Metadata">
			{#each meta as [key, value] (key)}
				<Property label={key} mono stack><span class="break-all">{value}</span></Property>
			{/each}
		</Properties>
	{/if}
{/snippet}

<DetailPage title={nameOf(object.key)} {trail} titleHint={uri} {controls} {properties}>
	<ObjectPreview {org} {bucket} {object} ondownload={download} />
</DetailPage>

<DeleteObjectsDialog
	bind:open={deleting}
	{org}
	{bucket}
	keys={[object.key]}
	prefixes={[]}
	ondeleted={() => goto(objectHref(org, bucket, parentOf(object.key)), { invalidateAll: true })}
/>
