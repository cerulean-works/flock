// In the browser project (the `.svelte.spec` name): DOMPurify sanitizes with a real DOM.
import { describe, expect, it } from 'vitest';
import { renderMarkdown, stripFrontMatter } from './markdown.ts';

const render = (md: string) => {
	const html = renderMarkdown(md);
	if (html == null) throw new Error('no DOM');
	const el = document.createElement('div');
	el.innerHTML = html;
	return { html, el };
};

describe('renderMarkdown', () => {
	it('keeps the safe parts of a card', () => {
		const { el } = render(
			'# Title\n\nSome *prose* with [a link](https://example.test).\n\n```python\nprint(1)\n```\n\n| a | b |\n| - | - |\n| 1 | 2 |'
		);
		expect(el.querySelector('h1')?.textContent).toBe('Title');
		expect(el.querySelector('em')?.textContent).toBe('prose');
		expect(el.querySelector('pre code')?.textContent).toBe('print(1)\n');
		expect(el.querySelector('table td')?.textContent).toBe('1');
		const a = el.querySelector('a')!;
		expect(a.getAttribute('href')).toBe('https://example.test');
		expect(a.getAttribute('rel')).toBe('noopener noreferrer');
	});

	it('strips scripts, handlers and javascript: links', () => {
		const { html, el } = render(
			'<script>alert(1)</script>\n\n<img src="x.png" onerror="alert(2)">\n\n[click](javascript:alert(3))\n\n<a href="javascript:alert(4)">raw</a>\n\n<p style="color:red" onclick="alert(5)">styled</p>'
		);
		expect(el.querySelector('script')).toBeNull();
		expect(html).not.toMatch(/alert|javascript:|onerror|onclick|style=/);
		expect(el.querySelector('img')?.getAttribute('src')).toBe('x.png');
		expect(el.textContent).toContain('styled');
	});

	it('drops forms and iframes', () => {
		const { el } = render('<form action="/x"><input name="a"></form><iframe src="/y"></iframe>');
		expect(el.querySelector('form, input, iframe')).toBeNull();
	});

	it('leaves out Hugging Face front matter', () => {
		expect(stripFrontMatter('---\nlicense: mit\n---\n# Card')).toBe('# Card');
		expect(stripFrontMatter('# No front matter\n---\n')).toBe('# No front matter\n---\n');
		const { el } = render('---\nlicense: mit\ntags: [a]\n---\n\nHello');
		expect(el.textContent?.trim()).toBe('Hello');
	});
});
