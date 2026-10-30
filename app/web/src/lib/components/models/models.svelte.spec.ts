import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import { app, goto, invalidateAll, resetApp } from '#lib/testing/app.svelte.ts';
import type {
	ModelEntry,
	ModelInfo,
	ModelTrashEntry,
	ModelVersion,
	ModelVersionDetail,
	ModelVersionsPage,
	OrgInfo,
	Person
} from '#lib/types.ts';
import RunModels from '../run/RunModels.svelte';
import DeleteModelDialog from './DeleteModelDialog.svelte';
import DeleteVersionDialog from './DeleteVersionDialog.svelte';
import DeletedModels from './DeletedModels.svelte';
import MakePrimaryDialog from './MakePrimaryDialog.svelte';
import ModelList from './ModelList.svelte';
import ModelRetention from './ModelRetention.svelte';
import ModelView from './ModelView.svelte';
import RemoveTagDialog from './RemoveTagDialog.svelte';
import TagDialog from './TagDialog.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

/** Answers the dashboard's endpoints by URL (the longest prefix wins); each call is recorded. */
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
const refuse = (status: number, message: string, extra: Record<string, unknown> = {}) =>
	Response.json({ message, ...extra }, { status });

const NOW = 1_790_000_000;
const alice: Person = { id: 2, name: 'Alice', email: 'alice@acme.test', avatar_url: null };
const bob: Person = { id: 3, name: 'Bob', email: 'bob@acme.test', avatar_url: null };

const version = (number: number, extra: Partial<ModelVersion> = {}): ModelVersion => ({
	model: 'acme/olmo2-1b',
	name: 'olmo2-1b',
	number,
	digest: `${number}`.padStart(64, 'a'),
	total_bytes: 3 * 1024 * 1024 * 1024,
	metadata: {},
	tags: [],
	primary: false,
	step: null,
	run: null,
	created_by: alice,
	created_at: NOW - number * 3600,
	eval_runs: 0,
	bucket: 'flock-models',
	prefix: `olmo2-1b/u/ULID${number}/`,
	...extra
});

const detail = (v: ModelVersion, extra: Partial<ModelVersionDetail> = {}): ModelVersionDetail => ({
	...v,
	files: [
		{ path: 'README.md', size: 120, sha256: 'b'.repeat(64) },
		{ path: 'config.json', size: 40, sha256: 'c'.repeat(64) },
		{ path: 'flock.json', size: 80, sha256: 'd'.repeat(64) },
		{ path: 'model.safetensors', size: 3 * 1024 ** 3, sha256: 'e'.repeat(64) }
	],
	evals: [],
	...extra
});

const versions: ModelVersion[] = [
	version(14, { tags: ['checkpoint-12000'], step: 12000, run: 'acme/pretrain/3k9x2' }),
	version(13, { tags: ['checkpoint-900', 'release'], step: 900 }),
	version(12, { primary: true, eval_runs: 2 })
];

const info: ModelInfo = {
	name: 'olmo2-1b',
	model: 'acme/olmo2-1b',
	created_at: NOW - 86_400 * 3,
	created_by: alice,
	versions: 3,
	latest: 14,
	primary: 12,
	primary_set_by: bob,
	primary_set_at: NOW - 7200,
	tags: [
		{ name: 'release', version: 13, moved_by: alice, moved_at: NOW },
		{ name: 'checkpoint-12000', version: 14, moved_by: alice, moved_at: NOW },
		{ name: 'checkpoint-900', version: 13, moved_by: alice, moved_at: NOW }
	]
};

const page = (list: ModelVersion[]): ModelVersionsPage => ({ versions: list, cursor: null });
const API = '/api/models/acme';

beforeEach(() => resetApp('http://flock.test/models/acme/olmo2-1b'));
afterEach(() => vi.unstubAllGlobals());

