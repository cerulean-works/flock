import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import { goto, invalidateAll, resetApp } from '#lib/testing/app.svelte.ts';
import type {
	Cell,
	DatasetFolder,
	DatasetInfo,
	RowsPage,
	TrashEntry,
	VersionsPage
} from '#lib/types.ts';
import CellView from './CellView.svelte';
import DatasetView from './DatasetView.svelte';
import DeleteDatasetDialog from './DeleteDatasetDialog.svelte';
import DeletedDatasets from './DeletedDatasets.svelte';
import FolderView from './FolderView.svelte';
import RemoveTagDialog from './RemoveTagDialog.svelte';
import RenameDialog from './RenameDialog.svelte';
import TagDialog from './TagDialog.svelte';

vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

/** Answers the dashboard's dataset endpoints by URL; each call is recorded. */
function serve(routes: Record<string, (init?: RequestInit, url?: string) => unknown>) {
	const calls: { url: string; init?: RequestInit }[] = [];
	vi.stubGlobal(
		'fetch',
		vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = String(input);
			calls.push({ url, init });
			const route = Object.keys(routes)
				.filter((r) => url.startsWith(r))
				.sort((a, b) => b.length - a.length)[0];
			if (!route) return Response.json({ message: 'not found' }, { status: 404 });
			const body = routes[route](init, url);
			return body instanceof Response ? body : Response.json(body);
		})
	);
	return calls;
}

const body = (init?: RequestInit) => JSON.parse(String(init?.body));

beforeEach(() => resetApp('http://flock.test/datasets/acme/eval/mmlu'));
afterEach(() => vi.unstubAllGlobals());

