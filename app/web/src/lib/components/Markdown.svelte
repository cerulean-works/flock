<script lang="ts" module>
	import DOMPurify from 'dompurify';
	import hljs from 'highlight.js/lib/core';
	import bash from 'highlight.js/lib/languages/bash';
	import diff from 'highlight.js/lib/languages/diff';
	import ini from 'highlight.js/lib/languages/ini';
	import javascript from 'highlight.js/lib/languages/javascript';
	import json from 'highlight.js/lib/languages/json';
	import python from 'highlight.js/lib/languages/python';
	import sql from 'highlight.js/lib/languages/sql';
	import typescript from 'highlight.js/lib/languages/typescript';
	import xml from 'highlight.js/lib/languages/xml';
	import yaml from 'highlight.js/lib/languages/yaml';
	import { Marked, Renderer } from 'marked';

	for (const [name, language] of Object.entries({
		bash,
		diff,
		ini,
		javascript,
		json,
		python,
		sql,
		typescript,
		xml,
		yaml
	}))
		hljs.registerLanguage(name, language);
	hljs.registerAliases(['sh', 'shell', 'zsh', 'console'], { languageName: 'bash' });
	hljs.registerAliases(['py'], { languageName: 'python' });
	hljs.registerAliases(['toml'], { languageName: 'ini' });
	hljs.registerAliases(['html'], { languageName: 'xml' });

	const escape = (text: string) =>
		text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

	const base = new Renderer();

	// Fenced code is highlighted like the docs site, with a copy button; languages it doesn't
	// know stay plain. Tables scroll sideways in a wrapper, so they can still span the width.
	const marked = new Marked({
		renderer: {
			code({ text, lang }) {
				const name = lang?.trim().split(/\s/)[0].toLowerCase() ?? '';
				const known = !!name && !!hljs.getLanguage(name);
				const body = known ? hljs.highlight(text, { language: name }).value : escape(text);
				return (
					`<div class="code-block"><pre><code class="hljs${known ? ` language-${name}` : ''}">${body}</code></pre>` +
					`<button type="button" class="code-copy" aria-label="Copy code">Copy</button></div>\n`
				);
			},
			table(token) {
				return `<div class="table-scroll">${base.table.call(this, token)}</div>\n`;
			}
		}
	});

	/** YAML front matter, as Hugging Face cards start with: metadata, not text to read. */
	const FRONT_MATTER = /^---\r?\n[\s\S]*?\r?\n---[ \t]*(\r?\n|$)/;
</script>

<script lang="ts">
	import { toast } from 'svelte-sonner';
	import type { ClassValue } from 'svelte/elements';

	/** Markdown text, shown as HTML with anything that could run (scripts, handlers) taken out. */
	let { text, class: className }: { text: string; class?: ClassValue } = $props();

	const html = $derived(
		DOMPurify.sanitize(marked.parse(text.replace(FRONT_MATTER, ''), { async: false }))
	);

	/** The code blocks' copy buttons, which come from the HTML above, not from Svelte. */
	function copyButtons(node: HTMLElement) {
		const onclick = (e: MouseEvent) => {
			const button = (e.target as Element).closest<HTMLButtonElement>('button.code-copy');
			const code = button?.parentElement?.querySelector('pre')?.textContent;
			if (!button || code == null) return;
			navigator.clipboard.writeText(code).then(
				() => {
					button.textContent = 'Copied';
					setTimeout(() => (button.textContent = 'Copy'), 1500);
				},
				() => toast.error('Couldn’t copy to the clipboard')
			);
		};
		node.addEventListener('click', onclick);
		return () => node.removeEventListener('click', onclick);
	}
</script>

<div class={['markdown', className]} {@attach copyButtons}>
	<!-- eslint-disable-next-line svelte/no-at-html-tags -- sanitized by DOMPurify -->
	{@html html}
</div>

