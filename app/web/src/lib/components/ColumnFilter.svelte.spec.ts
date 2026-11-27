import { describe, expect, it, vi } from 'vitest';
import { page as screen, userEvent } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../routes/layout.css';
import ColumnFilter from './ColumnFilter.svelte';
import ColumnFilterPanel from './ColumnFilterPanel.svelte';

const STATES = [
	{ value: 'running', label: 'Running', count: 3, dot: 'bg-state-running' },
	{ value: 'finished', label: 'Finished', count: 12 },
	{ value: 'failed', label: 'Failed', count: 1 }
];

/** The panel applies its draft after it goes: unmount, then let the queued microtask run. */
async function close(view: { unmount: () => void }) {
	view.unmount();
	await new Promise((r) => setTimeout(r, 0));
}

const base = () => ({ onclear: vi.fn(), onclose: vi.fn() });

describe('ColumnFilterPanel', () => {
	it('values: ticks several, applies them once when it closes', async () => {
		const onselect = vi.fn();
		const view = render(ColumnFilterPanel, {
			...base(),
			label: 'State',
			active: false,
			values: STATES,
			onselect
		});
		await screen.getByRole('checkbox', { name: /Running/ }).click();
		await screen.getByRole('checkbox', { name: /Failed/ }).click();
		// Nothing applied while the list is open: applying reloads the runs under it.
		expect(onselect).not.toHaveBeenCalled();
		await close(view);
		expect(onselect).toHaveBeenCalledTimes(1);
		expect(onselect).toHaveBeenCalledWith(['running', 'failed']);
	});

	it('values: closing unchanged applies nothing', async () => {
		const p = { ...base(), onselect: vi.fn() };
		const view = render(ColumnFilterPanel, {
			...p,
			label: 'State',
			active: true,
			values: STATES,
			selected: ['finished']
		});
		await expect.element(screen.getByRole('checkbox', { name: /Finished/ })).toBeChecked();
		await close(view);
		expect(p.onselect).not.toHaveBeenCalled();
		expect(p.onclear).not.toHaveBeenCalled();
	});

	it('values: unticking everything on an active filter clears it', async () => {
		const p = { ...base(), onselect: vi.fn() };
		const view = render(ColumnFilterPanel, {
			...p,
			label: 'State',
			active: true,
			values: STATES,
			selected: ['finished']
		});
		await screen.getByRole('checkbox', { name: /Finished/ }).click();
		await close(view);
		expect(p.onclear).toHaveBeenCalledTimes(1);
		expect(p.onselect).not.toHaveBeenCalled();
	});

	it('values: keeps a chosen value listed when no run has it now', async () => {
		render(ColumnFilterPanel, {
			...base(),
			label: 'State',
			active: true,
			values: STATES,
			selected: ['crashed']
		});
		await expect.element(screen.getByRole('checkbox', { name: /crashed/ })).toBeChecked();
	});

	it('values: Select all and Clear act on the draft', async () => {
		const onselect = vi.fn();
		const view = render(ColumnFilterPanel, {
			...base(),
			label: 'State',
			active: false,
			values: STATES,
			onselect
		});
		await screen.getByRole('button', { name: 'Select all' }).click();
		for (const s of STATES)
			await expect
				.element(screen.getByRole('checkbox', { name: new RegExp(s.label) }))
				.toBeChecked();
		await screen.getByRole('button', { name: 'Clear' }).click();
		await expect.element(screen.getByRole('checkbox', { name: /Running/ })).not.toBeChecked();
		await screen.getByRole('button', { name: 'Select all' }).click();
		await close(view);
		expect(onselect).toHaveBeenCalledWith(['running', 'finished', 'failed']);
	});

	it('values: searches long lists, and Select matching takes only what shows', async () => {
		const values = Array.from({ length: 10 }, (_, i) => ({
			value: `host-${i}`,
			label: `host-${i}`
		}));
		const onselect = vi.fn();
		const view = render(ColumnFilterPanel, {
			...base(),
			label: 'Host',
			active: false,
			values,
			onselect
		});
		const search = screen.getByRole('textbox', { name: 'Search host values' });
		await search.fill('host-1');
		await expect.element(screen.getByRole('checkbox', { name: /host-2/ })).not.toBeInTheDocument();
		await screen.getByRole('button', { name: 'Select matching' }).click();
		await close(view);
		expect(onselect).toHaveBeenCalledWith(['host-1']);
	});

	it('values: short lists have no search', async () => {
		render(ColumnFilterPanel, { ...base(), label: 'State', active: false, values: STATES });
		await expect.element(screen.getByRole('checkbox', { name: /Running/ })).toBeVisible();
		expect(screen.getByRole('textbox').query()).toBeNull();
	});

	it('range: at least / at most, applied on close', async () => {
		const onrange = vi.fn();
		const view = render(ColumnFilterPanel, {
			...base(),
			label: 'Loss',
			active: false,
			kind: 'range',
			onrange
		});
		await screen.getByLabelText('At least').fill('0.5');
		await screen.getByLabelText('At most').fill('2');
		expect(onrange).not.toHaveBeenCalled();
		await close(view);
		expect(onrange).toHaveBeenCalledWith({ min: '0.5', max: '2' });
	});

	it('range: Enter in a field closes the panel', async () => {
		const p = base();
		render(ColumnFilterPanel, { ...p, label: 'Loss', active: false, kind: 'range' });
		await screen.getByLabelText('At least').fill('1');
		await userEvent.keyboard('{Enter}');
		expect(p.onclose).toHaveBeenCalled();
	});

	it('range: dates read From and To', async () => {
		render(ColumnFilterPanel, {
			...base(),
			label: 'Created',
			active: false,
			kind: 'range',
			date: true
		});
		await expect.element(screen.getByLabelText('From')).toHaveAttribute('type', 'datetime-local');
		await expect.element(screen.getByLabelText('To')).toBeVisible();
	});

	it('range: emptied while active clears', async () => {
		const p = { ...base(), onrange: vi.fn() };
		const view = render(ColumnFilterPanel, {
			...p,
			label: 'Loss',
			active: true,
			kind: 'range',
			range: { min: '1' }
		});
		await screen.getByLabelText('At least').fill('');
		await close(view);
		expect(p.onclear).toHaveBeenCalled();
		expect(p.onrange).not.toHaveBeenCalled();
	});

	it('text: contains, trimmed, applied on close; Enter closes', async () => {
		const p = { ...base(), ontext: vi.fn() };
		const view = render(ColumnFilterPanel, { ...p, label: 'Host', active: false, kind: 'text' });
		await screen.getByRole('textbox', { name: 'Host contains' }).fill('  gpu-0 ');
		await userEvent.keyboard('{Enter}');
		expect(p.onclose).toHaveBeenCalled();
		await close(view);
		expect(p.ontext).toHaveBeenCalledWith('gpu-0');
	});

	it('Clear in the header (active only) closes and clears', async () => {
		const p = { ...base(), ontext: vi.fn() };
		const view = render(ColumnFilterPanel, {
			...p,
			label: 'Host',
			active: true,
			kind: 'text',
			text: 'gpu'
		});
		await screen.getByRole('button', { name: 'Clear' }).click();
		expect(p.onclose).toHaveBeenCalled();
		await close(view);
		expect(p.onclear).toHaveBeenCalledTimes(1);
		expect(p.ontext).not.toHaveBeenCalled();
	});

	it('offers Back when it is a step of a column list', async () => {
		const onback = vi.fn();
		render(ColumnFilterPanel, { ...base(), label: 'State', active: false, values: STATES, onback });
		await screen.getByRole('button', { name: 'Back to the columns' }).click();
		expect(onback).toHaveBeenCalled();
	});
});