describe('FolderView', () => {
	const folder: DatasetFolder = {
		kind: 'folder',
		path: 'eval',
		datasets: [{ name: 'mmlu', path: 'eval/mmlu', version: 12, updated_at: 1_790_000_000 }],
		folders: [{ name: 'deep', path: 'eval/deep' }],
		files: [{ name: 'notes.md', key: 'eval/notes.md', size: 2048, last_modified: 1_790_000_000 }],
		cursor: 'next'
	};

	it('lists datasets, then folders, then files, and loads more pages', async () => {
		const calls = serve({
			'/api/datasets/acme/at': () => ({
				...folder,
				datasets: [{ name: 'old', path: 'eval/old', version: null, updated_at: null }],
				folders: [],
				files: [],
				cursor: null
			})
		});
		render(FolderView, { org: 'acme', path: 'eval', folder, now: 1_790_000_100 });
		const names = () =>
			[...document.querySelectorAll('tbody tr td:first-child')].map((td) => td.textContent!.trim());
		expect(names()).toEqual(['mmlu', 'deep/', 'notes.md']);
		// (Version and size columns show on wider screens.)
		await expect.element(screen.getByText('v12')).toBeInTheDocument();
		await expect.element(screen.getByText('2.0 KB')).toBeInTheDocument();
		expect(screen.getByRole('link', { name: 'mmlu' }).element().getAttribute('href')).toBe(
			'/datasets/acme/eval/mmlu'
		);
		// Files open in the Storage tab.
		expect(screen.getByRole('link', { name: 'notes.md' }).element().getAttribute('href')).toBe(
			'/storage/acme/flock-datasets/eval/notes.md'
		);
		await screen.getByRole('button', { name: 'Load more' }).click();
		await vi.waitFor(() => expect(names()).toEqual(['mmlu', 'old', 'deep/', 'notes.md']));
		expect(calls[0].url).toBe('/api/datasets/acme/at?path=eval&cursor=next');
		await expect.element(screen.getByText('Unknown')).toBeInTheDocument();
		await expect.element(screen.getByRole('button', { name: 'Load more' })).not.toBeInTheDocument();
		// A folder isn't a dataset: no S3 path to copy, and no file browser link.
		await expect.element(screen.getByText(/s3:\/\//)).not.toBeInTheDocument();
		await expect
			.element(screen.getByRole('link', { name: 'Browse files' }))
			.not.toBeInTheDocument();
	});

	it('shows how to write a dataset when the bucket is empty', async () => {
		const empty = { ...folder, path: '', datasets: [], folders: [], files: [], cursor: null };
		render(FolderView, { org: 'acme', path: '', folder: empty, now: 0 });
		await expect.element(screen.getByText('No datasets yet.')).toBeVisible();
		await expect.element(screen.getByText('s3://flock-datasets/')).not.toBeInTheDocument();
		await expect.element(screen.getByText(/s3\.credentials\("acme"\)/)).toBeVisible();
		await expect.element(screen.getByRole('link', { name: 'account page' })).toBeVisible();
	});

	it('says when a folder is empty', async () => {
		const empty = { ...folder, datasets: [], folders: [], files: [], cursor: null };
		render(FolderView, { org: 'acme', path: 'eval', folder: empty, now: 0 });
		await expect.element(screen.getByText('This folder is empty.')).toBeVisible();
	});
});

const info: DatasetInfo = {
	path: 'eval/mmlu',
	version: 3,
	timestamp: 1_790_000_000,
	latest_version: 5,
	rows: 1234,
	columns: 2,
	fragments: 4,
	data_size: 5 * 1024 * 1024,
	schema: [
		{
			name: 'id',
			type: 'int64',
			nullable: false,
			metadata: {},
			children: []
		},
		{
			name: 'meta',
			type: 'struct<a: int32>',
			nullable: true,
			metadata: { note: 'kept' },
			children: [{ name: 'a', type: 'int32', nullable: true, metadata: {}, children: [] }]
		}
	],
	metadata: { huggingface: 'x'.repeat(300) },
	binary: false,
	details: {
		data_storage_version: '2.2',
		stable_row_ids: false,
		fragments: 4,
		indices: [{ name: 'id_idx', type: 'BTree', columns: ['id'] }]
	},
	tags: [
		{ name: 'v1.0', version: 3 },
		{ name: 'best', version: 1 }
	]
};

const versions: VersionsPage = {
	latest: 5,
	cursor: null,
	versions: [5, 4, 3, 2, 1].map((v) => ({
		version: v,
		timestamp: 1_790_000_000 - v,
		tags: v === 3 ? ['v1.0'] : v === 1 ? ['best'] : [],
		operation: v === 1 ? 'Overwrite' : 'Append',
		rows: v * 10,
		rows_delta: v === 1 ? null : 10,
		properties: (v === 5
			? { __lance_commit_message: 'more rows', 'flock.source': 'cli' }
			: {}) as Record<string, string>
	}))
};

const s3 = { endpoint: 'http://s3.flock.test', region: 'us-east-1' };

describe('DatasetView', () => {
	const view = (url = 'http://flock.test/datasets/acme/eval/mmlu?version=3') => {
		resetApp(url);
		render(DatasetView, {
			org: 'acme',
			path: 'eval/mmlu',
			info,
			failed: null,
			requested: '3',
			s3,
			now: 1_790_000_100
		});
	};

	it('shows the header numbers, the actions and a newer version', async () => {
		serve({ '/api/datasets/acme/readme': () => ({ text: '# MMLU', truncated: false }) });
		view();
		await expect.element(screen.getByRole('heading', { name: 'mmlu', level: 1 })).toBeVisible();
		await expect.element(screen.getByText('1,234')).toBeVisible();
		await expect.element(screen.getByText('5.0 MB')).toBeVisible();
		await expect.element(screen.getByText('s3://flock-datasets/eval/mmlu')).not.toBeInTheDocument();
		expect(screen.getByRole('link', { name: 'Browse files' }).element().getAttribute('href')).toBe(
			'/storage/acme/flock-datasets/eval/mmlu/'
		);
		await expect.element(screen.getByText('New version available.')).toBeVisible();
		expect(
			screen.getByRole('link', { name: 'View version 5' }).element().getAttribute('href')
		).toBe('/datasets/acme/eval/mmlu?version=5');
		// The README is the default tab.
		await expect.element(screen.getByRole('heading', { name: 'MMLU' })).toBeVisible();
		await expect.element(screen.getByRole('tab', { name: 'Viewer' })).toBeVisible();
	});

	it('shows how to reach the dataset in S3', async () => {
		serve({ '/api/datasets/acme/readme': () => ({ text: '', truncated: false }) });
		view();
		await screen.getByRole('button', { name: 'Access in S3' }).click();
		const dialog = screen.getByRole('dialog');
		await expect
			.element(dialog.getByText('s3://flock-datasets/eval/mmlu', { exact: true }))
			.toBeVisible();
		await expect.element(dialog.getByRole('button', { name: 'Copy S3 URI' })).toBeVisible();
		await expect.element(dialog.getByText('http://s3.flock.test', { exact: true })).toBeVisible();
		const code = dialog.getByRole('code').element().textContent!;
		expect(code).toContain('lance.dataset(\n    "s3://flock-datasets/eval/mmlu",');
		expect(code).toContain('"aws_endpoint": "http://s3.flock.test",');
		expect(code).toContain('"aws_access_key_id": "YOUR_ACCESS_KEY_ID",');
		expect(code).toContain('"allow_http": "true",');
		expect(dialog.getByRole('link', { name: 'account page' }).element().getAttribute('href')).toBe(
			'/account#s3-keys'
		);
	});

	it('shows the README without its front matter, with code highlighted', async () => {
		const text = '---\nlicense: odc-by\n---\n\n# MMLU\n\n```python\nimport lance\n```\n';
		serve({ '/api/datasets/acme/readme': () => ({ text, truncated: false }) });
		view();
		await expect.element(screen.getByRole('heading', { name: 'MMLU' })).toBeVisible();
		await expect.element(screen.getByText('license: odc-by')).not.toBeInTheDocument();
		await expect.element(screen.getByText('import', { exact: true })).toHaveClass('hljs-keyword');
		// This version's numbers sit beside it.
		await expect.element(screen.getByRole('rowheader', { name: 'Rows' })).toBeVisible();
	});

	it('says when there is no README', async () => {
		serve({ '/api/datasets/acme/readme': () => Response.json({ message: 'no' }, { status: 404 }) });
		view();
		await expect
			.element(
				screen.getByText(
					'This dataset has no README. Add a README.md to its folder to describe it.'
				)
			)
			.toBeVisible();
	});

	it('keeps the tab in the URL', async () => {
		serve({ '/api/datasets/acme/readme': () => ({ text: '', truncated: false }) });
		view();
		await screen.getByRole('tab', { name: 'Schema' }).click();
		await vi.waitFor(() =>
			expect(goto).toHaveBeenCalledWith('/datasets/acme/eval/mmlu?version=3&tab=schema', {
				replace: true,
				reset: false
			})
		);
		await expect.element(screen.getByText('id_idx')).toBeVisible();
		await expect.element(screen.getByText('2.2')).toBeVisible();
		// Structs open onto their fields.
		await screen.getByRole('button', { name: 'Show the fields of meta' }).click();
		await expect.element(screen.getByText('int32', { exact: true })).toBeVisible();
		await expect.element(screen.getByText('Show all')).toBeVisible();
	});

	it('reads rows at the version shown, a page at a time, with hidden columns left out', async () => {
		const rows: RowsPage = {
			version: 3,
			offset: 50,
			limit: 50,
			total: 1234,
			columns: [
				{ name: 'id', type: 'int64' },
				{ name: 'meta', type: 'struct<a: int32>' }
			],
			rows: [
				[
					{ k: 'num', v: 50 },
					{ k: 'json', v: '{"a":1}' }
				]
			]
		};
		const calls = serve({ '/api/datasets/acme/rows': () => rows });
		view('http://flock.test/datasets/acme/eval/mmlu?version=3&tab=rows&offset=50');
		await expect.element(screen.getByText('51–51')).toBeVisible();
		await expect.element(screen.getByText('of 1,234')).toBeVisible();
		expect(calls[0].url).toBe(
			'/api/datasets/acme/rows?path=eval%2Fmmlu&version=3&offset=50&limit=50'
		);
		// Pages 1, 2 (this one) and 3, then the last.
		const pages = screen.getByRole('navigation', { name: 'Row pages' }).getByRole('button');
		expect(pages.all().map((b) => b.element().textContent!.trim())).toEqual([
			'Previous',
			'1',
			'2',
			'3',
			'25',
			'Next'
		]);
		await expect
			.element(screen.getByRole('button', { name: 'Page 2', exact: true }))
			.toHaveAttribute('aria-current', 'page');
		await screen.getByRole('button', { name: 'Next page' }).click();
		expect(goto).toHaveBeenLastCalledWith(
			'/datasets/acme/eval/mmlu?version=3&tab=rows&offset=100',
			{ reset: false }
		);
		await screen.getByRole('button', { name: 'Page 25' }).click();
		expect(goto).toHaveBeenLastCalledWith(
			'/datasets/acme/eval/mmlu?version=3&tab=rows&offset=1200',
			{ reset: false }
		);
		await screen.getByRole('button', { name: 'Page 1', exact: true }).click();
		expect(goto).toHaveBeenLastCalledWith('/datasets/acme/eval/mmlu?version=3&tab=rows', {
			reset: false
		});

		// The page moved on (the URL is the page's), and a hidden column isn't asked for.
		await screen.getByRole('button', { name: 'Columns' }).click();
		await screen.getByRole('checkbox').nth(1).click();
		await vi.waitFor(() =>
			expect(calls.at(-1)!.url).toBe(
				'/api/datasets/acme/rows?path=eval%2Fmmlu&version=3&offset=0&limit=50&columns=id'
			)
		);
	});

	it('cuts a long column type short so it doesn’t widen the column', async () => {
		const type =
			'struct<label: string, simplified_evidence: int64, traditional_evidence: int64, ' +
			'han_characters: int64, minority_fraction: double, reason: string, version: string>';
		const rows: RowsPage = {
			version: 3,
			offset: 0,
			limit: 50,
			total: 1,
			columns: [{ name: 'script', type }],
			rows: [[{ k: 'json', v: '{"label":"simplified"}' }]]
		};
		serve({ '/api/datasets/acme/rows': () => rows });
		view('http://flock.test/datasets/acme/eval/mmlu?version=3&tab=rows');
		const label = screen.getByRole('table').getByText(type, { exact: true });
		await expect.element(label).toBeVisible();
		expect(label.element().getBoundingClientRect().width).toBeLessThanOrEqual(192);
		expect(label.element().closest('th')!.clientWidth).toBeLessThan(300);
	});

	it('opens a row in place with its cells in full, and closes it again', async () => {
		const rows: RowsPage = {
			version: 3,
			offset: 0,
			limit: 50,
			total: 2,
			columns: [
				{ name: 'text', type: 'string' },
				{ name: 'emb', type: 'fixed_size_list<float>[4]' },
				{ name: 'meta', type: 'struct<a: int32>' }
			],
			rows: [
				[
					{ k: 'str', v: 'a'.repeat(500), len: 900 },
					{ k: 'vec', dtype: 'float', len: 4, head: [0.123456, 1] },
					{ k: 'json', v: '{"a":1}' }
				],
				[{ k: 'str', v: 'short' }, { k: 'null' }, { k: 'null' }]
			]
		};
		const full: Record<string, Cell> = {
			text: { k: 'str', v: 'a'.repeat(899) + 'z' },
			emb: { k: 'json', v: '[0.123456,1,2,3]' }
		};
		const calls = serve({
			'/api/datasets/acme/rows': () => rows,
			'/api/datasets/acme/cell': (_, url) => {
				const column = new URL(url!, 'http://flock.test').searchParams.get('column')!;
				return { row: 0, column, type: 'x', cell: full[column] };
			}
		});
		view('http://flock.test/datasets/acme/eval/mmlu?version=3&tab=rows');
		await expect.element(screen.getByText('900 characters')).toBeVisible();
		await expect.element(screen.getByText(/0\.1235, 1, …/)).toBeVisible();

		// Clicking anywhere on the row opens it, reading only the cells that were cut.
		await screen.getByText('900 characters').click();
		await expect.element(screen.getByText(/a+z/)).toBeVisible();
		await expect.element(screen.getByText(/0\.123456, 1, 2, 3/)).toBeVisible();
		await expect.element(screen.getByText(/"a": 1/)).toBeVisible();
		const cells = calls.filter((c) => c.url.startsWith('/api/datasets/acme/cell?'));
		expect(cells.map((c) => c.url)).toEqual([
			'/api/datasets/acme/cell?path=eval%2Fmmlu&version=3&row=0&column=text',
			'/api/datasets/acme/cell?path=eval%2Fmmlu&version=3&row=0&column=emb'
		]);
		await expect
			.element(screen.getByRole('button', { name: 'Close row 0' }))
			.toHaveAttribute('aria-expanded', 'true');

		// And again closes it, back to the cut cells.
		await screen.getByRole('button', { name: 'Close row 0' }).click();
		await expect.element(screen.getByText(/a+z/)).not.toBeInTheDocument();
		await expect.element(screen.getByText('900 characters')).toBeVisible();
		await expect
			.element(screen.getByRole('button', { name: 'Open row 0' }))
			.toHaveAttribute('aria-expanded', 'false');
	});

	it('opens images in a dialog with their size and format', async () => {
		// A 2 × 1 PNG.
		const png =
			'iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAIAAAB7QOjdAAAADUlEQVR4nGP4zwAE/wEHAAH/4iOeWQAAAABJRU5ErkJggg==';
		serve({
			'/api/datasets/acme/rows': () => ({
				version: 3,
				offset: 0,
				limit: 10,
				total: 1,
				columns: [{ name: 'image', type: 'binary' }],
				rows: [[{ k: 'image', mime: 'image/png', size: 2048, data: png }]]
			})
		});
		view('http://flock.test/datasets/acme/eval/mmlu?version=3&tab=rows');
		await screen.getByRole('button', { name: 'PNG image, 2.0 KB' }).click();
		const dialog = screen.getByRole('dialog');
		await expect.element(dialog.getByRole('img', { name: 'image, row 0' })).toBeVisible();
		await expect.element(dialog.getByText('2 × 1 pixels')).toBeVisible();
		await expect.element(dialog.getByText('2.0 KB')).toBeVisible();
		await expect.element(dialog.getByText('PNG', { exact: true })).toBeVisible();
		// The click opened the image, not the row.
		await expect.element(screen.getByRole('button', { name: 'Open row 0' })).toBeInTheDocument();
	});

	it('picks versions and tags from the picker, kept in the URL', async () => {
		serve({
			'/api/datasets/acme/readme': () => ({ text: '', truncated: false }),
			'/api/datasets/acme/versions': () => versions
		});
		view();
		await screen.getByRole('button', { name: /Version 3/ }).click();
		const menu = screen.getByRole('menu');
		await expect.element(menu.getByText('latest')).toBeVisible();
		await menu.getByRole('menuitem', { name: /Version 2/ }).click();
		expect(goto).toHaveBeenLastCalledWith('/datasets/acme/eval/mmlu?version=2', { reset: false });
		await screen.getByRole('button', { name: /Version 3/ }).click();
		await screen.getByRole('menuitem', { name: /^best/ }).click();
		expect(goto).toHaveBeenLastCalledWith('/datasets/acme/eval/mmlu?version=best', {
			reset: false
		});
	});

	it('shows the history with its tags, and views a version', async () => {
		serve({
			'/api/datasets/acme/versions': () => versions,
			'/api/datasets/acme/info': () => info
		});
		view('http://flock.test/datasets/acme/eval/mmlu?version=3&tab=versions');
		await expect.element(screen.getByText('more rows')).toBeVisible();
		await expect.element(screen.getByText('+10 rows').first()).toBeVisible();
		expect(
			screen.getByRole('link', { name: 'View version 4' }).element().getAttribute('href')
		).toBe('/datasets/acme/eval/mmlu?version=4&tab=versions');
		await expect.element(screen.getByRole('button', { name: 'Remove the tag v1.0' })).toBeVisible();
		const current = document.querySelector('tr[aria-current="true"]');
		expect(current?.textContent).toContain('v1.0');
		// The commit message reads as text; other properties keep their keys.
		await expect.element(screen.getByText('__lance_commit_message')).not.toBeInTheDocument();
		await expect.element(screen.getByText('flock.source')).toBeVisible();
		// Adding a tag sits in the row's menu, and removing one warns first.
		await screen.getByRole('button', { name: 'Version 5 actions' }).click();
		await expect.element(screen.getByRole('menuitem', { name: 'Add tag…' })).toBeVisible();
	});

	it('warns before removing a tag', async () => {
		render(RemoveTagDialog, {
			open: true,
			org: 'acme',
			path: 'eval/mmlu',
			tag: 'v1.0',
			version: 3,
			onremoved: vi.fn()
		});
		await expect
			.element(
				screen.getByText(/Scripts and jobs that load this dataset by v1.0 will stop working/)
			)
			.toBeVisible();
	});

	it('shows Lance’s error in place of the tabs', async () => {
		serve({});
		resetApp('http://flock.test/datasets/acme/eval/mmlu?tab=rows');
		render(DatasetView, {
			org: 'acme',
			path: 'eval/mmlu',
			info: null,
			failed: 'Lance couldn’t read the dataset: missing data file',
			requested: null,
			s3,
			now: 0
		});
		await expect
			.element(screen.getByRole('alert'))
			.toHaveTextContent('Lance couldn’t read the dataset: missing data file');
		await expect.element(screen.getByRole('button', { name: 'Delete' })).toBeVisible();
	});
});

describe('CellView', () => {
	const cases: [Cell, string][] = [
		[{ k: 'null' }, 'null'],
		[{ k: 'num', v: 1234 }, '1,234'],
		[{ k: 'num', v: 1, label: 'dog' }, '1 dog'],
		[{ k: 'num', v: 'nan' }, 'nan'],
		[{ k: 'bool', v: false }, 'false'],
		[{ k: 'time', v: '2023-11-15T06:13:20+08:00' }, '2023-11-15T06:13:20+08:00'],
		[{ k: 'str', v: 'short' }, 'short'],
		[{ k: 'bin', size: 2048 }, '2.0 KB'],
		[{ k: 'blob', size: 3000 }, 'blob, 2.9 KB'],
		[{ k: 'vec', dtype: 'float', len: 768, head: [0.5, 1.25] }, 'float[768] 0.5, 1.25, …'],
		[{ k: 'json', v: '[1,2]' }, '[1,2]']
	];

	for (const [cell, text] of cases) {
		it(`shows ${cell.k} as ${text}`, async () => {
			const { container } = render(CellView, { cell });
			await vi.waitFor(() => expect(container.textContent!.replace(/\s+/g, ' ').trim()).toBe(text));
		});
	}

	it('shows images as thumbnails that open', async () => {
		const onopen = vi.fn();
		render(CellView, {
			cell: { k: 'image', mime: 'image/png', size: 70, data: 'iVBORw0KGgo=' },
			onopen
		});
		const img = screen.getByRole('img', { name: 'PNG image, 70 B' });
		expect(img.element().getAttribute('src')).toBe('data:image/png;base64,iVBORw0KGgo=');
		await screen.getByRole('button').click();
		expect(onopen).toHaveBeenCalled();
	});

	it('cuts long text and says how long it is', async () => {
		render(CellView, { cell: { k: 'str', v: 'a'.repeat(500), len: 1200 } });
		await expect.element(screen.getByText('1,200 characters')).toBeVisible();
		await expect.element(screen.getByText(/^a+…$/)).toHaveClass('line-clamp-4');
	});

	it('shows all of a cell in an opened row', async () => {
		render(CellView, { cell: { k: 'json', v: '{"a":[1,2]}' }, full: true });
		await expect.element(screen.getByText(/"a": \[/)).not.toHaveClass('line-clamp-4');
	});
});

describe('the dialogs', () => {
	it('deletes after the path is typed, then goes to the nearest folder left', async () => {
		const calls = serve({
			'/api/datasets/acme/delete': () => ({ id: 1 }),
			// eval/deep held only this dataset, so it's gone too.
			'/api/datasets/acme/at?path=eval%2Fdeep': () =>
				Response.json({ message: 'folder eval/deep not found' }, { status: 404 }),
			'/api/datasets/acme/at?path=eval': () => ({ kind: 'folder' })
		});
		render(DeleteDatasetDialog, { open: true, org: 'acme', path: 'eval/deep/mmlu' });
		await expect.element(screen.getByText('Org admins can restore it for 14 days.')).toBeVisible();
		const confirm = screen.getByRole('button', { name: 'Delete dataset' });
		await expect.element(confirm).toBeDisabled();
		await screen.getByLabelText('Type eval/deep/mmlu to confirm').fill('eval/deep/mmlu');
		await confirm.click();
		await vi.waitFor(() =>
			expect(goto).toHaveBeenCalledWith('/datasets/acme/eval', { replace: true })
		);
		expect(body(calls[0].init)).toEqual({ path: 'eval/deep/mmlu' });
	});

	it('renames, showing a refusal and then going to the new path', async () => {
		let taken = true;
		const calls = serve({
			'/api/datasets/acme/rename': () =>
				taken
					? Response.json(
							{ message: 'Something already exists at eval/mmlu.', taken: true },
							{ status: 409 }
						)
					: { path: 'eval/mmlu' }
		});
		render(RenameDialog, { open: true, org: 'acme', path: 'scratch/mmlu' });
		await expect
			.element(
				screen.getByText('Anything still writing to the old path will create a new dataset there.')
			)
			.toBeVisible();
		const input = screen.getByLabelText('New path');
		await expect.element(input).toHaveValue('scratch/mmlu');
		await input.fill('eval/m m');
		await screen.getByRole('button', { name: 'Rename' }).click();
		await expect.element(screen.getByRole('alert')).toBeVisible();
		expect(calls).toEqual([]);
		await input.fill('eval/mmlu');
		await screen.getByRole('button', { name: 'Rename' }).click();
		await expect
			.element(screen.getByRole('alert'))
			.toHaveTextContent('Something already exists at eval/mmlu.');
		taken = false;
		await screen.getByRole('button', { name: 'Rename' }).click();
		await vi.waitFor(() =>
			expect(goto).toHaveBeenCalledWith('/datasets/acme/eval/mmlu', { replace: true })
		);
		expect(body(calls.at(-1)!.init)).toEqual({ path: 'scratch/mmlu', to: 'eval/mmlu' });
	});

	it('tags a version, offering to move a tag that is elsewhere', async () => {
		const calls = serve({
			'/api/datasets/acme/tags/v1.0': (init) =>
				body(init).move
					? { tag: 'v1.0', version: 7 }
					: Response.json({ message: 'v1.0 is on version 3.', version: 3 }, { status: 409 })
		});
		const ontagged = vi.fn();
		render(TagDialog, { open: true, org: 'acme', path: 'eval/mmlu', version: 7, ontagged });
		await screen.getByLabelText('Name').fill('123');
		await screen.getByRole('button', { name: 'Add tag' }).click();
		await expect.element(screen.getByRole('alert')).toBeVisible();
		await screen.getByLabelText('Name').fill('v1.0');
		await screen.getByRole('button', { name: 'Add tag' }).click();
		await expect
			.element(screen.getByText('v1.0 is on version 3. Move it to version 7?'))
			.toBeVisible();
		await screen.getByRole('button', { name: 'Move tag' }).click();
		await vi.waitFor(() => expect(ontagged).toHaveBeenCalled());
		expect(calls.map((c) => [c.init?.method, body(c.init)])).toEqual([
			['PUT', { path: 'eval/mmlu', version: 7, move: false }],
			['PUT', { path: 'eval/mmlu', version: 7, move: true }]
		]);
	});
});

describe('DeletedDatasets', () => {
	const now = 1_790_000_000;
	const entry = (id: number, path: string, days: number, moved = true): TrashEntry => ({
		id,
		path,
		deleted_by: { id: 2, name: 'Alice', email: 'alice@acme.test', avatar_url: null },
		deleted_at: now - (14 - days) * 86_400,
		moved_at: moved ? now : null,
		expires_at: now + days * 86_400 - 60,
		latest_version: 3,
		objects: 12,
		bytes: 3 * 1024 * 1024
	});

	it('lists deleted datasets with the days they have left', async () => {
		serve({});
		render(DeletedDatasets, {
			org: 'acme',
			entries: [entry(1, 'eval/mmlu', 13), entry(2, 'scratch/x', 1), entry(3, 'moving', 14, false)],
			now
		});
		await expect.element(screen.getByText('13 days left')).toBeVisible();
		await expect.element(screen.getByText('1 day left')).toBeVisible();
		await expect.element(screen.getByText(/3\.0 MB · deleted .* by Alice/).first()).toBeVisible();
		await expect.element(screen.getByText('Still being moved')).toBeVisible();
		expect(screen.getByRole('button', { name: 'Restore' }).all()).toHaveLength(2);
	});

	it('restores, asking for another path when the old one is taken', async () => {
		let attempt = 0;
		const calls = serve({
			'/api/datasets/acme/trash/1/restore': () =>
				attempt++ === 0
					? Response.json(
							{ message: 'Something already exists at eval/mmlu.', taken: true },
							{ status: 409 }
						)
					: { path: 'eval/mmlu-restored' }
		});
		render(DeletedDatasets, { org: 'acme', entries: [entry(1, 'eval/mmlu', 13)], now });
		await screen.getByRole('button', { name: 'Restore' }).click();
		const dialog = screen.getByRole('dialog');
		await dialog.getByRole('button', { name: 'Restore' }).click();
		await expect
			.element(
				dialog.getByText('Something already exists at eval/mmlu. Restore it to another path.')
			)
			.toBeVisible();
		await expect.element(dialog.getByLabelText('Path')).toHaveValue('eval/mmlu-restored');
		await dialog.getByRole('button', { name: 'Restore' }).click();
		await vi.waitFor(() => expect(invalidateAll).toHaveBeenCalled());
		expect(calls.map((c) => body(c.init))).toEqual([{}, { to: 'eval/mmlu-restored' }]);
	});

	it('deletes for good after the path is typed', async () => {
		const calls = serve({
			'/api/datasets/acme/trash/1': () => new Response(null, { status: 204 })
		});
		render(DeletedDatasets, { org: 'acme', entries: [entry(1, 'eval/mmlu', 13)], now });
		await screen.getByRole('button', { name: 'Delete now' }).click();
		const confirm = screen.getByRole('button', { name: 'Delete for good' });
		await expect.element(confirm).toBeDisabled();
		await screen.getByLabelText('Type eval/mmlu to confirm').fill('eval/mmlu');
		await confirm.click();
		await vi.waitFor(() => expect(calls[0]?.init?.method).toBe('DELETE'));
		expect(calls[0].url).toBe('/api/datasets/acme/trash/1');
	});

	it('says when there are none', async () => {
		serve({});
		render(DeletedDatasets, { org: 'acme', entries: [], now });
		await expect.element(screen.getByText('No deleted datasets.')).toBeVisible();
	});
});