<!-- The docs site's reading style (docs.cerulean.works). -->
<style>
	.markdown {
		font: 400 15px/1.65 var(--font-reading);
		overflow-wrap: break-word;
	}
	.markdown > :global(:first-child) {
		margin-top: 0;
	}
	.markdown > :global(:last-child) {
		margin-bottom: 0;
	}
	.markdown :global(:is(h1, h2, h3, h4, h5, h6)) {
		font-family: var(--font-sans);
		font-weight: 400;
	}
	.markdown :global(h1) {
		margin: 40px 0 20px;
		font-size: 30px;
		line-height: 1.2;
		letter-spacing: -0.025em;
	}
	.markdown :global(h2) {
		margin: 40px 0 16px;
		font-size: 24px;
		line-height: 1.2;
		letter-spacing: -0.015em;
	}
	.markdown :global(h3) {
		margin: 32px 0 12px;
		font-size: 20px;
		line-height: 1.25;
		letter-spacing: -0.01em;
	}
	.markdown :global(:is(h4, h5, h6)) {
		margin: 24px 0 8px;
		font-size: 17px;
		font-weight: 500;
		line-height: 1.35;
	}
	.markdown :global(:is(p, ul, ol, dl, figure, blockquote, .code-block, .table-scroll)) {
		margin: 0 0 20px;
	}
	.markdown :global(a) {
		color: var(--link);
		text-decoration: underline;
		text-decoration-thickness: 1px;
		text-underline-offset: 0.18em;
	}
	.markdown :global(a:hover) {
		text-decoration-thickness: 2px;
	}
	.markdown :global(:is(strong, b)) {
		font-weight: 600;
	}
	.markdown :global(em) {
		font-style: italic;
	}
	.markdown :global(hr) {
		border: 0;
		border-top: 1px solid var(--border);
		margin: 40px 0;
	}
	.markdown :global(:is(ul, ol)) {
		padding-left: 1.4em;
	}
	.markdown :global(ul) {
		list-style: disc outside;
	}
	.markdown :global(ol) {
		list-style: decimal outside;
	}
	.markdown :global(li) {
		margin: 6px 0;
		padding-left: 0.2em;
	}
	.markdown :global(li::marker) {
		color: var(--muted-foreground);
	}
	.markdown :global(li > :is(ul, ol, p)) {
		margin: 6px 0;
	}
	.markdown :global(:is(img, video)) {
		display: inline;
		max-width: 100%;
		height: auto;
	}
	.markdown :global(:is(code, kbd, samp, pre)) {
		font-family: var(--font-mono);
	}
	.markdown :global(:not(pre) > code) {
		background: var(--card);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 0.1em 0.35em;
		font-size: 0.8em;
		overflow-wrap: anywhere;
	}
	.markdown :global(a > code) {
		color: inherit;
	}
	.markdown :global(pre) {
		background: var(--card);
		border-radius: 8px;
		padding: 12px 16px;
		font-size: 13px;
		line-height: 1.6;
		tab-size: 4;
		overflow-x: auto;
	}
	.markdown :global(.code-block) {
		position: relative;
	}
	.markdown :global(.code-copy) {
		position: absolute;
		top: 6px;
		right: 6px;
		border: 1px solid var(--border);
		border-radius: 6px;
		background: var(--background);
		padding: 2px 8px;
		font: 400 12px/1.5 var(--font-sans);
		color: var(--muted-foreground);
		opacity: 0;
		transition: opacity 0.12s;
	}
	.markdown :global(.code-copy:hover) {
		color: var(--foreground);
	}
	.markdown :global(:is(.code-block:hover, .code-block:focus-within) .code-copy) {
		opacity: 1;
	}
	@media (hover: none) {
		.markdown :global(.code-copy) {
			opacity: 1;
		}
	}
	.markdown :global(.table-scroll) {
		max-width: 100%;
		overflow-x: auto;
	}
	/* Full width, the space between columns the same as at the edges of each cell's text. */
	.markdown :global(table) {
		width: 100%;
		border-collapse: collapse;
		font-size: 14px;
		line-height: 1.5;
	}
	.markdown :global(:is(th, td)) {
		border-bottom: 1px solid var(--border);
		padding: 8px 12px;
		text-align: left;
		vertical-align: top;
	}
	.markdown :global(:is(th, td):first-child) {
		padding-left: 0;
	}
	.markdown :global(:is(th, td):last-child) {
		padding-right: 0;
	}
	.markdown :global(th) {
		border-bottom-color: var(--muted-foreground);
		font-weight: 500;
	}
	.markdown :global(blockquote) {
		border-left: 2px solid var(--border);
		color: var(--muted-foreground);
		padding: 2px 0 2px 20px;
	}
	.markdown :global(blockquote > :last-child) {
		margin-bottom: 0;
	}

	.markdown :global(:is(.hljs-comment, .hljs-quote)) {
		color: var(--hl-comment);
		font-style: italic;
	}
	.markdown :global(:is(.hljs-keyword, .hljs-selector-tag, .hljs-literal, .hljs-doctag)) {
		color: var(--hl-keyword);
	}
	.markdown :global(:is(.hljs-string, .hljs-regexp, .hljs-symbol, .hljs-template-variable)) {
		color: var(--hl-string);
	}
	.markdown :global(:is(.hljs-number, .hljs-bullet, .hljs-link)) {
		color: var(--hl-number);
	}
	.markdown :global(:is(.hljs-title, .hljs-section, .hljs-selector-id)) {
		color: var(--hl-title);
	}
	.markdown :global(:is(.hljs-type, .hljs-built_in, .hljs-title.class_)) {
		color: var(--hl-type);
	}
	.markdown
		:global(
			:is(
				.hljs-attr,
				.hljs-attribute,
				.hljs-variable,
				.hljs-property,
				.hljs-name,
				.hljs-selector-class
			)
		) {
		color: var(--hl-attr);
	}
	.markdown :global(.language-bash .hljs-built_in) {
		color: var(--hl-title);
	}
	.markdown :global(:is(.hljs-operator, .hljs-punctuation)) {
		color: var(--muted-foreground);
	}
	.markdown :global(:is(.hljs-meta, .hljs-tag)) {
		color: var(--hl-meta);
	}
	.markdown :global(.hljs-meta .hljs-string) {
		color: var(--hl-string);
	}
	.markdown :global(.hljs-deletion) {
		color: var(--hl-deletion);
	}
	.markdown :global(.hljs-addition) {
		color: var(--hl-addition);
	}
	.markdown :global(.hljs-params) {
		color: inherit;
	}
</style>