describe('ColumnFilter', () => {
	it('opens from its funnel and applies the ticks when the popover closes', async () => {
		const onselect = vi.fn();
		render(ColumnFilter, {
			label: 'State',
			active: false,
			values: STATES,
			onselect,
			onclear: vi.fn()
		});
		const trigger = screen.getByRole('button', { name: 'Filter state' });
		(trigger.element() as HTMLElement).focus();
		await userEvent.keyboard(' ');
		await screen.getByRole('checkbox', { name: /Failed/ }).click();
		expect(onselect).not.toHaveBeenCalled();
		await userEvent.keyboard('{Escape}');
		await expect.element(screen.getByRole('checkbox', { name: /Failed/ })).not.toBeInTheDocument();
		await expect.poll(() => onselect.mock.calls.length).toBe(1);
		expect(onselect).toHaveBeenCalledWith(['failed']);
	});

	it('Enter in a text filter closes the popover and applies', async () => {
		const ontext = vi.fn();
		render(ColumnFilter, { label: 'Host', active: false, kind: 'text', ontext, onclear: vi.fn() });
		const trigger = screen.getByRole('button', { name: 'Filter host' });
		(trigger.element() as HTMLElement).focus();
		await userEvent.keyboard(' ');
		await screen.getByRole('textbox', { name: 'Host contains' }).fill('node');
		await userEvent.keyboard('{Enter}');
		await expect.poll(() => ontext.mock.calls.length).toBe(1);
		expect(ontext).toHaveBeenCalledWith('node');
	});
});
