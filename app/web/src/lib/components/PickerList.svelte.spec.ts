import { createRawSnippet } from 'svelte';
import { describe, expect, it, vi } from 'vitest';
import { page as screen, userEvent } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../routes/layout.css';
import PickerList from './PickerList.svelte';

// The props say `unknown` options: render can't carry the component's type parameter.
const row = createRawSnippet((name: () => unknown) => ({ render: () => `<span>${name()}</span>` }));

/** A list of three folders, with its search box focused and every key answered. */
function folders(options = ['alpha', 'beta', 'gamma']) {
	const keys = { onpick: vi.fn(), onup: vi.fn(), onback: vi.fn() };
	const view = render(PickerList, {
		options,
		key: String,
		row,
		noun: 'folders',
		label: 'Search folders',
		placeholder: 'Search folders…',
		...keys
	});
	// Its field (PickerField) focuses the box on open; alone, the test does.
	const box = screen.getByRole('combobox', { name: 'Search folders' });
	(box.element() as HTMLElement).focus();
	return { ...keys, view, box };
}

const active = () => screen.getByRole('option', { selected: true });

describe('PickerList', () => {
	it('moves with the arrows, wrapping at both ends, and Enter picks the active option', async () => {
		const { onpick, box } = folders();
		await expect.element(box).toHaveFocus();
		await expect.element(active()).toHaveTextContent('alpha');

		await userEvent.keyboard('{ArrowUp}');
		await expect.element(active()).toHaveTextContent('gamma');
		await userEvent.keyboard('{ArrowDown}');
		await expect.element(active()).toHaveTextContent('alpha');
		await userEvent.keyboard('{ArrowDown}');
		await expect.element(active()).toHaveTextContent('beta');
		// Screen readers follow the keys through the search box.
		await expect.element(box).toHaveAttribute('aria-activedescendant', active().element().id);

		await userEvent.keyboard('{Enter}');
		expect(onpick).toHaveBeenCalledExactlyOnceWith('beta');
	});

	it('leaves an input method its keys while it composes', async () => {
		const { onpick, box } = folders();
		await expect.element(box).toHaveFocus();
		const element = box.element();
		for (const key of ['ArrowDown', 'Enter'])
			element.dispatchEvent(
				new KeyboardEvent('keydown', { key, isComposing: true, bubbles: true })
			);
		await expect.element(active()).toHaveTextContent('alpha');
		expect(onpick).not.toHaveBeenCalled();
	});

	it('goes up a folder on Backspace only in an empty box, and back a level on Escape', async () => {
		const { onup, onback, box } = folders();
		await box.fill('a');
		await userEvent.keyboard('{Backspace}');
		expect(onup).not.toHaveBeenCalled(); // it deleted the letter
		await expect.element(box).toHaveValue('');
		await userEvent.keyboard('{Backspace}');
		expect(onup).toHaveBeenCalledOnce();

		await userEvent.keyboard('{Escape}');
		expect(onback).toHaveBeenCalledOnce();
	});

	it('shows loading, then an error as an alert, and says why nothing is listed', async () => {
		const { view, box } = folders([]);
		await view.rerender({ loading: true });
		await expect.element(screen.getByText('Loading…')).toBeVisible();

		await view.rerender({ loading: false, error: 'Couldn’t list the folders (500)' });
		await expect
			.element(screen.getByRole('alert'))
			.toHaveTextContent('Couldn’t list the folders (500)');

		await view.rerender({ error: null });
		await expect.element(screen.getByText('No folders here.')).toBeVisible();
		await box.fill('zz');
		await expect.element(screen.getByText('No folders match.')).toBeVisible();
	});
});
