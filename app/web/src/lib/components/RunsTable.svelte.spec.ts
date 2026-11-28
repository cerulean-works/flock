import { beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../routes/layout.css';
import { noAxes } from '#lib/chart/axes.ts';
import { columnDef } from '#lib/data/snapshot.ts';
import { page, resetApp } from '#lib/testing/app.svelte.ts';
import WorkspaceHarness from '#lib/testing/WorkspaceHarness.svelte';
import RunsTable from './RunsTable.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

const NAMES = ['olmo', 'llama', 'qwen'];

/** A project's workspace of three finished runs, with one numeric column. */
function workspace() {
	return {
		meta: {
			entity: 'acme',
			project: 'bert',
			pageSize: 100,
			now: 1_790_000_200,
			columns: [columnDef('summary.loss', 'num')],
			groups: [],
			metrics: [],
			panels: [],
			sections: []
		},
		page: {
			query: { page: 1, sort: 'created_at', order: 'desc', size: 100, search: '', filters: [] },
			total: NAMES.length,
			projectTotal: NAMES.length,
			active: 0,
			runs: NAMES.map((name, ordinal) => ({
				id: `r${ordinal}`,
				name,
				state: 'finished',
				ordinal,
				lines: 0,
				systemLines: 0,
				defs: []
			})),
			metricCount: 0,
			num: [Float64Array.from([2.1, 2.3, 2.2])],
			text: [null],
			series: [],
			axes: NAMES.map(() => noAxes())
		}
	};
}

/** The runs table as a member sees it, rows checkable and with menus. */
function open() {
	resetApp('http://flock.test/acme/bert', {
		shell: { access: 'member', me: null },
		groups: { groups: [], ungrouped_count: 3 }
	});
	page.params = { entity: 'acme', project: 'bert' };
	render(WorkspaceHarness, {
		// @ts-expect-error -- only what the table reads
		props: { data: workspace(), component: RunsTable, props: { mode: 'table', nameW: 280 } }
	});
}

/** A run's row in the table (the name cell is in it). */
const row = (name: string) =>
	screen.getByText(name, { exact: true }).element().closest<HTMLElement>('[data-row]')!;
const menu = () => screen.getByRole('menu');

beforeEach(async () => {
	await screen.viewport(1280, 800);
});

describe('a run row’s right-click', () => {
	it('opens the run’s menu where the pointer is, and marks the row while it is open', async () => {
		open();
		await expect.element(screen.getByText('llama', { exact: true })).toBeVisible();
		const box = row('llama').getBoundingClientRect();
		await screen
			.getByText('llama', { exact: true })
			.click({ button: 'right', position: { x: 4, y: 4 } });

		await expect.element(menu()).toBeVisible();
		await expect.element(menu().getByRole('menuitem', { name: 'Edit run…' })).toBeVisible();
		await expect.element(menu().getByRole('menuitem', { name: 'Delete run…' })).toBeVisible();
		// Opened from the point clicked: its anchor sits there.
		const anchor = document.querySelector<HTMLElement>('.fixed[aria-label="Actions for llama"]')!;
		const point = anchor.getBoundingClientRect();
		const name = screen.getByText('llama', { exact: true }).element().getBoundingClientRect();
		expect(Math.abs(point.left - (name.left + 4))).toBeLessThan(2);
		expect(Math.abs(point.top - (name.top + 4))).toBeLessThan(2);
		expect(point.top).toBeGreaterThanOrEqual(box.top);

		expect(row('llama').classList.contains('bg-muted')).toBe(true);
		expect(row('olmo').classList.contains('bg-muted')).toBe(false);
	});

	it('acts on the whole selection from a selected row of several', async () => {
		open();
		await screen.getByRole('checkbox', { name: 'Select olmo' }).click();
		await screen.getByRole('checkbox', { name: 'Select qwen' }).click();

		await screen.getByText('qwen', { exact: true }).click({ button: 'right' });
		await expect.element(menu().getByText('2 selected')).toBeVisible();
		await expect.element(menu().getByRole('menuitem', { name: 'Delete 2 runs…' })).toBeVisible();
		// Two runs compare from either one's menu; one run's own Edit is left out.
		await expect
			.element(menu().getByRole('menuitem', { name: 'Compare', exact: true }))
			.toBeVisible();
		expect(menu().getByRole('menuitem', { name: 'Edit run…' }).query()).toBeNull();
	});

	it('acts on an unselected row alone, whatever else is selected', async () => {
		open();
		await screen.getByRole('checkbox', { name: 'Select olmo' }).click();
		await screen.getByRole('checkbox', { name: 'Select qwen' }).click();

		await screen.getByText('llama', { exact: true }).click({ button: 'right' });
		await expect.element(menu().getByRole('menuitem', { name: 'Edit run…' })).toBeVisible();
		await expect.element(menu().getByRole('menuitem', { name: 'Delete run…' })).toBeVisible();
		expect(menu().getByText('2 selected').query()).toBeNull();
		// The selection is left as it was.
		await expect.element(screen.getByRole('checkbox', { name: 'Select olmo' })).toBeChecked();
		await expect.element(screen.getByRole('checkbox', { name: 'Select llama' })).not.toBeChecked();
	});

	it('acts on a lone selected row alone', async () => {
		open();
		await screen.getByRole('checkbox', { name: 'Select olmo' }).click();
		await screen.getByText('olmo', { exact: true }).click({ button: 'right' });
		await expect.element(menu().getByRole('menuitem', { name: 'Edit run…' })).toBeVisible();
		expect(
			menu()
				.getByText(/selected/)
				.query()
		).toBeNull();
	});

	it('leaves the browser’s own menu to a reader, who has no run actions', async () => {
		resetApp('http://flock.test/acme/bert', { shell: { access: 'public', me: null } });
		page.params = { entity: 'acme', project: 'bert' };
		render(WorkspaceHarness, {
			// @ts-expect-error -- only what the table reads
			props: { data: workspace(), component: RunsTable, props: { mode: 'table', nameW: 280 } }
		});
		await expect.element(screen.getByText('llama', { exact: true })).toBeVisible();
		const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
		row('llama').dispatchEvent(event);
		expect(event.defaultPrevented).toBe(false);
		expect(screen.getByRole('menu').query()).toBeNull();
	});
});
