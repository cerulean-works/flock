import { afterEach, describe, expect, it, vi } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import type { Listing, ObjectDetails } from '#lib/types.ts';
import Breadcrumbs from './Breadcrumbs.svelte';
import DeleteObjectsDialog from './DeleteObjectsDialog.svelte';
import ObjectBrowser from './ObjectBrowser.svelte';
import ObjectPreview from './ObjectPreview.svelte';
import { crumbs } from '#lib/storage.ts';

/** Answers fetches by URL: the dashboard's endpoints, and the proxy's presigned URLs. */
function serve(routes: Record<string, (init?: RequestInit) => unknown>) {
	const calls: { url: string; init?: RequestInit }[] = [];
	vi.stubGlobal(
		'fetch',
		vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = String(input);
			calls.push({ url, init });
			const route = Object.keys(routes).find((r) => url.startsWith(r));
			if (!route) return new Response('{"message":"not found"}', { status: 404 });
			const body = routes[route](init);
			return body instanceof Response ? body : Response.json(body);
		})
	);
	return calls;
}

afterEach(() => vi.unstubAllGlobals());

const object = (key: string, size = 10) => ({ key, size, last_modified: 1_790_000_000, etag: 'e' });

describe('Breadcrumbs', () => {
	it('links every level, the page itself last', async () => {
		render(Breadcrumbs, { crumbs: crumbs('acme', 'Acme Lab', 'data', 'runs/v1/') });
		const links = page.getByRole('link').all();
		expect(links.map((l) => l.element().textContent?.trim())).toEqual([
			'Storage',
			'Acme Lab',
			'data',
			'runs',
			'v1'
		]);
		expect(links.at(-1)!.element().getAttribute('aria-current')).toBe('page');
		expect(links.at(-1)!.element().getAttribute('href')).toBe('/storage/acme/data/runs/v1/');
	});
});

describe('ObjectBrowser', () => {
	const listing: Listing = {
		prefix: 'runs/',
		folders: ['runs/v1/'],
		objects: [object('runs/a.txt'), object('runs/b.json', 2048), object('runs/c.png')],
		cursor: 'next'
	};

	it('lists folders first, then loads more pages', async () => {
		const calls = serve({
			'/api/storage/acme/data/objects': () => ({
				prefix: 'runs/',
				folders: [],
				objects: [object('runs/d.md')],
				cursor: null
			})
		});
		render(ObjectBrowser, {
			org: 'acme',
			bucket: 'data',
			prefix: 'runs/',
			listing,
			now: 1_790_000_100
		});
		const rows = () =>
			[...document.querySelectorAll('tbody tr')].map((r) =>
				r.querySelector('td:nth-child(2)')!.textContent!.trim()
			);
		expect(rows()).toEqual(['v1/', 'a.txt', 'b.json', 'c.png']);
		await expect.element(page.getByText('2.0 KB')).toBeInTheDocument();
		await page.getByRole('button', { name: 'Load more' }).click();
		await vi.waitFor(() => expect(rows()).toEqual(['v1/', 'a.txt', 'b.json', 'c.png', 'd.md']));
		expect(calls[0].url).toBe(
			'/api/storage/acme/data/objects?prefix=runs%2F&limit=200&cursor=next'
		);
		await expect.element(page.getByRole('button', { name: 'Load more' })).not.toBeInTheDocument();
	});

	it('filters by name prefix through the API', async () => {
		const calls = serve({
			'/api/storage/acme/data/objects': () => ({
				...listing,
				objects: [object('runs/b.json')],
				folders: [],
				cursor: null
			})
		});
		render(ObjectBrowser, { org: 'acme', bucket: 'data', prefix: 'runs/', listing, now: 0 });
		await page.getByRole('textbox', { name: 'Filter by name prefix' }).fill('b');
		await vi.waitFor(() => expect(document.querySelectorAll('tbody tr').length).toBe(1));
		expect(calls.at(-1)!.url).toBe('/api/storage/acme/data/objects?prefix=runs%2Fb&limit=200');
	});

	it('selects rows, shift-click selecting a range, and deletes them', async () => {
		const calls = serve({
			'/api/storage/acme/data/delete': () => ({ deleted: 5 }),
			'/api/storage/acme/data/objects': () => ({ ...listing, cursor: null })
		});
		render(ObjectBrowser, { org: 'acme', bucket: 'data', prefix: 'runs/', listing, now: 0 });
		await page.getByRole('checkbox', { name: 'Select v1' }).click();
		await userEvent.keyboard('{Shift>}');
		await page.getByRole('checkbox', { name: 'Select b.json' }).click();
		await userEvent.keyboard('{/Shift}');
		await expect.element(page.getByText('3 selected')).toBeVisible();
		// Download is for one object; folders are deleted with what's in them.
		await expect.element(page.getByRole('button', { name: 'Download' })).not.toBeInTheDocument();
		await page.getByRole('toolbar').getByRole('button', { name: 'Delete' }).click();
		const dialog = page.getByRole('alertdialog');
		await expect.element(dialog.getByText('Delete 2 objects and 1 folder?')).toBeVisible();
		await dialog.getByRole('button', { name: 'Delete' }).click();
		await vi.waitFor(() => expect(calls.some((c) => c.url.endsWith('/delete'))).toBe(true));
		const sent = calls.find((c) => c.url.endsWith('/delete'))!;
		expect(JSON.parse(String(sent.init!.body))).toEqual({
			keys: ['runs/a.txt', 'runs/b.json'],
			prefixes: ['runs/v1/']
		});
	});
});

