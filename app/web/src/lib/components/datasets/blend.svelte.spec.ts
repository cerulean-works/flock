import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen, userEvent } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import { resetApp } from '#lib/testing/app.svelte.ts';
import type { BlendColumn, BlendPreview } from '#lib/types.ts';
import BlendDialog from './BlendDialog.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

const web = [
	{ name: 'id', type: 'int64', nullable: true },
	{ name: 'text', type: 'string', nullable: true }
];
const code = [
	{ name: 'doc_id', type: 'int64', nullable: true },
	{ name: 'content', type: 'string', nullable: true },
	{ name: 'score', type: 'int32', nullable: true }
];

/** What the API answers for a preview: the automatic map unless the request sent one. */
function previewOf(sent: { columns: BlendColumn[] | null; seed: number }): BlendPreview {
	const automatic: BlendColumn[] = [
		{
			name: 'id',
			type: 'int64',
			nullable: true,
			from: { web: { column: 'id' }, code: { null: true } }
		},
		{
			name: 'text',
			type: 'string',
			nullable: true,
			from: { web: { column: 'text' }, code: { null: true } }
		}
	];
	const columns = sent.columns ?? automatic;
	return {
		sources: [
			{ path: 'web', version: 3, live: 1000, rows: 100, share: 0.1, schema: web },
			{ path: 'code', version: 1, live: 500, rows: 50, share: 0.1, schema: code }
		],
		total: 150,
		columns,
		automatic: sent.columns === null,
		unused: {
			web: [],
			code: code.filter(
				(c) =>
					!columns.some((o) => JSON.stringify(o.from.code) === JSON.stringify({ column: c.name }))
			)
		},
		writes: [],
		problems: [],
		column_problems: []
	};
}

/** The API's refusal of fractions more than 0.1% off 100%. */
function percentageProblem(sent: { sources: { fraction?: number }[] }): string[] {
	const fractions = sent.sources.map((s) => s.fraction);
	if (fractions.some((f) => f === undefined)) return [];
	const added = fractions.reduce((a, f) => a! + f!, 0)!;
	return Math.abs(added - 1) > 0.001 + 1e-9
		? [`The percentages add up to ${added * 100}%, not 100%.`]
		: [];
}

let calls: { url: string; body: Record<string, unknown> }[] = [];

const entry = (path: string, version: number) => ({
	name: path.split('/').at(-1)!,
	path,
	version,
	updated_at: null
});
/** `GET …/datasets/at` answers, by folder. */
const folders: Record<string, unknown> = {
	'': {
		kind: 'folder',
		path: '',
		folders: [
			{ name: 'mixes', path: 'mixes' },
			{ name: 'web', path: 'web' }
		],
		datasets: [],
		files: [],
		cursor: null
	},
	mixes: {
		kind: 'folder',
		path: 'mixes',
		folders: [],
		datasets: [entry('mixes/old', 2)],
		files: [],
		cursor: null
	},
	web: {
		kind: 'folder',
		path: 'web',
		folders: [],
		datasets: [entry('web/train', 3), entry('web/test', 1)],
		files: [],
		cursor: null
	}
};

beforeEach(() => {
	resetApp('http://flock.test/datasets/acme/mixes');
	calls = [];
	vi.stubGlobal(
		'fetch',
		vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = String(input);
			const body = JSON.parse(String(init?.body ?? '{}'));
			calls.push({ url, body });
			if (url.includes('/versions?')) {
				const at = new URL(url, 'http://flock.test').searchParams.get('path');
				const v = (version: number, tags: string[] = []) => ({
					version,
					tags,
					timestamp: 0,
					operation: null,
					rows: version * 1000,
					rows_delta: null,
					properties: {}
				});
				return Response.json({
					versions:
						at === 'web/train' ? [v(3, ['clean', 'v1-final']), v(2), v(1, ['raw'])] : [v(2)],
					latest: at === 'web/train' ? 3 : 2,
					cursor: null
				});
			}
			if (url.includes('/at?')) {
				const at = new URL(url, 'http://flock.test').searchParams.get('path') ?? '';
				return Response.json(folders[at] ?? { message: 'not found' }, {
					status: at in folders ? 200 : 404
				});
			}
			if (url.endsWith('/blend/preview')) {
				const answer = previewOf(body);
				answer.problems = percentageProblem(body);
				return Response.json(answer);
			}
			if (url.endsWith('/blend'))
				return Response.json(
					{ id: 7, kind: 'blend', dest: body.to, state: 'running' },
					{ status: 202 }
				);
			return Response.json({ message: 'not found' }, { status: 404 });
		})
	);
});
afterEach(() => vi.unstubAllGlobals());

