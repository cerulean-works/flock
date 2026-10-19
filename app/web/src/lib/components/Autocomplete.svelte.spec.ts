import { describe, expect, it } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../routes/layout.css'; // Tailwind, which places the list
import Autocomplete from './Autocomplete.svelte';

const options = [
	{ value: 'state', hint: 'state' },
	{ value: 'config.lr', hint: 'number' },
	{ value: 'config.lr_decay', hint: 'number' },
	{ value: 'summary.lr', hint: 'number' },
	{ value: 'config.opt', hint: 'text' }
];

const listed = () =>
	[...document.querySelectorAll('[role="option"]')].map((o) => o.querySelector('span')!.title);

describe('Autocomplete', () => {
	it('lists every option on focus, as wide as the input', async () => {
		render(Autocomplete, { value: '', options, placeholder: 'config.lr' });
		await page.getByRole('combobox').click();
		await expect.element(page.getByRole('listbox')).toBeVisible();
		expect(listed()).toEqual(options.map((o) => o.value));
		const input = document.querySelector('input')!.getBoundingClientRect();
		const list = document.querySelector('[role="listbox"]')!.getBoundingClientRect();
		expect(list.width).toBe(input.width);
		expect(list.left).toBe(input.left);
	});

	it('narrows as you type and picks with the keyboard', async () => {
		let picked = '';
		render(Autocomplete, { value: '', options, onValueChange: (v: string) => (picked = v) });
		await page.getByRole('combobox').click();
		await userEvent.keyboard('lr');
		expect(listed()).toEqual(['config.lr', 'config.lr_decay', 'summary.lr']);
		await userEvent.keyboard('{ArrowDown}{Enter}');
		expect(picked).toBe('config.lr_decay');
		await expect.element(page.getByRole('listbox')).not.toBeInTheDocument();
		await expect.element(page.getByRole('combobox')).toHaveValue('config.lr_decay');
	});

	it('closes on Escape without letting it reach the document', async () => {
		let escapes = 0;
		const count = (e: KeyboardEvent) => e.key === 'Escape' && escapes++;
		document.addEventListener('keydown', count);
		try {
			render(Autocomplete, { value: '', options });
			await page.getByRole('combobox').click();
			await userEvent.keyboard('{Escape}');
			await expect.element(page.getByRole('listbox')).not.toBeInTheDocument();
			expect(escapes).toBe(0);
			// With the list closed, Escape is for whatever holds the input.
			await userEvent.keyboard('{Escape}');
			expect(escapes).toBe(1);
		} finally {
			document.removeEventListener('keydown', count);
		}
	});
});
