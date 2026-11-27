import { beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../routes/layout.css';
import { noAxes } from '#lib/chart/axes.ts';
import { columnDef } from '#lib/data/snapshot.ts';
import { page, resetApp } from '#lib/testing/app.svelte.ts';
import WorkspaceHarness from '#lib/testing/WorkspaceHarness.svelte';
import ViewSettings from './ViewSettings.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

/** A project's workspace of one run and two summary columns. */
function workspace() {
	return {
		meta: {
			entity: 'acme',
			project: 'bert',
			pageSize: 100,
			now: 1_790_000_200,
			columns: [columnDef('summary.loss', 'num'), columnDef('summary.acc', 'num')],
			groups: [],
			metrics: [],
			panels: [],
			sections: []
		},
		page: {
			query: { page: 1, sort: 'created_at', order: 'desc', size: 100, search: '', filters: [] },
			total: 1,
			projectTotal: 1,
			active: 0,
			runs: [
				{
					id: 'r0',
					name: 'olmo',
					state: 'finished',
					ordinal: 0,
					lines: 0,
					systemLines: 0,
					defs: []
				}
			],
			metricCount: 0,
			num: [Float64Array.from([2.1]), Float64Array.from([0.4])],
			text: [null, null],
			series: [],
			axes: [noAxes()]
		}
	};
}

/** The view bar's settings button, for a view that shows `props`. */
function open(props: Record<string, unknown>) {
	resetApp('http://flock.test/acme/bert', { shell: { access: 'member', me: null } });
	page.params = { entity: 'acme', project: 'bert' };
	render(WorkspaceHarness, {
		// @ts-expect-error -- only what the settings read
		props: { data: workspace(), component: ViewSettings, props }
	});
}

const columns = { onjump: () => {} };
const tabs = () => screen.getByRole('tab');
const columnList = () => screen.getByRole('list', { name: 'Columns' });
const xAxis = () => screen.getByText('X axis', { exact: true });

beforeEach(async () => {
	await screen.viewport(1280, 800);
});

describe('a view’s settings', () => {
	it('on the table, are its columns alone, with no tabs', async () => {
		open({ columns });
		await screen.getByRole('button', { name: 'Columns' }).click();
		await expect.element(columnList()).toBeVisible();
		expect(tabs().elements()).toHaveLength(0);
		await expect.element(xAxis()).not.toBeInTheDocument();
	});

	it('on the charts, are the line plot settings alone, with no tabs', async () => {
		open({ charts: true });
		await screen.getByRole('button', { name: 'Line plot settings' }).click();
		await expect.element(xAxis()).toBeVisible();
		expect(tabs().elements()).toHaveLength(0);
		await expect.element(columnList()).not.toBeInTheDocument();
	});

	it('on Visualize, show both under two tabs, columns first', async () => {
		open({ columns, charts: true });
		await screen.getByRole('button', { name: 'Columns and line plots' }).click();
		expect(
			tabs()
				.elements()
				.map((t) => t.textContent?.trim())
		).toEqual(['Columns', 'Line plots']);
		await expect
			.element(screen.getByRole('tab', { name: 'Columns' }))
			.toHaveAttribute('aria-selected', 'true');
		await expect.element(columnList()).toBeVisible();

		await screen.getByRole('tab', { name: 'Line plots' }).click();
		await expect
			.element(screen.getByRole('tab', { name: 'Line plots' }))
			.toHaveAttribute('aria-selected', 'true');
		await expect.element(xAxis()).toBeVisible();
		await expect.element(columnList()).not.toBeInTheDocument();
	});

	it('say on their button how many columns are hidden', async () => {
		open({ columns });
		await screen.getByRole('button', { name: 'Columns' }).click();
		await columnList().getByRole('checkbox', { name: 'Show summary.acc' }).click();
		await expect
			.element(screen.getByRole('button', { name: 'Columns, 1 columns hidden' }))
			.toBeInTheDocument();
	});

	it('put the view’s display choices above the rest', async () => {
		const onchange = vi.fn();
		open({
			columns,
			display: [
				{
					label: 'Group by',
					value: null,
					options: [
						{ value: null, label: 'none' },
						{ value: 'config.lr', label: 'config.lr' }
					],
					onchange
				}
			]
		});
		await screen.getByRole('button', { name: 'Columns' }).click();
		const display = screen.getByRole('group', { name: 'Display' });
		await expect.element(display).toBeVisible();
		await display.getByRole('button', { name: 'Group by: none' }).click();
		await screen.getByRole('menuitemradio', { name: 'config.lr' }).click();
		expect(onchange).toHaveBeenCalledWith('config.lr');
	});
});
