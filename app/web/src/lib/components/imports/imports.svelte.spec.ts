import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import { invalidateAll, resetApp } from '#lib/testing/app.svelte.ts';
import { HFImportList } from '#lib/hf.svelte.ts';
import type { HFDatasetPreview, HFImport } from '#lib/types.ts';
import HFDatasetDialog from './HFDatasetDialog.svelte';
import HFImportRows from './HFImportRows.svelte';
import HFModelDialog from './HFModelDialog.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

/** Answers the dashboard's import endpoints by URL; each call is recorded. */
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
const now = Date.now() / 1000;

const job = (fields: Partial<HFImport> = {}): HFImport => ({
	id: 7,
	kind: 'dataset',
	repo: 'acme/data',
	revision: 'main',
	sha: null,
	dest: 'web/data',
	state: 'running',
	phase: 'downloading',
	bytes_total: 4 * 1024 * 1024,
	bytes_done: 1024 * 1024,
	files_total: 2,
	files_done: 0,
	rows_done: 0,
	progress: 0.125,
	result: null,
	error: null,
	command: 'flock datasets import acme/data web/data --config default --org acme',
	started_by: null,
	started_at: now - 60,
	finished_at: null,
	...fields
});

beforeEach(() => {
	resetApp('http://flock.test/datasets/acme/web');
	localStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());

describe('HFImportRows', () => {
	it('shows a running import with its progress, and cancels it', async () => {
		const calls = serve({
			'/api/imports/acme/hf/7': () => job({ state: 'cancelled', finished_at: now }),
			'/api/imports/acme/hf': () => ({ imports: [job(), job({ id: 8, dest: 'other' })] })
		});
		const list = new HFImportList('acme');
		const stop = list.watch();
		render(HFImportRows, { org: 'acme', list, kind: 'dataset', folder: 'web' });
		await expect.element(screen.getByText('Downloading. 1.0 MB of 4.0 MB')).toBeVisible();
		await expect.element(screen.getByText('other')).not.toBeInTheDocument();
		const bar = screen.getByRole('progressbar');
		expect(bar.element().getAttribute('aria-valuenow')).toBe('13');
		await screen.getByRole('button', { name: 'Cancel' }).click();
		await expect.element(screen.getByText('The import of acme/data was cancelled.')).toBeVisible();
		expect(calls.at(-1)?.init?.method).toBe('DELETE');
		stop();
	});

	it('links a finished import, reads the page again, and hides it', async () => {
		let state: HFImport['state'] = 'running';
		serve({
			'/api/imports/acme/hf': () => ({
				imports: [
					job({
						state,
						finished_at: state === 'running' ? null : now,
						result: state === 'running' ? null : { datasets: [] }
					})
				]
			})
		});
		const list = new HFImportList('acme');
		const stop = list.watch();
		render(HFImportRows, { org: 'acme', list, kind: 'dataset', folder: 'web' });
		await expect.element(screen.getByRole('progressbar')).toBeVisible();
		state = 'finished';
		await list.refresh();
		await expect.element(screen.getByText('Imported acme/data.')).toBeVisible();
		expect(invalidateAll).toHaveBeenCalled();
		expect(screen.getByRole('link', { name: 'Open' }).element().getAttribute('href')).toBe(
			'/datasets/acme/web/data'
		);
		await screen.getByRole('button', { name: 'Hide' }).click();
		await expect.element(screen.getByText('Imported acme/data.')).not.toBeInTheDocument();
		expect(localStorage.getItem('flock-hf-hidden')).toBe('[7]');
		stop();
	});

	it('says why an import failed, with the command to run instead', async () => {
		serve({
			'/api/imports/acme/hf': () => ({
				imports: [
					job({
						kind: 'model',
						dest: 'qwen',
						state: 'failed',
						finished_at: now,
						error: 'The Hub refused the access token.',
						command: 'flock models import acme/data acme/qwen'
					})
				]
			})
		});
		const list = new HFImportList('acme');
		const stop = list.watch();
		render(HFImportRows, { org: 'acme', list, kind: 'model' });
		await expect.element(screen.getByText('The import of acme/data failed.')).toBeVisible();
		await expect.element(screen.getByText('The Hub refused the access token.')).toBeVisible();
		await expect.element(screen.getByText('flock models import acme/data acme/qwen')).toBeVisible();
		stop();
	});
});

describe('HFDatasetDialog', () => {
	const preview: HFDatasetPreview = {
		kind: 'dataset',
		repo: 'acme/data',
		revision: 'main',
		configs: [
			{
				name: 'en',
				size: 3000,
				splits: [
					{ name: 'train', size: 2048, rows: 10 },
					{ name: 'test', size: 1024, rows: 2 }
				]
			},
			{ name: 'fr', size: null, splits: null }
		],
		default_config: 'en',
		max_bytes: 2500
	};

	it('looks the dataset up, then imports the splits chosen into a folder', async () => {
		const started = vi.fn();
		const calls = serve({
			'/api/imports/acme/hf/preview': () => preview,
			'/api/imports/acme/hf': () => job()
		});
		render(HFDatasetDialog, { open: true, org: 'acme', folder: 'web', onstarted: started });
		await screen.getByLabelText('Repository').fill('acme');
		await screen.getByRole('button', { name: 'Continue' }).click();
		await expect.element(screen.getByText('Enter the repository as owner/name')).toBeVisible();
		await screen.getByLabelText('Repository').fill('acme/data');
		await screen.getByLabelText('Access token').fill('hf_secret');
		await screen.getByRole('button', { name: 'Continue' }).click();

		await expect.element(screen.getByLabelText('Folder')).toHaveValue('web/data');
		const headers = new Headers(calls[0].init?.headers);
		expect(headers.get('x-hf-token')).toBe('hf_secret');
		expect(calls[0].url).toBe('/api/imports/acme/hf/preview?kind=dataset&repo=acme%2Fdata');
		// Both splits are more than the server imports: the command shows instead.
		await expect
			.element(screen.getByText(/flock datasets import acme\/data web\/data --config en/))
			.toBeVisible();
		await expect.element(screen.getByRole('button', { name: 'Import' })).toBeDisabled();

		await screen.getByLabelText('train').click();
		await screen.getByRole('button', { name: 'Import' }).click();
		expect(body(calls.at(-1)?.init)).toEqual({
			kind: 'dataset',
			repo: 'acme/data',
			config: 'en',
			splits: ['test'],
			dest: 'web/data',
			token: 'hf_secret'
		});
		expect(started).toHaveBeenCalledWith(job());
	});

	it('shows the command when the API refuses an import as too large', async () => {
		serve({
			'/api/imports/acme/hf/preview': () => ({ ...preview, max_bytes: 10 ** 9 }),
			'/api/imports/acme/hf': () =>
				Response.json(
					{
						message: 'This dataset is 60 GB, more than the 47 GB this server imports.',
						command: 'flock datasets import acme/data web/data --config en --org acme',
						too_large: true
					},
					{ status: 409 }
				)
		});
		render(HFDatasetDialog, { open: true, org: 'acme', folder: 'web', onstarted: vi.fn() });
		await screen.getByLabelText('Repository').fill('acme/data');
		await screen.getByRole('button', { name: 'Continue' }).click();
		await screen.getByRole('button', { name: 'Import' }).click();
		await expect
			.element(screen.getByText('This dataset is 60 GB, more than the 47 GB this server imports.'))
			.toBeVisible();
		await expect
			.element(screen.getByText('flock datasets import acme/data web/data --config en --org acme'))
			.toBeVisible();
	});
});

describe('HFModelDialog', () => {
	it('imports a model as a new version, named after its repository', async () => {
		const started = vi.fn();
		const calls = serve({ '/api/imports/acme/hf': () => job({ kind: 'model' }) });
		render(HFModelDialog, { open: true, org: 'acme', onstarted: started });
		await screen.getByLabelText('Repository').fill('Qwen/Qwen3-0.6B');
		await expect.element(screen.getByLabelText('Model name')).toHaveValue('qwen3-0.6b');
		await screen.getByLabelText('Tags').fill('base, qwen');
		await screen.getByLabelText('Make it the primary version').click();
		await screen.getByRole('button', { name: 'Import' }).click();
		expect(body(calls[0].init)).toEqual({
			kind: 'model',
			repo: 'Qwen/Qwen3-0.6B',
			revision: 'main',
			name: 'qwen3-0.6b',
			tags: ['base', 'qwen'],
			primary: true,
			token: null
		});
		expect(started).toHaveBeenCalled();
	});

	it('says why the API refused, with the command when it’s too large', async () => {
		serve({
			'/api/imports/acme/hf': () =>
				Response.json(
					{
						message: 'This model is 60 GB, more than the 47 GB this server imports.',
						command: 'flock models import Qwen/Big acme/big'
					},
					{ status: 409 }
				)
		});
		render(HFModelDialog, { open: true, org: 'acme', onstarted: vi.fn() });
		await screen.getByLabelText('Repository').fill('Qwen/Big');
		await screen.getByLabelText('Model name').fill('big');
		await screen.getByRole('button', { name: 'Import' }).click();
		await expect.element(screen.getByRole('alert')).toBeVisible();
		await expect.element(screen.getByText('flock models import Qwen/Big acme/big')).toBeVisible();
	});
});
