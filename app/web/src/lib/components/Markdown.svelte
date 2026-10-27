<script lang="ts">
	import DOMPurify from 'dompurify';
	import { marked } from 'marked';
	import type { ClassValue } from 'svelte/elements';

	/** Markdown text, shown as HTML with anything that could run (scripts, handlers) taken out. */
	let { text, class: className }: { text: string; class?: ClassValue } = $props();

	const html = $derived(DOMPurify.sanitize(marked.parse(text, { async: false })));
</script>

<div class={['markdown text-[14px] leading-[1.45]', className]}>
	<!-- eslint-disable-next-line svelte/no-at-html-tags -- sanitized by DOMPurify -->
	{@html html}
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
	.markdown :global(table) {
		border-collapse: collapse;
	}
	.markdown :global(:is(th, td)) {
		border: 1px solid var(--border);
		padding: 0.25em 0.6em;
	}
</style>