describe('ObjectBrowser in flock-models', () => {
	it('reads files but leaves changes to flock models push and the Models tab', async () => {
		serve({});
		const listing: Listing = {
			prefix: 'm/u/01JD/',
			folders: [],
			objects: [object('m/u/01JD/config.json')],
			cursor: null
		};
		render(ObjectBrowser, {
			org: 'acme',
			bucket: 'flock-models',
			prefix: 'm/u/01JD/',
			listing,
			now: 0
		});
		await expect.element(page.getByText('config.json')).toBeVisible();
		await expect.element(page.getByRole('button', { name: 'Upload' })).not.toBeInTheDocument();
		await expect
			.element(page.getByRole('button', { name: 'Create folder' }))
			.not.toBeInTheDocument();
		expect(page.getByRole('link', { name: 'Models' }).element().getAttribute('href')).toBe(
			'/models/acme'
		);
		await page.getByRole('checkbox', { name: 'Select config.json' }).click();
		await expect.element(page.getByRole('button', { name: 'Download' })).toBeVisible();
		await expect
			.element(page.getByRole('toolbar').getByRole('button', { name: 'Delete' }))
			.not.toBeInTheDocument();
	});
});

describe('DeleteObjectsDialog', () => {
	it('shows a refusal, such as the folder cap, and stays open', async () => {
		serve({
			'/api/storage/acme/data/delete': () =>
				Response.json(
					{
						message:
							'This would delete more than 10,000 objects. Use aws s3 rm --recursive instead.'
					},
					{ status: 409 }
				)
		});
		const ondeleted = vi.fn();
		render(DeleteObjectsDialog, {
			open: true,
			org: 'acme',
			bucket: 'data',
			keys: [],
			prefixes: ['huge/'],
			ondeleted
		});
		await expect.element(page.getByText('Delete huge/?')).toBeVisible();
		await page.getByRole('button', { name: 'Delete' }).click();
		await expect.element(page.getByRole('alert')).toHaveTextContent(/more than 10,000 objects/i);
		expect(ondeleted).not.toHaveBeenCalled();
	});
});

