import { describe, expect, it } from 'vitest';
import { page } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css'; // Tailwind, which sizes the scroller
import type { DatasetRows } from '#lib/types.ts';
import DatasetRowsTable from './DatasetRowsTable.svelte';

const long = 'x'.repeat(2000);

const rows = (n: number, offset = 0): DatasetRows => ({
	version: 3,
	offset,
	total: 5000,
	columns: [
		{ name: 'id', type: 'int64' },
		{ name: 'text', type: 'string' },
		{ name: 'image', type: 'binary' },
		{ name: 'meta', type: 'struct<lang: string>' }
	],
	rows: Array.from({ length: n }, (_, i) => [
		offset + i,
		i === 0 ? long : `row ${offset + i}`,
		{ $binary: 1024 + i },
		i === 1 ? null : { lang: 'en' }
	]),
	truncated: [[0, 1]]
});

function mount(data: DatasetRows) {
	const host = document.createElement('div');
	host.style.height = '400px';
	host.style.width = '900px';
	document.body.append(host);
	return render(DatasetRowsTable, { target: host, props: { page: data } });
}

describe('DatasetRowsTable', () => {
	it('heads columns with their names and types', async () => {
		mount(rows(3));
		await expect.element(page.getByRole('columnheader', { name: /id\s*int64/ })).toBeVisible();
		await expect
			.element(page.getByRole('columnheader', { name: /meta\s*struct<lang: string>/ }))
			.toBeVisible();
	});

	it('shows binary values by size, nested values as JSON and nulls as null', async () => {
		mount(rows(3));
		await expect.element(page.getByText('binary · 1,024 bytes')).toBeVisible();
		await expect.element(page.getByText('{"lang":"en"}').first()).toBeVisible();
		await expect.element(page.getByRole('button', { name: 'null' })).toBeVisible();
	});

	it('marks cut cells and opens them in full', async () => {
		mount(rows(3));
		const cut = page.getByRole('button', { name: /cut short/ });
		await expect.element(cut).toBeVisible();
		await cut.click();
		const panel = page.getByRole('region', { name: /Cell text, row 0/ });
		await expect.element(panel).toBeVisible();
		await expect.element(panel.getByText(/cut short by Flock/)).toBeVisible();
		expect(panel.element().querySelector('pre')?.textContent).toBe(`${long}…`);
		await page.getByRole('button', { name: 'Close the cell' }).click();
		await expect.element(panel).not.toBeInTheDocument();
	});

	it('renders only the rows in view', async () => {
		mount(rows(500, 1000));
		const shown = document.querySelectorAll('[role="rowgroup"] [role="row"]').length;
		expect(shown).toBeGreaterThan(5);
		expect(shown).toBeLessThan(60);
		// Row numbers count from the page's offset.
		await expect.element(page.getByRole('rowheader', { name: '1,000' })).toBeVisible();
	});
});