describe('ModelList', () => {
	const entry: ModelEntry = {
		name: 'olmo2-1b',
		model: 'acme/olmo2-1b',
		created_at: NOW - 86_400,
		created_by: alice,
		versions: 3,
		latest: 14,
		primary: 12,
		size: 5 * 1024 * 1024,
		updated_at: NOW - 3600
	};

	it('lists models with their latest and primary versions, size and age', async () => {
		render(ModelList, { org: 'acme', models: [entry], now: NOW });
		const link = screen.getByRole('link', { name: 'olmo2-1b' });
		expect(link.element().getAttribute('href')).toBe('/models/acme/olmo2-1b');
		await expect.element(screen.getByText('Version 14')).toBeInTheDocument();
		await expect.element(screen.getByText('Version 12')).toBeVisible();
		await expect.element(screen.getByText('5.0 MB')).toBeInTheDocument();
		await expect.element(screen.getByText('1h ago')).toBeVisible();
	});

	it('shows how to save a model when there are none', async () => {
		render(ModelList, { org: 'acme', models: [], now: NOW });
		await expect.element(screen.getByText('No models yet.')).toBeVisible();
		await expect.element(screen.getByText(/save_to_hub\(model, "acme\/my-model"\)/)).toBeVisible();
		await expect.element(screen.getByText('flock models pull acme/my-model')).toBeVisible();
	});
});

