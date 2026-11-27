import { beforeEach, describe, expect, it, vi } from 'vitest';
import { page as screen } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../routes/layout.css';
import { resetApp } from '#lib/testing/app.svelte.ts';
import { Selection } from '#lib/selection.svelte.ts';
import type { ModelEntry, ModelFolder } from '#lib/types.ts';
import ModelList from './models/ModelList.svelte';

vi.mock('$app/forms', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/navigation', () => import('#lib/testing/app.svelte.ts'));
vi.mock('$app/state', () => import('#lib/testing/app.svelte.ts'));

const model = (name: string): ModelEntry => ({
	name,
	model: `acme/${name}`,
	project: null,
	created_at: 1_790_000_000,
	created_by: null,
	versions: 1,
	latest: 1,
	primary: 1,
	size: 1000,
	updated_at: 1_790_000_000
});

const FOLDER: ModelFolder = {
	kind: 'folder',
	folder: '',
	project: null,
	models: [model('olmo'), model('llama'), model('qwen')],
	folders: [{ name: 'baselines', path: 'baselines', models: 2 }],
	cursor: null
};

/** A list of models with a menu on each row (as for a member), and its selection. */
function open() {
	resetApp('http://flock.test/models/acme');
	const selection = new Selection();
	render(ModelList, {
		scope: { org: 'acme', project: null },
		path: '',
		folder: FOLDER,
		now: 1_790_000_100,
		selection
	});
	return selection;
}

const row = (name: string) => screen.getByRole('row', { name, exact: true });
const menu = () => screen.getByRole('menu');

beforeEach(async () => {
	await screen.viewport(1280, 800);
});

describe('a data list row’s right-click', () => {
	it('opens the row’s menu where the pointer is, and marks the row while it is open', async () => {
		open();
		await expect.element(row('llama')).toBeVisible();
		const target = row('llama').element() as HTMLElement;
		const box = target.getBoundingClientRect();
		await row('llama').click({ button: 'right', position: { x: 200, y: 10 } });

		await expect.element(menu()).toBeVisible();
		await expect.element(menu().getByRole('menuitem', { name: 'Rename…' })).toBeVisible();
		await expect.element(menu().getByRole('menuitem', { name: 'Delete…' })).toBeVisible();
		// From the point clicked, not from the row's ⋯ at its end.
		const anchor = document.querySelector<HTMLElement>('.fixed[aria-label="Actions for llama"]')!;
		const point = anchor.getBoundingClientRect();
		expect(Math.abs(point.left - (box.left + 200))).toBeLessThan(2);
		expect(Math.abs(point.top - (box.top + 10))).toBeLessThan(2);
		const at = (menu().element() as HTMLElement).getBoundingClientRect();
		expect(Math.abs(at.left - (box.left + 200))).toBeLessThan(4);

		await expect.element(row('llama')).toHaveClass('bg-muted');
		await expect.element(row('olmo')).not.toHaveClass('bg-muted');
	});

	it('acts on the whole selection from a checked row of several', async () => {
		const selection = open();
		selection.click('olmo');
		selection.click('qwen');
		await expect.element(row('olmo')).toHaveAttribute('aria-selected', 'true');

		await row('qwen').click({ button: 'right' });
		await expect.element(menu().getByText('2 models', { exact: true })).toBeVisible();
		// One row's own items are left out for a selection.
		expect(menu().getByRole('menuitem', { name: 'Rename…' }).query()).toBeNull();
		await menu().getByRole('menuitem', { name: 'Delete 2 models…' }).click();
		expect(selection.action).toBe('delete');
		expect(selection.targets).toEqual(['olmo', 'qwen']);
	});

	it('acts on an unchecked row alone, whatever else is checked', async () => {
		const selection = open();
		selection.click('olmo');
		selection.click('qwen');

		await row('llama').click({ button: 'right' });
		await expect.element(menu().getByRole('menuitem', { name: 'Rename…' })).toBeVisible();
		expect(menu().getByText('2 models', { exact: true }).query()).toBeNull();
		await menu().getByRole('menuitem', { name: 'Move…' }).click();
		expect(selection.action).toBe('move');
		expect(selection.targets).toEqual(['llama']);
	});

	it('acts on a lone checked row alone', async () => {
		const selection = open();
		selection.click('olmo');

		await row('olmo').click({ button: 'right' });
		await menu().getByRole('menuitem', { name: 'Delete…' }).click();
		expect(selection.targets).toEqual(['olmo']);
	});

	it('gives a folder its own menu', async () => {
		open();
		await row('baselines').click({ button: 'right' });
		await expect.element(menu().getByRole('menuitem', { name: 'Rename…' })).toBeVisible();
		await expect.element(row('baselines')).toHaveClass('bg-muted');
	});

	it('leaves the browser’s own menu to a list with no row menus', async () => {
		resetApp('http://flock.test/models/acme');
		// No selection: a reader, whose rows have no menu.
		render(ModelList, {
			scope: { org: 'acme', project: null },
			path: '',
			folder: FOLDER,
			now: 1_790_000_100
		});
		await expect.element(row('llama')).toBeVisible();
		const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
		(row('llama').element() as HTMLElement).dispatchEvent(event);
		expect(event.defaultPrevented).toBe(false);
		expect(screen.getByRole('menu').query()).toBeNull();
	});
});
