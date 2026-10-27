import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen, userEvent } from 'vitest/browser';
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
	});

	it('shows how to write a dataset when the bucket is empty', async () => {
		const empty = { ...folder, path: '', datasets: [], folders: [], files: [], cursor: null };
		render(FolderView, { org: 'acme', path: '', folder: empty, now: 0 });
		await expect.element(screen.getByText('No datasets yet.')).toBeVisible();
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
		properties: (v === 5 ? { message: 'more rows' } : {}) as Record<string, string>
	}))
};

describe('DatasetView', () => {
	const view = (url = 'http://flock.test/datasets/acme/eval/mmlu?version=3') => {
		resetApp(url);
		render(DatasetView, {
			org: 'acme',
			path: 'eval/mmlu',
			info,
			failed: null,
			requested: '3',
			now: 1_790_000_100
		});
	};

	it('shows the header numbers, the actions and a newer version', async () => {
		serve({ '/api/datasets/acme/readme': () => ({ text: '# MMLU', truncated: false }) });
		view();
		await expect.element(screen.getByRole('heading', { name: 'mmlu', level: 1 })).toBeVisible();
		await expect.element(screen.getByText('1,234')).toBeVisible();
		await expect.element(screen.getByText('5.0 MB')).toBeVisible();
		await expect.element(screen.getByText('s3://flock-datasets/eval/mmlu')).toBeVisible();
		await expect.element(screen.getByRole('button', { name: 'Copy S3 URI' })).toBeVisible();
		expect(screen.getByRole('link', { name: 'Browse files' }).element().getAttribute('href')).toBe(
			'/storage/acme/flock-datasets/eval/mmlu/'
		);
		await expect.element(screen.getByText('New version available.')).toBeVisible();
		expect(
			screen.getByRole('link', { name: 'View version 5' }).element().getAttribute('href')
		).toBe('/datasets/acme/eval/mmlu?version=5');
		// The README is the default tab.
		await expect.element(screen.getByRole('heading', { name: 'MMLU' })).toBeVisible();
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
					{ k: 'json', v: '{"a":1' + '0'.repeat(494), len: 900 }
				]
			]
		};
		const calls = serve({
			'/api/datasets/acme/rows': () => rows,
			'/api/datasets/acme/cell': () => ({
				row: 50,
				column: 'meta',
				type: 'struct<a: int32>',
				cell: { k: 'json', v: '{"a":1}' }
			})
		});
		view('http://flock.test/datasets/acme/eval/mmlu?version=3&tab=rows&offset=50');
		await expect.element(screen.getByText('51–51')).toBeVisible();
		expect(calls[0].url).toBe(
			'/api/datasets/acme/rows?path=eval%2Fmmlu&version=3&offset=50&limit=50'
		);
		await screen.getByRole('button', { name: 'Next page' }).click();
		expect(goto).toHaveBeenLastCalledWith(
			'/datasets/acme/eval/mmlu?version=3&tab=rows&offset=100',
			{ reset: false }
		);
		// A cut cell opens in full in the side sheet.
		await screen.getByRole('button', { name: '900 characters' }).click();
		await vi.waitFor(() =>
			expect(calls.some((c) => c.url.startsWith('/api/datasets/acme/cell?'))).toBe(true)
		);
		await expect.element(screen.getByRole('dialog').getByText('"a": 1')).toBeVisible();
		await userEvent.keyboard('{Escape}');

		// The page moved on (the URL is the page's), and a hidden column isn't asked for.
		await screen.getByRole('button', { name: 'Columns' }).click();
		await screen.getByRole('checkbox').nth(1).click();
		await vi.waitFor(() =>
			expect(calls.at(-1)!.url).toBe(
				'/api/datasets/acme/rows?path=eval%2Fmmlu&version=3&offset=100&limit=50&columns=id'
			)
		);
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
		const onopen = vi.fn();
		render(CellView, { cell: { k: 'str', v: 'a'.repeat(500), len: 1200 }, onopen });
		await screen.getByRole('button', { name: '1,200 characters' }).click();
		expect(onopen).toHaveBeenCalled();
	});
});

describe('the dialogs', () => {
	it('deletes after the path is typed, then goes to the folder', async () => {
		const calls = serve({ '/api/datasets/acme/delete': () => ({ id: 1 }) });
		render(DeleteDatasetDialog, { open: true, org: 'acme', path: 'eval/mmlu' });
		await expect.element(screen.getByText('Org admins can restore it for 14 days.')).toBeVisible();
		const confirm = screen.getByRole('button', { name: 'Delete dataset' });
		await expect.element(confirm).toBeDisabled();
		await screen.getByLabelText('Type eval/mmlu to confirm').fill('eval/mmlu');
		await confirm.click();
		await vi.waitFor(() =>
			expect(goto).toHaveBeenCalledWith('/datasets/acme/eval', { replace: true })
		);
		expect(body(calls[0].init)).toEqual({ path: 'eval/mmlu' });
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