describe('ModelView', () => {
	const primary = detail(versions[2]);
	const view = (
		url = 'http://flock.test/models/acme/olmo2-1b',
		shown = primary,
		requested = null
	) => {
		resetApp(url);
		render(ModelView, { org: 'acme', info, version: shown, requested, now: NOW });
	};
	// The Overview tab reads README.md, config.json and flock.json through presigned URLs.
	const files: Record<string, string> = {
		'README.md': '# OLMo 2 1B',
		'config.json': '{"d_model": 2048, "attention": {"heads": 16}}',
		'flock.json': '{"architecture": "flock.training.core.models.olmo2:Olmo2", "step": 12000}'
	};
	const storage = () => ({
		'/api/storage/acme/flock-models/presign': (init?: RequestInit) => ({
			url: `http://s3.flock.test/${body(init).key}`
		}),
		'http://s3.flock.test/': (_?: RequestInit, url?: string) =>
			new Response(files[url!.split('/').at(-1)!] ?? '')
	});

	it('shows the header with the primary, who made it primary and the actions', async () => {
		serve(storage());
		view();
		await expect.element(screen.getByRole('heading', { name: 'olmo2-1b', level: 1 })).toBeVisible();
		await expect.element(screen.getByRole('button', { name: /^Version 12/ })).toBeVisible();
		await expect.element(screen.getByText('Primary', { exact: true })).toBeVisible();
		await expect.element(screen.getByText('Made primary by Bob 2h ago')).toBeVisible();
		await expect.element(screen.getByText('3.0 GB ·', { exact: false }).first()).toBeVisible();
		expect(screen.getByRole('link', { name: 'Browse files' }).element().getAttribute('href')).toBe(
			'/storage/acme/flock-models/olmo2-1b/u/ULID12/'
		);
		await expect.element(screen.getByRole('button', { name: 'Delete' })).toBeVisible();
	});

	it('shows the README, config.json and flock.json in the Overview', async () => {
		const calls = serve(storage());
		view();
		await expect.element(screen.getByRole('heading', { name: 'OLMo 2 1B' })).toBeVisible();
		await expect.element(screen.getByRole('rowheader', { name: 'attention.heads' })).toBeVisible();
		await expect.element(screen.getByText('2,048')).toBeVisible();
		await expect.element(screen.getByRole('rowheader', { name: 'architecture' })).toBeVisible();
		await expect.element(screen.getByRole('rowheader', { name: 'Digest' })).toBeVisible();
		const keys = calls
			.filter((c) => c.url.endsWith('/presign'))
			.map((c) => body(c.init).key)
			.sort();
		expect(keys).toEqual([
			'olmo2-1b/u/ULID12/README.md',
			'olmo2-1b/u/ULID12/config.json',
			'olmo2-1b/u/ULID12/flock.json'
		]);
	});

	it('says when a version has no README', async () => {
		serve(storage());
		view(undefined, { ...primary, files: primary.files.filter((f) => f.path !== 'README.md') });
		await expect
			.element(
				screen.getByText(
					'This version has no README. Add a README.md to the folder you push to describe it.'
				)
			)
			.toBeVisible();
	});

	it('gives load_from_hub and flock models pull for the version shown', async () => {
		serve(storage());
		view();
		await screen.getByRole('button', { name: 'Load' }).click();
		const dialog = screen.getByRole('dialog');
		await expect.element(dialog.getByText('Load version 12')).toBeVisible();
		await expect
			.element(dialog.getByText(/models\.load_from_hub\("acme\/olmo2-1b@12", device="cuda"\)/))
			.toBeVisible();
		await expect.element(dialog.getByText('flock models pull acme/olmo2-1b@12')).toBeVisible();
	});

	it('picks versions and tags, with checkpoint tags in step order and the primary first', async () => {
		serve({ ...storage(), [`${API}/olmo2-1b/versions`]: () => page(versions) });
		view();
		await screen.getByRole('button', { name: /^Version 12/ }).click();
		const menu = screen.getByRole('menu');
		const items = menu
			.getByRole('menuitem')
			.all()
			.map((i) => i.element().textContent!.replace(/\s+/g, ' ').trim());
		expect(items.slice(0, 4)).toEqual([
			'primary version 12',
			'release version 13',
			'checkpoint-900 version 13',
			'checkpoint-12000 version 14'
		]);
		await menu.getByRole('menuitem', { name: /^release/ }).click();
		expect(goto).toHaveBeenLastCalledWith('/models/acme/olmo2-1b?version=release', {
			reset: false
		});
		await screen.getByRole('button', { name: /^Version 12/ }).click();
		await screen.getByRole('menuitem', { name: /Version 14/ }).click();
		expect(goto).toHaveBeenLastCalledWith('/models/acme/olmo2-1b?version=14', { reset: false });
	});

	it('keeps the tab in the URL', async () => {
		serve({ ...storage(), [`${API}/olmo2-1b/versions`]: () => page(versions) });
		view();
		await screen.getByRole('tab', { name: 'Files' }).click();
		await vi.waitFor(() =>
			expect(goto).toHaveBeenCalledWith('/models/acme/olmo2-1b?tab=files', {
				replace: true,
				reset: false
			})
		);
		await expect.element(screen.getByText('model.safetensors')).toBeVisible();
		expect(
			screen.getByRole('link', { name: 'model.safetensors' }).element().getAttribute('href')
		).toBe('/storage/acme/flock-models/olmo2-1b/u/ULID12/model.safetensors');
	});

	it('lists the Versions with the Primary badge, run, step and row menu', async () => {
		serve({ [`${API}/olmo2-1b/versions`]: () => page(versions) });
		view('http://flock.test/models/acme/olmo2-1b?tab=versions');
		const row = screen.getByRole('row').filter({ hasText: 'checkpoint-12000' });
		// (The run column shows on wider screens.)
		await expect.element(row).toBeInTheDocument();
		const run = row.element().querySelector('a[href="/acme/pretrain/runs/3k9x2"]');
		expect(run?.textContent).toBe('3k9x2');
		expect(row.element().textContent).toContain('· step 12,000');
		const tags = screen.getByRole('list', { name: 'Tags of version 13' }).getByRole('listitem');
		expect(tags.all().map((t) => t.element().textContent!.trim())).toEqual([
			'release',
			'checkpoint-900'
		]);
		const primaryRow = screen.getByRole('row').filter({ hasText: 'Primary' });
		expect(primaryRow.element().getAttribute('aria-current')).toBe('true');

		await screen.getByRole('button', { name: 'Version 14 actions' }).click();
		const menu = screen.getByRole('menu');
		expect(
			menu
				.getByRole('menuitem')
				.all()
				.map((i) => i.element().textContent!.trim())
		).toEqual(['View', 'Make primary', 'Add tag…', 'Delete…']);
		expect(menu.getByRole('menuitem', { name: 'View' }).element().getAttribute('href')).toBe(
			'/models/acme/olmo2-1b?tab=versions&version=14'
		);
	});

	it('filters the Versions to checkpoints', async () => {
		const calls = serve({
			[`${API}/olmo2-1b/versions`]: (_, url) =>
				page(url!.includes('checkpoints=true') ? versions.slice(0, 2) : versions)
		});
		view('http://flock.test/models/acme/olmo2-1b?tab=versions');
		await expect.element(screen.getByRole('link', { name: '12', exact: true })).toBeVisible();
		await screen.getByRole('switch', { name: 'Checkpoints' }).click();
		await expect
			.element(screen.getByRole('link', { name: '12', exact: true }))
			.not.toBeInTheDocument();
		expect(calls.at(-1)!.url).toBe(`${API}/olmo2-1b/versions?checkpoints=true`);
	});

	it('makes a version primary from the row menu and keeps the version shown', async () => {
		const calls = serve({
			[`${API}/olmo2-1b/versions`]: () => page(versions),
			[`${API}/olmo2-1b/primary`]: () => ({ ...detail(versions[0]), previous: 12 })
		});
		view('http://flock.test/models/acme/olmo2-1b?tab=versions');
		await screen.getByRole('button', { name: 'Version 14 actions' }).click();
		await screen.getByRole('menuitem', { name: 'Make primary' }).click();
		const dialog = screen.getByRole('alertdialog');
		await expect.element(dialog.getByText('Make version 14 primary?')).toBeVisible();
		await expect
			.element(
				dialog.getByText(
					'Loading acme/olmo2-1b without a version will give version 14 instead of 12.'
				)
			)
			.toBeVisible();
		await dialog.getByRole('button', { name: 'Make primary' }).click();
		await vi.waitFor(() =>
			expect(goto).toHaveBeenLastCalledWith('/models/acme/olmo2-1b?tab=versions&version=12', {
				replace: true,
				reset: false,
				invalidateAll: true
			})
		);
		const put = calls.find((c) => c.url.endsWith('/primary'))!;
		expect([put.init?.method, body(put.init)]).toEqual(['PUT', { version: 14 }]);
	});

	it('can’t make the primary version primary again', async () => {
		serve({ [`${API}/olmo2-1b/versions`]: () => page(versions) });
		view('http://flock.test/models/acme/olmo2-1b?tab=versions');
		await screen.getByRole('button', { name: 'Version 12 actions' }).click();
		await expect
			.element(screen.getByRole('menuitem', { name: 'Make primary' }))
			.toHaveAttribute('aria-disabled', 'true');
	});

	it('says when the model has no versions yet', async () => {
		serve({});
		resetApp('http://flock.test/models/acme/olmo2-1b');
		render(ModelView, {
			org: 'acme',
			info: { ...info, versions: 0, latest: null, primary: null, tags: [] },
			version: null,
			requested: null,
			now: NOW
		});
		await expect
			.element(
				screen.getByText(
					'This model has no versions yet. A version appears once its upload finishes.'
				)
			)
			.toBeVisible();
		await expect.element(screen.getByRole('button', { name: 'Load' })).not.toBeInTheDocument();
		await expect.element(screen.getByRole('button', { name: 'Delete' })).toBeVisible();
	});

	it('shows each eval run’s metrics side by side', async () => {
		const scored = detail(versions[2], { evals: ['acme/evals/e1', 'acme/evals/e2'] });
		const calls = serve({
			[`${API}/olmo2-1b/versions/12/evals`]: () => ({
				runs: [
					{
						path: 'acme/evals/e1',
						entity: 'acme',
						project: 'evals',
						id: 'e1',
						display_name: 'mmlu run',
						state: 'finished',
						created_at: NOW,
						metrics: { 'mmlu/acc': 0.5, 'mmlu/docs_per_second': 30, _runtime: 4 }
					},
					{
						path: 'acme/evals/e2',
						entity: 'acme',
						project: 'evals',
						id: 'e2',
						display_name: null,
						state: 'finished',
						created_at: NOW,
						metrics: { 'mmlu/acc': 0.625, 'hellaswag/acc_norm': 0.25 }
					}
				]
			})
		});
		resetApp('http://flock.test/models/acme/olmo2-1b?tab=evals');
		render(ModelView, { org: 'acme', info, version: scored, requested: null, now: NOW });
		await expect.element(screen.getByRole('link', { name: 'mmlu run' })).toBeVisible();
		expect(screen.getByRole('link', { name: 'e2' }).element().getAttribute('href')).toBe(
			'/acme/evals/runs/e2'
		);
		const rows = screen
			.getByRole('rowheader')
			.all()
			.map((r) => r.element().textContent!.trim());
		expect(rows).toEqual(['hellaswag/acc_norm', 'mmlu/acc']);
		const mmlu = screen.getByRole('row').filter({ hasText: 'mmlu/acc' });
		expect(
			mmlu
				.getByRole('cell')
				.all()
				.map((c) => c.element().textContent!.trim())
		).toEqual(['0.5', '0.625']);
		expect(calls[0].url).toBe(`${API}/olmo2-1b/versions/12/evals`);
	});

	it('says how to evaluate a version no run has scored', async () => {
		serve({});
		view('http://flock.test/models/acme/olmo2-1b?tab=evals');
		await expect
			.element(screen.getByText('No eval runs have scored this version yet.'))
			.toBeVisible();
		await expect
			.element(screen.getByText('flock eval run --model flock:acme/olmo2-1b@12'))
			.toBeVisible();
	});
});

