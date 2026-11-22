import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import { resetApp } from '#lib/testing/app.svelte.ts';
import { LaunchList } from '#lib/launches.svelte.ts';
import type { EvalCatalogue, EvalLaunch } from '#lib/types.ts';
import EvalLaunchButton from './EvalLaunchButton.svelte';
import EvalLaunchDialog from './EvalLaunchDialog.svelte';
import EvalLaunchRows from './EvalLaunchRows.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

/** Answers the dashboard's eval endpoints by URL; each call is recorded. */
function serve(routes: Record<string, (init?: RequestInit) => unknown>) {
	const calls: { url: string; init?: RequestInit }[] = [];
	vi.stubGlobal(
		'fetch',
		vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
			const url = String(input);
			calls.push({ url, init });
			const route = routes[url];
			if (!route) return Response.json({ message: 'not found' }, { status: 404 });
			const body = route(init);
			return body instanceof Response ? body : Response.json(body);
		})
	);
	return calls;
}

const body = (init?: RequestInit) => JSON.parse(String(init?.body));
const now = Date.now() / 1000;
const ME = { id: 3, name: 'Ada', email: 'ada@acme.test', avatar_url: null };

const CATALOGUE: EvalCatalogue = {
	suites: ['olmo3:base_easy'],
	tasks: ['arc_easy:rc', 'hellaswag:rc'],
	max_usd: 5,
	monthly_usd: 200
};

const launch = (fields: Partial<EvalLaunch> = {}): EvalLaunch => ({
	id: 7,
	project: 'bert',
	model: 'allenai/OLMo-2-0425-1B',
	revision: 'main',
	sha: '0123456789abcdef',
	suites: ['olmo3:base_easy'],
	tasks: [],
	add_bos: false,
	run: 'abc123',
	state: 'running',
	cap_usd: 5,
	flock_commit: null,
	error: null,
	started_by: ME,
	started_at: now - 60,
	finished_at: null,
	...fields
});

beforeEach(() => resetApp('http://flock.test/acme/bert'));
afterEach(() => vi.unstubAllGlobals());

describe('EvalLaunchButton', () => {
	it('is hidden on a server that doesn’t launch evals', async () => {
		const calls = serve({});
		render(EvalLaunchButton, {
			org: 'acme',
			project: 'bert',
			list: new LaunchList('acme', 'bert')
		});
		await expect
			.element(screen.getByRole('button', { name: 'Launch eval' }))
			.not.toBeInTheDocument();
		expect(calls[0].url).toBe('/api/evals/acme/launches/catalogue');
	});

	it('opens the dialog with what may be scored', async () => {
		serve({ '/api/evals/acme/launches/catalogue': () => CATALOGUE });
		render(EvalLaunchButton, {
			org: 'acme',
			project: 'bert',
			list: new LaunchList('acme', 'bert')
		});
		await screen.getByRole('button', { name: 'Launch eval' }).click();
		await expect.element(screen.getByLabelText('olmo3:base_easy')).toBeVisible();
		await expect.element(screen.getByText(/at most \$5 per launch/)).toBeVisible();
	});
});

describe('EvalLaunchDialog', () => {
	const props = (onstarted = vi.fn()) => ({
		open: true,
		org: 'acme',
		project: 'bert',
		catalogue: CATALOGUE,
		catalogueError: null,
		onstarted
	});

	it('launches the model on the suites and tasks picked, into the project', async () => {
		const started = vi.fn();
		const calls = serve({ '/api/evals/acme/launches': () => launch() });
		render(EvalLaunchDialog, props(started));
		await screen.getByLabelText('Model repository').fill('google/gemma-3-1b-pt');
		await screen.getByRole('button', { name: 'Launch' }).click();
		await expect.element(screen.getByText('Pick a suite or a task')).toBeVisible();
		expect(calls).toEqual([]);

		await screen.getByLabelText('olmo3:base_easy').click();
		await screen.getByPlaceholder('Filter').fill('hella');
		await expect.element(screen.getByLabelText('arc_easy:rc')).not.toBeInTheDocument();
		await screen.getByLabelText('hellaswag:rc').click();
		await screen.getByLabelText('Add BOS (Gemma)').click();
		await screen.getByRole('button', { name: 'Launch' }).click();
		expect(calls[0].url).toBe('/api/evals/acme/launches');
		expect(calls[0].init?.method).toBe('POST');
		expect(body(calls[0].init)).toEqual({
			model: 'google/gemma-3-1b-pt',
			revision: 'main',
			suites: ['olmo3:base_easy'],
			tasks: ['hellaswag:rc'],
			add_bos: true,
			project: 'bert'
		});
		expect(started).toHaveBeenCalledWith(launch());
	});

	it('says why the API refused', async () => {
		serve({
			'/api/evals/acme/launches': () =>
				Response.json({ message: 'this month’s launches reached the cap' }, { status: 409 })
		});
		render(EvalLaunchDialog, props());
		await screen.getByLabelText('Model repository').fill('allenai/OLMo-2-0425-1B');
		await screen.getByLabelText('arc_easy:rc').click();
		await screen.getByRole('button', { name: 'Launch' }).click();
		await expect
			.element(screen.getByRole('alert'))
			.toHaveTextContent('This month’s launches reached the cap');
	});

	it('waits for the catalogue, then says when it can’t be read', async () => {
		const { rerender } = render(EvalLaunchDialog, { ...props(), catalogue: null });
		await expect.element(screen.getByRole('status')).toHaveTextContent(/first time/);
		await rerender({ catalogueError: 'Modal refused the call' });
		await expect.element(screen.getByRole('alert')).toHaveTextContent('Modal refused the call');
	});
});