describe('ObjectPreview', () => {
	const details = (key: string, content_type: string | null, size = 100): ObjectDetails => ({
		key,
		size,
		last_modified: 0,
		etag: 'e',
		content_type,
		metadata: {}
	});

	function presigned(body = '') {
		return serve({
			'/api/storage/acme/data/presign': () => ({ url: 'http://s3.flock.test/data/x?sig' }),
			'http://s3.flock.test/': () => new Response(body)
		});
	}

	it('shows SVG only in an <img>, so its scripts never run', async () => {
		const calls = presigned();
		render(ObjectPreview, {
			org: 'acme',
			bucket: 'data',
			object: details('evil.svg', 'image/svg+xml'),
			ondownload: () => {}
		});
		await expect.element(page.getByRole('img', { name: 'evil.svg' })).toBeInTheDocument();
		expect(document.querySelector('svg circle, iframe, object, embed')).toBeNull();
		expect(JSON.parse(String(calls[0].init!.body))).toEqual({
			method: 'get',
			key: 'evil.svg',
			disposition: 'inline',
			content_type: 'image/svg+xml'
		});
		// The image's bytes are never fetched into the page.
		expect(calls.length).toBe(1);
	});

	it('reads text with a Range request and says when it is cut', async () => {
		const calls = presigned('line one\nline two');
		const ondownload = vi.fn();
		render(ObjectPreview, {
			org: 'acme',
			bucket: 'data',
			object: details('train.log', 'text/plain', 3 * 1024 * 1024),
			ondownload
		});
		await expect.element(page.getByText(/line one/)).toBeVisible();
		expect(new Headers(calls[1].init!.headers).get('range')).toBe('bytes=0-1048575');
		await expect.element(page.getByText('Showing the first 1.0 MB of 3.0 MB.')).toBeVisible();
		await page.getByRole('button', { name: 'Download the whole file' }).click();
		expect(ondownload).toHaveBeenCalled();
	});

	it('pretty-prints JSON', async () => {
		presigned('{"lr":0.1,"layers":[1,2]}');
		render(ObjectPreview, {
			org: 'acme',
			bucket: 'data',
			object: details('config.json', 'application/json'),
			ondownload: () => {}
		});
		await vi.waitFor(() =>
			expect(document.querySelector('pre')?.textContent).toBe(
				'{\n  "lr": 0.1,\n  "layers": [\n    1,\n    2\n  ]\n}'
			)
		);
	});

	it('renders Markdown without anything that runs', async () => {
		presigned('# Notes\n\n<img src=x onerror="window.__ran=1"><script>window.__ran=1</script>');
		render(ObjectPreview, {
			org: 'acme',
			bucket: 'data',
			object: details('README.md', null),
			ondownload: () => {}
		});
		await expect.element(page.getByRole('heading', { name: 'Notes' })).toBeVisible();
		const html = document.querySelector('.markdown')!.innerHTML;
		expect(html).not.toMatch(/onerror|<script/);
		expect((window as unknown as { __ran?: number }).__ran).toBeUndefined();
	});

	it('shows PDFs in the browser viewer', async () => {
		presigned();
		render(ObjectPreview, {
			org: 'acme',
			bucket: 'data',
			object: details('paper.pdf', 'binary/octet-stream'),
			ondownload: () => {}
		});
		await vi.waitFor(() =>
			expect(document.querySelector('iframe')?.getAttribute('src')).toBe(
				'http://s3.flock.test/data/x?sig'
			)
		);
	});

	it('names the proxy when the browser can’t reach it', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(async (input: RequestInfo | URL) => {
				if (String(input).startsWith('/api/'))
					return Response.json({ url: 'http://s3.flock.test:8090/data/x?sig' });
				throw new TypeError('Failed to fetch');
			})
		);
		render(ObjectPreview, {
			org: 'acme',
			bucket: 'data',
			object: details('notes.txt', 'text/plain'),
			ondownload: () => {}
		});
		await expect
			.element(page.getByRole('alert'))
			.toHaveTextContent('Couldn’t reach the S3 proxy at http://s3.flock.test:8090');
	});

	it('offers only the download for other files', async () => {
		const calls = presigned();
		render(ObjectPreview, {
			org: 'acme',
			bucket: 'data',
			object: details('data.parquet', null),
			ondownload: () => {}
		});
		await expect.element(page.getByText(/This file type can’t be previewed/)).toBeVisible();
		expect(calls).toEqual([]);
	});
});