describe('the tag dialogs', () => {
	it('tags a version, offering to move a tag that is elsewhere', async () => {
		const calls = serve({
			[`${API}/olmo2-1b/tags/release`]: (init) =>
				body(init).move
					? { tag: 'release', version: 14 }
					: refuse(409, 'release is on version 13.', { version: 13 })
		});
		const ontagged = vi.fn();
		render(TagDialog, { open: true, org: 'acme', name: 'olmo2-1b', version: 14, ontagged });
		await screen.getByLabelText('Name').fill('release');
		await screen.getByRole('button', { name: 'Add tag' }).click();
		await expect
			.element(screen.getByText('release is on version 13. Move it to version 14?'))
			.toBeVisible();
		await screen.getByRole('button', { name: 'Move tag' }).click();
		await vi.waitFor(() => expect(ontagged).toHaveBeenCalled());
		expect(calls.map((c) => [c.init?.method, body(c.init)])).toEqual([
			['PUT', { version: 14, move: false }],
			['PUT', { version: 14, move: true }]
		]);
	});

	it('shows the refusal for a checkpoint tag on another version, without offering a move', async () => {
		serve({
			[`${API}/olmo2-1b/tags/checkpoint-900`]: () =>
				refuse(409, "checkpoint-900 is on version 13. Checkpoint tags don't move.", {
					version: 13
				})
		});
		render(TagDialog, {
			open: true,
			org: 'acme',
			name: 'olmo2-1b',
			version: 14,
			ontagged: vi.fn()
		});
		await screen.getByLabelText('Name').fill('checkpoint-900');
		await screen.getByRole('button', { name: 'Add tag' }).click();
		await expect
			.element(screen.getByRole('alert'))
			.toHaveTextContent('checkpoint-900 is on version 13. Checkpoint tags don’t move.');
		await expect.element(screen.getByRole('button', { name: 'Move tag' })).not.toBeInTheDocument();
	});

	it('refuses primary and numbers as tag names before asking', async () => {
		const calls = serve({});
		render(TagDialog, {
			open: true,
			org: 'acme',
			name: 'olmo2-1b',
			version: 14,
			ontagged: vi.fn()
		});
		await screen.getByLabelText('Name').fill('primary');
		await screen.getByRole('button', { name: 'Add tag' }).click();
		await expect
			.element(screen.getByRole('alert'))
			.toHaveTextContent('That name is for the primary version, so it can’t be a tag');
		await screen.getByLabelText('Name').fill('12');
		await screen.getByRole('button', { name: 'Add tag' }).click();
		await expect.element(screen.getByRole('alert')).toHaveTextContent(/numbers alone are versions/);
		expect(calls).toEqual([]);
	});

	it('warns before removing a tag', async () => {
		const calls = serve({
			[`${API}/olmo2-1b/tags/release`]: () => new Response(null, { status: 204 })
		});
		const onremoved = vi.fn();
		render(RemoveTagDialog, {
			open: true,
			org: 'acme',
			name: 'olmo2-1b',
			tag: 'release',
			version: 13,
			onremoved
		});
		await expect
			.element(
				screen.getByText(
					'Code that loads acme/olmo2-1b@release will stop working. Version 13 stays, and only the tag is removed.'
				)
			)
			.toBeVisible();
		await screen.getByRole('button', { name: 'Remove tag' }).click();
		await vi.waitFor(() => expect(onremoved).toHaveBeenCalled());
		expect(calls[0].init?.method).toBe('DELETE');
	});
});

