import { describe, expect, it, vi } from 'vitest';
import { page as screen, userEvent } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../routes/layout.css';
import HeaderMenu from './HeaderMenu.svelte';

/** Opens the menu from the keyboard, as bits menus open reliably in tests. */
async function open(name: string | RegExp) {
	const trigger = screen.getByRole('button', { name });
	(trigger.element() as HTMLElement).focus();
	await userEvent.keyboard(' ');
	return trigger;
}

describe('HeaderMenu', () => {
	it('sorts either way, and clears a sort only while the column sorts the list', async () => {
		const onsort = vi.fn();
		const onclear = vi.fn();
		const view = render(HeaderMenu, { label: 'Loss', sort: { dir: null, onsort, onclear } });
		await open('Sort loss');
		await expect
			.element(screen.getByRole('menuitemradio', { name: 'Sort ascending' }))
			.toBeVisible();
		expect(screen.getByRole('menuitem', { name: 'Clear sort' }).query()).toBeNull();
		await screen.getByRole('menuitemradio', { name: 'Sort descending' }).click();
		expect(onsort).toHaveBeenCalledWith('desc');

		await view.rerender({ label: 'Loss', sort: { dir: 'desc', onsort, onclear } });
		await open('Sort loss, descending');
		await expect
			.element(screen.getByRole('menuitemradio', { name: 'Sort descending' }))
			.toHaveAttribute('aria-checked', 'true');
		await screen.getByRole('menuitem', { name: 'Clear sort' }).click();
		expect(onclear).toHaveBeenCalled();
	});

	it('offers Best by under the sort, checked on the column it reads', async () => {
		const onbest = vi.fn();
		render(HeaderMenu, {
			label: 'Loss',
			sort: { dir: null, onsort: vi.fn() },
			best: { active: true, order: 'asc', onbest }
		});
		await open('Sort loss');
		await expect
			.element(screen.getByRole('menuitemradio', { name: 'Best is lowest' }))
			.toHaveAttribute('aria-checked', 'true');
		await screen.getByRole('menuitemradio', { name: 'Best is highest' }).click();
		expect(onbest).toHaveBeenCalledWith('desc');
	});

	it('without a sort, opens on its options and lists other columns to rank by', async () => {
		const onpick = vi.fn();
		render(HeaderMenu, {
			label: 'Loss',
			best: {
				active: true,
				order: 'asc',
				onbest: vi.fn(),
				keys: ['summary.val/loss', 'summary.acc'],
				onpick
			}
		});
		await open('Loss options');
		await expect
			.element(screen.getByRole('menuitem', { name: 'Rank by another column' }))
			.toBeVisible();
		expect(screen.getByRole('menuitemradio', { name: 'Sort ascending' }).query()).toBeNull();
	});
});
