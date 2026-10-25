import DOMPurify from 'dompurify';
import { marked } from 'marked';

/**
 * Dataset cards: Markdown, often Hugging Face's, rendered as sanitized HTML. Anyone in an org can
 * write a card, so its HTML is never trusted: scripts, event handlers, `javascript:` links,
 * styles and forms are removed.
 */

/** A card without its YAML front matter (Hugging Face keeps its metadata there). */
export function stripFrontMatter(md: string): string {
	return md.replace(/^\uFEFF?---\r?\n[\s\S]*?\r?\n(---|\.\.\.)[ \t]*(\r?\n|$)/, '');
}

const FORBID_TAGS = ['style', 'form', 'input', 'button', 'textarea', 'select', 'iframe', 'object'];

let hooked = false;

/**
 * A card as safe HTML, or null where there is no DOM to sanitize with (the server): pages render
 * it once in the browser.
 */
export function renderMarkdown(md: string): string | null {
	if (typeof window === 'undefined' || !DOMPurify.isSupported) return null;
	if (!hooked) {
		// Links leave the page without handing it to the destination.
		DOMPurify.addHook('afterSanitizeAttributes', (node) => {
			if (node.tagName === 'A' && node.hasAttribute('href'))
				node.setAttribute('rel', 'noopener noreferrer');
		});
		hooked = true;
	}
	const html = marked.parse(stripFrontMatter(md), { async: false, gfm: true });
	return DOMPurify.sanitize(html, { FORBID_TAGS, FORBID_ATTR: ['style'] });
}