describe('the delete dialogs', () => {
	it('deletes a version after its name is typed, warning about its eval runs', async () => {
		const calls = serve({ [`${API}/olmo2-1b/versions/11`]: () => ({ kind: 'version', id: 1 }) });
		const ondeleted = vi.fn();
		render(DeleteVersionDialog, {
			open: true,
			org: 'acme',
			version: version(11, { eval_runs: 2, tags: ['checkpoint-100'] }),
			shown: false,
			ondeleted
		});
		await expect.element(screen.getByText('Org admins can restore it for 14 days.')).toBeVisible();
		await expect
			.element(
				screen.getByText(
					'2 eval runs scored this version. Their results stay, but the version they name can’t be loaded after it’s deleted.'
				)
			)
			.toBeVisible();
		const confirm = screen.getByRole('button', { name: 'Delete version' });
		await expect.element(confirm).toBeDisabled();
		await screen.getByLabelText('Type acme/olmo2-1b@11 to confirm').fill('acme/olmo2-1b@11');
		await confirm.click();
		await vi.waitFor(() => expect(ondeleted).toHaveBeenCalled());
		expect(calls[0].init?.method).toBe('DELETE');
	});

	it('goes to the primary after deleting the version shown', async () => {
		serve({ [`${API}/olmo2-1b/versions/11`]: () => ({ kind: 'version', id: 1 }) });
		render(DeleteVersionDialog, {
			open: true,
			org: 'acme',
			version: version(11),
			shown: true,
			ondeleted: vi.fn()
		});
		await screen.getByLabelText('Type acme/olmo2-1b@11 to confirm').fill('acme/olmo2-1b@11');
		await screen.getByRole('button', { name: 'Delete version' }).click();
		await vi.waitFor(() =>
			expect(goto).toHaveBeenCalledWith('/models/acme/olmo2-1b', { replace: true })
		);
	});

	it('refuses the primary version and versions with other tags', async () => {
		const calls = serve({});
		const { rerender } = render(DeleteVersionDialog, {
			open: true,
			org: 'acme',
			version: version(12, { primary: true }),
			shown: false,
			ondeleted: vi.fn()
		});
		await expect
			.element(
				screen.getByText('Version 12 is the primary version. Make another version primary first.')
			)
			.toBeVisible();
		await expect.element(screen.getByRole('textbox')).not.toBeInTheDocument();
		await rerender({ version: version(13, { tags: ['checkpoint-900', 'release'] }) });
		await expect
			.element(screen.getByText('Version 13 is tagged release. Move or remove the tags first.'))
			.toBeVisible();
		expect(calls).toEqual([]);
	});

	it('shows the API’s refusal when the version changed meanwhile', async () => {
		serve({
			[`${API}/olmo2-1b/versions/11`]: () =>
				refuse(409, 'Version 11 is the primary version. Make another version primary first.')
		});
		render(DeleteVersionDialog, {
			open: true,
			org: 'acme',
			version: version(11),
			shown: false,
			ondeleted: vi.fn()
		});
		await screen.getByLabelText('Type acme/olmo2-1b@11 to confirm').fill('acme/olmo2-1b@11');
		await screen.getByRole('button', { name: 'Delete version' }).click();
		await expect
			.element(screen.getByRole('alert'))
			.toHaveTextContent('Version 11 is the primary version. Make another version primary first.');
	});

	it('deletes a model after its name is typed, then goes to the models', async () => {
		const calls = serve({ [`${API}/olmo2-1b`]: () => ({ kind: 'model', id: 1 }) });
		render(DeleteModelDialog, { open: true, org: 'acme', info });
		await expect
			.element(
				screen.getByText('This deletes all 3 versions. Org admins can restore it for 14 days.')
			)
			.toBeVisible();
		const confirm = screen.getByRole('button', { name: 'Delete model' });
		await expect.element(confirm).toBeDisabled();
		await screen.getByLabelText('Type acme/olmo2-1b to confirm').fill('acme/olmo2-1b');
		await confirm.click();
		await vi.waitFor(() => expect(goto).toHaveBeenCalledWith('/models/acme', { replace: true }));
		expect([calls[0].url, calls[0].init?.method]).toEqual([`${API}/olmo2-1b`, 'DELETE']);
	});
});

