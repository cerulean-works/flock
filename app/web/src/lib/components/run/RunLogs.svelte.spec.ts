import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import RunLogs from './RunLogs.svelte';

const TOTAL = 5000;

/** A training script's output: progress, metrics, a warning and a traceback near the end. */
function text(i: number): string {
	const at = new Date(Date.UTC(2026, 9, 18, 10, 0, 0) + i * 1000).toISOString().slice(0, -1);
	if (i === 0) return `${at} starting train.py on gpu-035`;
	if (i === TOTAL - 6) return `${at} [WARNING] grad norm is large: 12.5`;
	if (i === TOTAL - 5) return `${at} Traceback (most recent call last):`;
	if (i === TOTAL - 4) return `${at}   File "train.py", line 42, in <module>`;
	if (i === TOTAL - 3) return `${at}     main()`;
	if (i === TOTAL - 2) return `${at} RuntimeError: CUDA out of memory`;
	if (i % 50 === 0) return `${at} \x1b[32mINFO\x1b[0m saved checkpoint step=${i}`;
	return `${at} step ${i * 10} loss ${(3 - i / TOTAL).toFixed(4)} lr=3e-4`;
}

function mockFetch() {
	return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
		const u = new URL(String(input), location.href);
		const offset = Number(u.searchParams.get('offset'));
		const limit = Number(u.searchParams.get('limit'));
		const lines = [];
		for (let line = offset; line < Math.min(TOTAL, offset + limit); line++)
			lines.push({ line, text: text(line) });
		return new Response(JSON.stringify({ total: TOTAL, lines }));
	});
}

const rows = () => document.querySelectorAll('[data-line]');

describe('RunLogs', () => {
	beforeEach(() => page.viewport(1100, 640));
	afterEach(() => vi.restoreAllMocks());

	it('renders only the lines in view, scrolled to the end', async () => {
		mockFetch();
		render(RunLogs, { url: '/logs', total: TOTAL, live: false, start: 0, name: 'run' });
		await expect.element(page.getByText('RuntimeError: CUDA out of memory')).toBeVisible();
		// The last 1000 lines are loaded, but only a screenful (and some) are in the DOM.
		expect(rows().length).toBeGreaterThan(10);
		expect(rows().length).toBeLessThan(200);
		const last = rows()[rows().length - 1] as HTMLElement;
		expect(last.dataset.line).toBe(String(TOTAL - 1));
		// The traceback reads as an error, all of it.
		const frame = document.querySelector(`[data-line="${TOTAL - 4}"]`)!;
		expect(frame.className).toContain('border-ansi-red');
		// ANSI codes are styled, not printed.
		expect(document.body.textContent).not.toContain('\x1b');
	});

	it('finds matches and steps through them', async () => {
		mockFetch();
		render(RunLogs, { url: '/logs', total: TOTAL, live: false, start: 0, name: 'run' });
		await expect.element(page.getByText('RuntimeError: CUDA out of memory')).toBeVisible();
		await page.getByRole('textbox', { name: 'Search the log' }).click();
		await userEvent.keyboard('checkpoint');
		// One every 50 lines in the 1000 loaded.
		await expect.element(page.getByText(/^\d+ \/ 20$/)).toBeVisible();
		const at = () => Number(/(\d+) \/ 20/.exec(document.body.textContent!)![1]);
		const before = at();
		await userEvent.keyboard('{Enter}');
		await expect.poll(at).toBe((before % 20) + 1);
		await userEvent.keyboard('{Shift>}{Enter}{Enter}{/Shift}');
		await expect.poll(at).toBe(((before + 18) % 20) + 1);
		// The current match is in view.
		const current = document.querySelector('.bg-ansi-yellow')!.getBoundingClientRect();
		expect(current.top).toBeGreaterThan(0);
		expect(current.bottom).toBeLessThan(window.innerHeight);
	});
});