const start = {
	sources: [
		{ path: 'web', amount: '100' },
		{ path: 'code', amount: '50' }
	]
};

describe('the blend dialog', () => {
	it('starts with seed 42 and sends the map, counts and seed it shows', async () => {
		const onstarted = vi.fn();
		render(BlendDialog, {
			open: true,
			scope: { org: 'acme', project: null },
			folder: 'mixes',
			start,
			onstarted
		});
		await expect.element(screen.getByLabelText('Seed', { exact: true })).toHaveValue('42');
		await expect.element(screen.getByLabelText('Name')).toHaveValue('blend');
		await expect
			.element(screen.getByRole('button', { name: 'In folder' }))
			.toHaveTextContent('mixes');
		await expect
			.element(screen.getByRole('button', { name: 'Create blend' }))
			.not.toBeInTheDocument();

		// Step 2: the automatic map filled code with nulls, and marks them.
		await screen.getByRole('button', { name: 'Next' }).click();
		await expect.element(screen.getByText('null').first()).toBeVisible();
		// Step 3: what will be made, then Create.
		await screen.getByRole('button', { name: 'Next' }).click();
		await expect
			.element(screen.getByText('Blend 150 rows into mixes/blend with seed 42.'))
			.toBeVisible();
		await expect.element(screen.getByRole('cell', { name: '100', exact: true })).toBeVisible();

		await screen.getByRole('button', { name: 'Create blend' }).click();
		await vi.waitFor(() => expect(onstarted).toHaveBeenCalled());
		const sent = calls.find((c) => c.url.endsWith('/blend'))!.body;
		expect(sent.seed).toBe(42);
		expect(sent.to).toBe('mixes/blend');
		expect(sent.sources).toEqual([
			{ path: 'web', version: null, rows: 100 },
			{ path: 'code', version: null, rows: 50 }
		]);
		expect((sent.columns as BlendColumn[]).map((c) => c.name)).toEqual(['id', 'text']);
	});

	it('maps a column from another name, and adds one from "Not in the blend"', async () => {
		await screen.viewport(1280, 900);
		render(BlendDialog, {
			open: true,
			scope: { org: 'acme', project: null },
			folder: 'mixes',
			start,
			onstarted: vi.fn()
		});
		await screen.getByRole('button', { name: 'Next' }).click();
		await screen.getByLabelText('text from code').click();
		await screen.getByRole('option', { name: /content/ }).click();
		await expect.element(screen.getByText('renamed', { exact: true })).toBeVisible();
		// int32 score can't become string: offered but disabled, saying why. It can become int64.
		await screen.getByLabelText('text from code').click();
		await expect
			.element(screen.getByRole('option', { name: /score/ }))
			.toHaveTextContent('int32, can’t become string');
		await userEvent.keyboard('{Escape}');
		await screen.getByLabelText('id from code').click();
		await expect
			.element(screen.getByRole('option', { name: /score/ }))
			.toHaveTextContent('int32 → int64');
		await userEvent.keyboard('{Escape}');

		await screen.getByRole('button', { name: /score int32 · code/ }).click();
		await screen.getByRole('button', { name: /Add a column naming each row’s source/ }).click();
		await vi.waitFor(() => {
			const last = calls.filter((c) => c.url.endsWith('/blend/preview')).at(-1)!.body;
			const columns = last.columns as BlendColumn[];
			expect(columns.map((c) => c.name)).toEqual(['id', 'text', 'score', 'source']);
			expect(columns[1].from.code).toEqual({ column: 'content' });
			expect(columns[2].from).toEqual({ web: { null: true }, code: { column: 'score' } });
			expect(columns[3].from).toEqual({ web: { value: 'web' }, code: { value: 'code' } });
		});
	});

	it('picks sources from a dropdown browsing flock-datasets', async () => {
		render(BlendDialog, {
			open: true,
			scope: { org: 'acme', project: null },
			folder: 'mixes',
			onstarted: vi.fn()
		});
		// No version until a dataset is chosen.
		await expect
			.element(screen.getByRole('button', { name: 'Version of source 1' }))
			.not.toBeInTheDocument();
		// It starts in the dialog's folder; up goes to the root, a folder opens in place.
		await screen.getByRole('button', { name: 'Source 1', exact: true }).click();
		await expect.element(screen.getByRole('option', { name: /old/ })).toBeVisible();
		await screen.getByRole('button', { name: 'Up a folder' }).click();
		await screen.getByRole('option', { name: 'web/' }).click();
		await screen.getByRole('combobox', { name: 'Search datasets' }).fill('tra');
		await expect.element(screen.getByRole('option', { name: /test/ })).not.toBeInTheDocument();
		await screen.getByRole('option', { name: /train/ }).click();
		await expect
			.element(screen.getByRole('button', { name: 'Source 1', exact: true }))
			.toHaveTextContent('web/train');

		// An empty source starts in the dialog's folder.
		await screen.getByRole('button', { name: 'Source 2', exact: true }).click();
		await screen.getByRole('option', { name: /old/ }).click();
		await vi.waitFor(() => {
			const last = calls.filter((c) => c.url.endsWith('/blend/preview')).at(-1)!.body;
			expect((last.sources as { path: string }[]).map((s) => s.path)).toEqual([
				'web/train',
				'mixes/old'
			]);
		});
		// A version is chosen once a dataset is, from its versions with their tags.
		await screen.getByRole('button', { name: 'Version of source 1' }).click();
		await expect.element(screen.getByRole('option', { name: /^v1 / })).toHaveTextContent('raw');
		await screen.getByRole('option', { name: /clean/ }).click();
		await expect
			.element(screen.getByRole('button', { name: 'Version of source 1' }))
			.toHaveTextContent('v3 · clean · v1-final · 3,000 rows');
		await vi.waitFor(() => {
			const last = calls.filter((c) => c.url.endsWith('/blend/preview')).at(-1)!.body;
			expect((last.sources as { version: string | null }[])[0].version).toBe('3');
		});
		// Reopening starts in the picked dataset's folder.
		await screen.getByRole('button', { name: 'Source 1', exact: true }).click();
		await expect
			.element(screen.getByRole('option', { name: /train/ }))
			.toHaveAttribute('aria-selected', 'true');
	});

	it('shows one source at a time on a small screen', async () => {
		await screen.viewport(400, 800);
		render(BlendDialog, {
			open: true,
			scope: { org: 'acme', project: null },
			folder: 'mixes',
			start,
			onstarted: vi.fn()
		});
		await screen.getByRole('button', { name: 'Next' }).click();
		await expect.element(screen.getByLabelText('text from web')).toBeVisible();
		await expect.element(screen.getByLabelText('text from code')).not.toBeVisible();
		await screen.getByRole('tab', { name: 'code' }).click();
		await expect.element(screen.getByLabelText('text from code')).toBeVisible();
		await expect.element(screen.getByLabelText('text from web')).not.toBeVisible();
	});

	it('reads percentages as fractions of the total', async () => {
		render(BlendDialog, {
			open: true,
			scope: { org: 'acme', project: null },
			folder: '',
			start: {
				sources: [
					{ path: 'web', amount: '66' },
					{ path: 'code', amount: '34' }
				],
				mode: 'fraction',
				total: '300,000'
			},
			onstarted: vi.fn()
		});
		await vi.waitFor(() => {
			const last = calls.filter((c) => c.url.endsWith('/blend/preview')).at(-1)!.body;
			expect(last.total).toBe(300000);
			expect(last.sources).toEqual([
				{ path: 'web', version: null, fraction: 0.66 },
				{ path: 'code', version: null, fraction: 0.34 }
			]);
		});
	});

	it('refuses percentages off 100% on the first step, within 0.1%', async () => {
		render(BlendDialog, {
			open: true,
			scope: { org: 'acme', project: null },
			folder: '',
			start: {
				sources: [
					{ path: 'web', amount: '66.6' },
					{ path: 'code', amount: '33.3' }
				],
				mode: 'fraction',
				total: '300000'
			},
			onstarted: vi.fn()
		});
		await expect.element(screen.getByRole('button', { name: 'Next' })).toBeEnabled();
		await screen.getByLabelText('Percentage from source 2').fill('30');
		await expect
			.element(screen.getByText('The percentages add up to 96.6%, not 100%.'))
			.toBeVisible();
		await expect.element(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
	});
});