describe('MakePrimaryDialog', () => {
	it('shows a refusal in place', async () => {
		serve({
			[`${API}/olmo2-1b/primary`]: () => refuse(404, 'version 14 of acme/olmo2-1b not found')
		});
		render(MakePrimaryDialog, {
			open: true,
			org: 'acme',
			name: 'olmo2-1b',
			number: 14,
			current: 12,
			onchanged: vi.fn()
		});
		await screen.getByRole('button', { name: 'Make primary' }).click();
		await expect
			.element(screen.getByRole('alert'))
			.toHaveTextContent('Version 14 of acme/olmo2-1b not found');
	});
});

describe('DeletedModels', () => {
	const model: ModelTrashEntry = {
		kind: 'model',
		id: 4,
		name: 'old',
		model: 'acme/old',
		deleted_by: alice,
		deleted_at: NOW - 86_400,
		expires_at: NOW + 13 * 86_400 - 60,
		moved: true,
		versions: 2,
		bytes: 3 * 1024 * 1024
	};
	const deletedVersion: ModelTrashEntry = {
		kind: 'version',
		id: 9,
		name: 'olmo2-1b',
		model: 'acme/olmo2-1b',
		number: 7,
		deleted_by: null,
		deleted_at: NOW - 86_400,
		expires_at: NOW + 86_400 - 60,
		moved: true,
		bytes: 1024,
		tags: ['checkpoint-700'],
		eval_runs: 0
	};

	it('lists deleted models and versions with the days they have left', async () => {
		serve({});
		render(DeletedModels, {
			org: 'acme',
			entries: [model, deletedVersion, { ...deletedVersion, id: 10, number: 8, moved: false }],
			now: NOW
		});
		await expect.element(screen.getByText('acme/old')).toBeVisible();
		await expect
			.element(screen.getByText(/Model with 2 versions · 3\.0 MB · deleted .* by Alice/))
			.toBeVisible();
		await expect.element(screen.getByText('acme/olmo2-1b@7')).toBeVisible();
		await expect.element(screen.getByText('13 days left')).toBeVisible();
		await expect.element(screen.getByText('1 day left')).toBeVisible();
		await expect.element(screen.getByText('Still being moved')).toBeVisible();
		expect(screen.getByRole('button', { name: 'Restore' }).all()).toHaveLength(2);
	});

	it('restores a model, showing the refusal when its name is taken', async () => {
		let attempt = 0;
		const calls = serve({
			[`${API}/-/trash/models/4/restore`]: () =>
				attempt++ === 0
					? refuse(409, 'A model named old exists now.', { taken: true })
					: { name: 'old' }
		});
		render(DeletedModels, { org: 'acme', entries: [model], now: NOW });
		await screen.getByRole('button', { name: 'Restore' }).click();
		const dialog = screen.getByRole('dialog');
		await expect
			.element(dialog.getByText('It comes back with the 2 versions deleted with it, and its tags.'))
			.toBeVisible();
		await dialog.getByRole('button', { name: 'Restore' }).click();
		await expect
			.element(dialog.getByRole('alert'))
			.toHaveTextContent('A model named old exists now.');
		await dialog.getByRole('button', { name: 'Restore' }).click();
		await vi.waitFor(() => expect(invalidateAll).toHaveBeenCalled());
		expect(calls.map((c) => c.init?.method)).toEqual(['POST', 'POST']);
	});

	it('restores a version with its checkpoint tags', async () => {
		const calls = serve({ [`${API}/-/trash/versions/9/restore`]: () => ({ number: 7 }) });
		render(DeletedModels, { org: 'acme', entries: [deletedVersion], now: NOW });
		await screen.getByRole('button', { name: 'Restore' }).click();
		const dialog = screen.getByRole('dialog');
		await expect
			.element(dialog.getByText('It comes back as version 7, with checkpoint-700.'))
			.toBeVisible();
		await dialog.getByRole('button', { name: 'Restore' }).click();
		await vi.waitFor(() => expect(calls).toHaveLength(1));
	});

	it('deletes for good after the name is typed', async () => {
		const calls = serve({
			[`${API}/-/trash/versions/9`]: () => new Response(null, { status: 204 })
		});
		render(DeletedModels, { org: 'acme', entries: [deletedVersion], now: NOW });
		await screen.getByRole('button', { name: 'Delete now' }).click();
		const confirm = screen.getByRole('button', { name: 'Delete for good' });
		await expect.element(confirm).toBeDisabled();
		await screen.getByLabelText('Type acme/olmo2-1b@7 to confirm').fill('acme/olmo2-1b@7');
		await confirm.click();
		await vi.waitFor(() => expect(calls[0]?.init?.method).toBe('DELETE'));
		expect(calls[0].url).toBe(`${API}/-/trash/versions/9`);
	});

	it('says when there are none', async () => {
		serve({});
		render(DeletedModels, { org: 'acme', entries: [], now: NOW });
		await expect.element(screen.getByText('No deleted models.')).toBeVisible();
	});
});

