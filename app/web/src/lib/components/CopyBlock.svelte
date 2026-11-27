<script lang="ts" module>
	import hljs from 'highlight.js/lib/core';
	import bash from 'highlight.js/lib/languages/bash';
	import python from 'highlight.js/lib/languages/python';

	// Only what the app's samples are written in. Registering again is harmless, so this sits
	// beside Markdown's own set.
	if (!hljs.getLanguage('python')) hljs.registerLanguage('python', python);
	if (!hljs.getLanguage('bash')) hljs.registerLanguage('bash', bash);

	export type CodeLanguage = 'python' | 'bash';
</script>

<script lang="ts">
	import type { ClassValue } from 'svelte/elements';
	import CopyButton from './CopyButton.svelte';

	interface Props {
		code: string;
		/** Highlights the code as this language. Without one it's plain, as for a URL or a key. */
		lang?: CodeLanguage;
		/** The copy button's name. */
		label?: string;
		/** Wraps long lines instead of scrolling them: a one-line command, read whole. */
		wrap?: boolean;
		class?: ClassValue;
	}

	let { code, lang, label = 'Copy code', wrap = false, class: className }: Props = $props();

	const html = $derived(lang ? hljs.highlight(code, { language: lang }).value : null);
</script>

<!-- min-w-0: in a grid, a long line scrolls rather than widening the column. -->
<div class={['code-sample relative min-w-0 rounded-lg bg-muted', className]}>
	<!-- eslint-disable svelte/no-at-html-tags -- highlight.js escapes the code it marks up -->
	<pre
		class={[
			'py-3.5 pr-12 pl-4 font-mono text-[13px] leading-[1.65] [tab-size:4]',
			wrap ? 'break-words whitespace-pre-wrap' : 'overflow-x-auto'
		]}><code class={lang ? `hljs language-${lang}` : undefined}
			>{#if html !== null}{@html html}{:else}{code}{/if}</code
		></pre>
	<!-- eslint-enable svelte/no-at-html-tags -->
	<!-- On the block's fill, so a long line scrolls under it rather than through it. -->
	<CopyButton text={code} {label} class="absolute top-2 right-2 bg-muted" />
</div>

<!-- Token colours are the --hl-* tokens in layout.css, which Markdown's code uses too. -->
<style>
	.code-sample :global(:is(.hljs-comment, .hljs-quote)) {
		color: var(--hl-comment);
		font-style: italic;
	}
	.code-sample :global(:is(.hljs-keyword, .hljs-literal, .hljs-doctag)) {
		color: var(--hl-keyword);
	}
	.code-sample :global(:is(.hljs-string, .hljs-regexp, .hljs-symbol)) {
		color: var(--hl-string);
	}
	.code-sample :global(:is(.hljs-number, .hljs-bullet)) {
		color: var(--hl-number);
	}
	.code-sample :global(:is(.hljs-title, .hljs-section)) {
		color: var(--hl-title);
	}
	.code-sample :global(:is(.hljs-type, .hljs-built_in, .hljs-title.class_)) {
		color: var(--hl-type);
	}
	.code-sample :global(:is(.hljs-attr, .hljs-attribute, .hljs-variable, .hljs-property)) {
		color: var(--hl-attr);
	}
	.code-sample :global(.language-bash .hljs-built_in) {
		color: var(--hl-title);
	}
	.code-sample :global(:is(.hljs-operator, .hljs-punctuation)) {
		color: var(--muted-foreground);
	}
	.code-sample :global(.hljs-meta) {
		color: var(--hl-meta);
	}
	.code-sample :global(.hljs-params) {
		color: inherit;
	}
</style>
