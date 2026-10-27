<script lang="ts">
	import DownloadIcon from '@lucide/svelte/icons/download';
	import LinkIcon from '@lucide/svelte/icons/link';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { Button } from '#lib/components/ui/button/index.js';
	import { bucketCall, objectHref, parentOf, s3Uri } from '#lib/storage.ts';
	import type { ObjectDetails, Presigned } from '#lib/types.ts';
	import { bytes, dateTime } from '#lib/utils.ts';
	import CopyButton from '../CopyButton.svelte';
	import DeleteObjectsDialog from './DeleteObjectsDialog.svelte';
	import ObjectPreview from './ObjectPreview.svelte';

	interface Props {
		org: string;
		bucket: string;
		object: ObjectDetails;
	}

	let { org, bucket, object }: Props = $props();

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

	const details = $derived([
		['Key', object.key],
		['Size', `${bytes(object.size)} (${object.size.toLocaleString('en-US')} bytes)`],
		['Last modified', object.last_modified === null ? '–' : dateTime(object.last_modified)],
		['ETag', object.etag || '–'],
		['Content type', object.content_type ?? '–'],
		...Object.entries(object.metadata).map(([k, v]) => [`x-amz-meta-${k}`, v])
	]);
</script>

<div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
	<ObjectPreview {org} {bucket} {object} ondownload={download} />
	<aside class="grid content-start gap-4">
		<div class="flex flex-wrap gap-2">
			<Button size="sm" onclick={download}><DownloadIcon /> Download</Button>
			<Button
				variant="outline"
				size="sm"
				onclick={copyLink}
				title="A presigned URL valid for 1 hour"
			>
				<LinkIcon /> Copy link
			</Button>
			<Button
				variant="outline"
				size="sm"
				class="text-destructive"
				onclick={() => (deleting = true)}
			>
				<Trash2Icon /> Delete
			</Button>
		</div>
		<div class="flex items-center gap-1 text-[13px] text-muted-foreground">
			<span class="min-w-0 truncate font-mono text-[12.5px]">{s3Uri(bucket, object.key)}</span>
			<CopyButton text={s3Uri(bucket, object.key)} label="Copy S3 URI" />
		</div>
		<dl class="grid gap-2 text-[13px]">
			{#each details as [label, value] (label)}
				<div>
					<dt class="text-muted-foreground">{label}</dt>
					<dd class="font-mono text-[12.5px] break-all">{value}</dd>
				</div>
			{/each}
		</dl>
	</aside>
</div>

<DeleteObjectsDialog
	bind:open={deleting}
	{org}
	{bucket}
	keys={[object.key]}
	prefixes={[]}
	ondeleted={() => goto(objectHref(org, bucket, parentOf(object.key)), { invalidateAll: true })}
/>
