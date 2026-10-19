import { describe, expect, it } from 'vitest';
import { NAME_W, readWidth, SIDEBAR_W } from './widths.ts';

describe('readWidth', () => {
	it('reads a saved width, kept within bounds', () => {
		expect(readWidth(NAME_W, '420')).toBe(420);
		expect(readWidth(NAME_W, '10')).toBe(NAME_W.min);
		expect(readWidth(SIDEBAR_W, '9000')).toBe(SIDEBAR_W.max);
		expect(readWidth(SIDEBAR_W, '300.6')).toBe(301);
	});

	it('falls back to the initial width', () => {
		expect(readWidth(NAME_W, undefined)).toBe(NAME_W.initial);
		expect(readWidth(SIDEBAR_W, '')).toBe(SIDEBAR_W.initial);
		expect(readWidth(SIDEBAR_W, 'wide')).toBe(SIDEBAR_W.initial);
	});
});
