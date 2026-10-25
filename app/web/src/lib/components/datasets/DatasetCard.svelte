<script lang="ts">
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import { onMount } from 'svelte';
	import { enhance } from '$app/forms';
	import CopyBlock from '#lib/components/CopyBlock.svelte';
	import FormError from '#lib/components/FormError.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Textarea } from '#lib/components/ui/textarea/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { renderMarkdown } from '#lib/markdown.ts';
	import type { DatasetReadme } from '#lib/types.ts';

	interface Props {
		readme: DatasetReadme;
		/** Members edit the card. */
		editable: boolean;
		/** An earlier version is in view: its card is shown, and edits start from the latest. */
		pinned: boolean;
		/** The dataset has no version yet: a card would be its first. */
		empty: boolean;
		/** `org/name`, for the how-to. */
		repo: string;
	}

	let { readme, editable, pinned, empty, repo }: Props = $props();

	// Rendered in the browser only, once mounted: sanitizing needs a DOM, and the server's page
	// (the Markdown as written) must hydrate as it was sent.
	let mounted = $state(false);
	onMount(() => (mounted = true));
	const html = $derived(mounted && readme.content ? renderMarkdown(readme.content) : null);

	let editing = $state(false);
	const saving = new Mutation<{ version: number }>({
		success: ({ version }) => `Card saved as version ${version}`,
		done: () => (editing = false)
	});
</script>

{#if editing}
	<form
		method="POST"
		action="?/readme"
		class="mx-auto grid max-w-[52rem] gap-4 px-5 py-8"
		use:enhance={saving.enhance}
	>
		<div class="grid gap-2">
			<Label for="card-content">Card</Label>
			<Textarea
				id="card-content"
				name="content"
				value={readme.content ?? ''}
				rows={22}
				class="font-mono text-[13px] leading-relaxed"
				spellcheck="true"
			/>
			<p class="text-xs text-muted-foreground">Markdown. Saving makes a new version.</p>
		</div>
		<div class="grid gap-2">
			<Label for="card-message">
				Message <span class="font-normal text-muted-foreground">optional</span>
			</Label>
			<Input id="card-message" name="message" placeholder="Describe the dataset’s columns" />
		</div>
		<FormError error={saving.error} />
		<div class="flex gap-2">
			<Button type="submit" disabled={saving.pending}>
				{saving.pending ? 'Saving…' : 'Save card'}
			</Button>
			<Button
				variant="outline"
				onclick={() => {
					editing = false;
					saving.reset();
				}}
			>
				Cancel
			</Button>
		</div>
	</form>
{:else if readme.content}
	<div class="mx-auto max-w-[44rem] px-5 py-8">
		{#if editable && !pinned}
			<div class="mb-2 flex justify-end">
				<Button variant="ghost" size="sm" onclick={() => (editing = true)}>
					<PencilIcon /> Edit
				</Button>
			</div>
		{/if}
		{#if html != null}
			<!-- eslint-disable-next-line svelte/no-at-html-tags -- sanitized by renderMarkdown -->
			<article class="card-prose">{@html html}</article>
		{:else}
			<!-- Until the browser renders it (and wherever it can't), the Markdown as written. -->
			<pre class="font-mono text-[13px] leading-relaxed whitespace-pre-wrap">{readme.content}</pre>
		{/if}
	</div>
{:else}
	<div class="mx-auto max-w-[44rem] px-5 py-10">
		<h2 class="font-serif text-[28px] leading-tight tracking-[-0.01em]">
			{pinned ? 'This version has no card' : 'No card yet'}
		</h2>
		<p class="mt-3 font-serif text-[19px] leading-normal">
			A card says what the dataset holds, where it came from and how to use it.
		</p>
		{#if editable && !pinned && !empty}
			<p class="mt-6">
				<Button variant="outline" onclick={() => (editing = true)}>
					<PencilIcon /> Write the card
				</Button>
			</p>
		{/if}
		{#if empty}
			<p class="mt-6 text-[13px] text-muted-foreground">
				The dataset has no versions yet: its first write makes version 1. Write rows from Python:
			</p>
			<div class="mt-2">
				<CopyBlock
					code={`from flock.hub import push\n\npush([{"text": "hello"}], ${JSON.stringify(repo)}, message="first rows")`}
				/>
			</div>
		{/if}
	</div>
{/if}

<style>
	/* The card's prose: Newsreader at a reading measure, code in monospace. */
	.card-prose {
		font-family: var(--font-serif);
		font-size: 19px;
		line-height: 1.5;
		overflow-wrap: break-word;
	}
	.card-prose :global(:where(h1, h2, h3, h4)) {
		font-weight: 400;
		letter-spacing: -0.01em;
		line-height: 1.2;
		margin: 1.6em 0 0.5em;
	}
	.card-prose :global(h1) {
		font-size: 34px;
		letter-spacing: -0.02em;
	}
	.card-prose :global(h2) {
		font-size: 27px;
	}
	.card-prose :global(h3) {
		font-size: 22px;
	}
	.card-prose :global(h4) {
		font-size: 19px;
		font-weight: 500;
	}
	.card-prose :global(:first-child) {
		margin-top: 0;
	}
	.card-prose :global(:where(p, ul, ol, blockquote, pre, table)) {
		margin: 0 0 1em;
	}
	.card-prose :global(:where(ul, ol)) {
		padding-left: 1.4em;
	}
	.card-prose :global(ul) {
		list-style: disc;
	}
	.card-prose :global(ol) {
		list-style: decimal;
	}
	.card-prose :global(a) {
		color: var(--link);
		text-decoration: underline;
		text-underline-offset: 3px;
	}
	.card-prose :global(blockquote) {
		border-left: 2px solid var(--border);
		color: var(--muted-foreground);
		padding-left: 1em;
	}
	.card-prose :global(code) {
		font-family: var(--font-mono);
		font-size: 0.78em;
	}
	.card-prose :global(pre) {
		background: var(--muted);
		font-size: 15px;
		line-height: 1.6;
		overflow-x: auto;
		padding: 12px 16px;
	}
	.card-prose :global(pre code) {
		font-size: 13px;
	}
	.card-prose :global(table) {
		border-collapse: collapse;
		display: block;
		font-family: var(--font-sans);
		font-size: 14px;
		overflow-x: auto;
	}
	.card-prose :global(:where(th, td)) {
		border-bottom: 1px solid var(--divider);
		padding: 6px 12px 6px 0;
		text-align: left;
		vertical-align: top;
	}
	.card-prose :global(th) {
		font-weight: 500;
	}
	.card-prose :global(img) {
		max-width: 100%;
	}
	.card-prose :global(hr) {
		border: 0;
		border-top: 1px solid var(--border);
		margin: 2em 0;
	}
</style>
