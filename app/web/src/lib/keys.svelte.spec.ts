import { afterEach, describe, expect, it, vi } from 'vitest';
import { listKeys, typing } from './keys.svelte.ts';

function list(n: number) {
	const ul = document.createElement('ul');
	for (let i = 0; i < n; i++) {
		const li = document.createElement('li');
		li.dataset.keyrow = '';
		const a = document.createElement('a');
		a.href = `#row-${i}`;
		li.append(a);
		ul.append(li);
	}
	document.body.append(ul);
	return ul;
}

const press = (key: string, target: EventTarget = window) =>
	target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));

const cursor = (ul: HTMLElement) =>
	[...ul.querySelectorAll('[data-keyrow]')].findIndex((r) => r.hasAttribute('data-cursor'));

afterEach(() => (document.body.innerHTML = ''));

describe('list keys', () => {
	it('moves a cursor with j and k, within the list', () => {
		const ul = list(3);
		const stop = listKeys()(ul);
		press('j');
		expect(cursor(ul)).toBe(0);
		press('j');
		press('j');
		press('j');
		expect(cursor(ul)).toBe(2);
		press('k');
		expect(cursor(ul)).toBe(1);
		press('Escape');
		expect(cursor(ul)).toBe(-1);
		if (typeof stop === 'function') stop();
	});

	it('opens the row in the cursor with Enter', () => {
		const ul = list(2);
		const stop = listKeys()(ul);
		const click = vi.fn((e: Event) => e.preventDefault());
		ul.querySelectorAll('a')[1].addEventListener('click', click);
		press('j');
		press('j');
		press('Enter');
		expect(click).toHaveBeenCalledOnce();
		if (typeof stop === 'function') stop();
	});

	it('leaves keys typed into a field alone', () => {
		const ul = list(2);
		const stop = listKeys()(ul);
		const input = document.createElement('input');
		document.body.append(input);
		press('j', input);
		expect(cursor(ul)).toBe(-1);
		expect(typing(new KeyboardEvent('keydown', { key: 'k', metaKey: true }))).toBe(true);
		if (typeof stop === 'function') stop();
	});
});