describe('ModelRetention', () => {
	const org = { name: 'acme', model_retention: null } as OrgInfo;

	it('is off by default, and turns on with a number of versions', async () => {
		render(ModelRetention, { org });
		const input = screen.getByLabelText('Untagged versions to keep');
		await expect.element(screen.getByRole('switch', { name: 'Retention' })).not.toBeChecked();
		await expect.element(input).toBeDisabled();
		await expect.element(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
		await screen.getByRole('switch', { name: 'Retention' }).click();
		await input.fill('5');
		await screen.getByRole('button', { name: 'Save' }).click();
		expect(app.sent.map((s) => [s.action.split('?')[1], s.form.get('keep')])).toEqual([
			['/modelRetention', '5']
		]);
	});

	it('turns off by sending no number', async () => {
		render(ModelRetention, { org: { ...org, model_retention: 20 } });
		await expect.element(screen.getByLabelText('Untagged versions to keep')).toHaveValue(20);
		await screen.getByRole('switch', { name: 'Retention' }).click();
		await screen.getByRole('button', { name: 'Save' }).click();
		expect(app.sent.map((s) => s.form.get('keep'))).toEqual(['']);
	});
});

describe('RunModels', () => {
	it('lists the versions a run made, linked to each one, with steps and tags', async () => {
		render(RunModels, {
			title: 'Checkpoints',
			versions: [version(13, { step: 900, tags: ['release', 'checkpoint-900'], primary: true })]
		});
		await expect.element(screen.getByRole('heading', { name: /Checkpoints/ })).toBeVisible();
		const link = screen.getByRole('link', { name: 'olmo2-1b@13' });
		expect(link.element().getAttribute('href')).toBe('/models/acme/olmo2-1b?version=13');
		await expect
			.element(screen.getByText('primary · step 900 · release · checkpoint-900'))
			.toBeVisible();
	});
});
