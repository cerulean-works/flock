<script lang="ts">
	import { PREVIEW_BYTES, bucketCall, nameOf, prettyJson, previewOf } from '#lib/storage.ts';
	import type { ObjectDetails, Presigned } from '#lib/types.ts';
	import { unreachable } from '#lib/uploads.svelte.ts';
	import { bytes } from '#lib/utils.ts';
	import Markdown from '../Markdown.svelte';

	interface Props {
		org: string;
		bucket: string;
		object: ObjectDetails;
		/** Download the whole object, for a cut-off text preview. */
		ondownload: () => void;
	}

	let { org, bucket, object, ondownload }: Props = $props();

	const preview = $derived(previewOf(object.key, object.content_type));
	const truncated = $derived(object.size > PREVIEW_BYTES);

	let url = $state<string | null>(null);
	let text = $state<string | null>(null);
	let error = $state<string | null>(null);

	// The object's bytes come straight from the S3 proxy, by a presigned URL served inline. Text
	// is read up to 1 MiB with a Range request; media stream with ranges of their own.
	$effect(() => {
		const { kind, type } = preview;
		const key = object.key;
		url = text = error = null;
		if (!kind) return;
		let stale = false;
		const media = kind === 'image' || kind === 'video' || kind === 'audio' || kind === 'pdf';
		bucketCall<Presigned>(org, bucket, 'presign', {
			method: 'get',
			key,
			disposition: 'inline',
			...(media && type ? { content_type: type } : {})
		})
			.then(async (signed) => {
				if (stale) return;
				if (media) {
					url = signed.url!;
					return;
				}
				let res: Response;
				try {
					res = await fetch(signed.url!, { headers: { range: `bytes=0-${PREVIEW_BYTES - 1}` } });
				} catch {
					throw new Error(unreachable(signed.url!));
				}
				if (!res.ok) throw new Error(`The S3 proxy answered ${res.status}`);
				const body = await res.text();
				if (!stale) text = body;
			})
			.catch((e: Error) => {
				if (!stale) error = e.message;
			});
		return () => (stale = true);
	});

	const shown = $derived.by(() => {
		if (text === null) return null;
		if (preview.kind === 'json' && !truncated) {
			const pretty = prettyJson(text);
			if (pretty !== null) return { kind: 'code' as const, body: pretty };
		}
		if (preview.kind === 'markdown') return { kind: 'markdown' as const, body: text };
		return { kind: 'code' as const, body: text };
	});
</script>

<div class="min-w-0">
	{#if !preview.kind}
		<p class="bg-card px-4 py-6 text-center text-[13px] text-muted-foreground">
			This file type can’t be previewed.
		</p>
	{:else if error}
		<p class="bg-card px-4 py-6 text-[13px] text-destructive" role="alert">{error}</p>
	{:else if url && preview.kind === 'image'}
		<!-- Always an <img>, SVG included: an image's scripts never run there. -->
		<div class="grid place-items-center bg-card p-4">
			<img src={url} alt={nameOf(object.key)} class="max-h-[70vh] max-w-full object-contain" />
		</div>
	{:else if url && preview.kind === 'video'}
		<!-- svelte-ignore a11y_media_has_caption -->
		<video src={url} controls class="max-h-[70vh] w-full bg-card"></video>
	{:else if url && preview.kind === 'audio'}
		<audio src={url} controls class="w-full"></audio>
	{:else if url && preview.kind === 'pdf'}
		<iframe src={url} title={nameOf(object.key)} class="h-[75vh] w-full border-0 bg-card"></iframe>
	{:else if shown?.kind === 'markdown'}
		<Markdown text={shown.body} class="pt-1" />
	{:else if shown}
		<pre
			class="max-h-[70vh] overflow-auto bg-card px-4 py-3 font-mono text-[12.5px] leading-relaxed whitespace-pre"><code
				>{shown.body}</code
			></pre>
	{:else}
		<p class="bg-card px-4 py-6 text-center text-[13px] text-muted-foreground">Loading preview…</p>
	{/if}
	{#if text !== null && truncated}
		<p class="mt-2 text-[13px] text-muted-foreground">
			Showing the first {bytes(PREVIEW_BYTES)} of {bytes(object.size)}.
			<button class="text-link underline-offset-3 hover:underline" onclick={ondownload}>
				Download the whole file
			</button>
		</p>
	{/if}
</div>