describe('EvalLaunchRows', () => {
	it('cancels a running launch of the viewer’s', async () => {
		const calls = serve({
			'/api/evals/acme/launches/7': () => launch({ state: 'cancelled', finished_at: now }),
			'/api/evals/acme/launches?project=bert': () => ({
				launches: [launch()]
			})
		});
		const list = new LaunchList('acme', 'bert');
		const stop = list.watch();
		render(EvalLaunchRows, { org: 'acme', project: 'bert', list, me: ME.id, admin: false });
		await expect.element(screen.getByText('allenai/OLMo-2-0425-1B@0123456')).toBeVisible();
		// Flock filters by project before its limit, so the list asks for this project only.
		expect(calls[0].url).toBe('/api/evals/acme/launches?project=bert');
		await screen.getByRole('button', { name: 'Cancel' }).click();
		await expect.element(screen.getByText('Cancelled')).toBeVisible();
		expect(calls.at(-1)).toMatchObject({
			url: '/api/evals/acme/launches/7',
			init: { method: 'DELETE' }
		});
		stop();
	});

	it('asks again after a failed first read, before any launch is known', async () => {
		let reads = 0;
		serve({
			'/api/evals/acme/launches?project=bert': () =>
				++reads === 1
					? Response.json({ error: 'unavailable' }, { status: 503 })
					: { launches: [launch()] }
		});
		const list = new LaunchList('acme', 'bert');
		const stop = list.watch();
		render(EvalLaunchRows, { org: 'acme', project: 'bert', list, me: ME.id, admin: false });
		await expect
			.element(screen.getByText('allenai/OLMo-2-0425-1B@0123456'), { timeout: 8000 })
			.toBeVisible();
		expect(reads).toBe(2);
		stop();
	});

	it('offers Cancel on someone else’s launch only to an admin', async () => {
		serve({
			'/api/evals/acme/launches?project=bert': () => ({
				launches: [launch({ started_by: { ...ME, id: 9, name: 'Bo' } })]
			})
		});
		for (const admin of [false, true]) {
			const list = new LaunchList('acme', 'bert');
			const stop = list.watch();
			render(EvalLaunchRows, { org: 'acme', project: 'bert', list, me: ME.id, admin });
			await expect.element(screen.getByText(/Bo, 1m ago/)).toBeVisible();
			const button = screen.getByRole('button', { name: 'Cancel' });
			if (admin) await expect.element(button).toBeVisible();
			else await expect.element(button).not.toBeInTheDocument();
			stop();
			document.body.innerHTML = '';
		}
	});

	it('links a finished launch to its run, and says why one failed', async () => {
		serve({
			'/api/evals/acme/launches?project=bert': () => ({
				launches: [
					launch({ state: 'finished', finished_at: now, flock_commit: 'deadbeef' }),
					launch({ id: 8, state: 'failed', finished_at: now, error: 'CUDA out of memory' })
				]
			})
		});
		const list = new LaunchList('acme', 'bert');
		const stop = list.watch();
		render(EvalLaunchRows, { org: 'acme', project: 'bert', list, me: ME.id, admin: false });
		await expect
			.element(screen.getByRole('link', { name: 'Open run' }))
			.toHaveAttribute('href', '/acme/bert/runs/abc123');
		await expect.element(screen.getByText('CUDA out of memory')).toBeVisible();
		await expect.element(screen.getByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
		stop();
	});
});
