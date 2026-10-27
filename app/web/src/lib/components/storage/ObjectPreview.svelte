<script lang="ts">
	import DOMPurify from 'dompurify';
	import { marked } from 'marked';
	import { PREVIEW_BYTES, bucketCall, nameOf, prettyJson, previewOf } from '#lib/storage.ts';
	import type { ObjectDetails, Presigned } from '#lib/types.ts';
	import { unreachable } from '#lib/uploads.svelte.ts';
	import { bytes } from '#lib/utils.ts';

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
		if (preview.kind === 'markdown') {
			// Markdown becomes HTML, with anything that could run (scripts, handlers) taken out.
			const html = DOMPurify.sanitize(marked.parse(text, { async: false }));
			return { kind: 'html' as const, body: html };
		}
		return { kind: 'code' as const, body: text };
	});
</script>

<div class="min-w-0">
	{#if !preview.kind}
		<p class="bg-card px-4 py-6 text-center text-[13px] text-muted-foreground">
			No preview for this kind of file: download it to open it.
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
	{:else if shown?.kind === 'html'}
		<div class="markdown bg-card px-5 py-4 text-[14px] leading-[1.45]">
			<!-- eslint-disable-next-line svelte/no-at-html-tags -- sanitized by DOMPurify -->
			{@html shown.body}
		</div>
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

<style>
	.markdown :global(:is(h1, h2, h3, h4)) {
		margin: 1em 0 0.4em;
		font-weight: 400;
		letter-spacing: -0.01em;
	}
	.markdown :global(h1) {
		font-size: 24px;
	}
	.markdown :global(h2) {
		font-size: 20px;
	}
	.markdown :global(:is(p, ul, ol, pre, blockquote, table)) {
		margin: 0.6em 0;
	}
	.markdown :global(:is(ul, ol)) {
		padding-left: 1.4em;
		list-style: revert;
	}
	.markdown :global(a) {
		color: var(--link);
	}
	.markdown :global(code) {
		font-family: var(--font-mono);
		font-size: 0.9em;
	}
	.markdown :global(pre) {
		overflow-x: auto;
		background: var(--muted);
		padding: 0.6em 0.8em;
		border-radius: 0.5rem;
	}
	.markdown :global(blockquote) {
		color: var(--muted-foreground);
		padding-left: 0.8em;
	}
	.markdown :global(img) {
		max-width: 100%;
	}
</style>
