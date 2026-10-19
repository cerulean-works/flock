import { describe, expect, it } from 'vitest';
import {
	avatarFileError,
	dateTime,
	formatValue,
	duration,
	formatMetric,
	formatNumber,
	formatRuntime,
	formatTick,
	gpus,
	hours,
	money,
	monthName,
	monthOf,
	orgNameError,
	parseTags,
	projectNameError,
	relativeTime,
	shiftMonth
} from './utils.ts';

describe('formatNumber', () => {
	it.each([
		[0, '0'],
		[151936, '151,936'],
		[0.123456, '0.12346'],
		[1e-6, '1e-6'],
		[4.2e9, '4,200,000,000'],
		[4212300000.5, '4.212e9'],
		[NaN, '–']
	])('%s → %s', (v, out) => expect(formatNumber(v)).toBe(out));
});

describe('formatMetric', () => {
	it('honours the metric format', () => {
		expect(formatMetric(87.456, 'pct')).toBe('87.46%');
		expect(formatMetric(9123, 'si')).toBe('9.123k');
		expect(formatMetric(2e-6, 'exp')).toBe('2e-6');
		expect(formatMetric(1234.6, 'int')).toBe('1,235');
	});
});

describe('formatTick', () => {
	it('derives precision from the tick step', () => {
		expect(formatTick(0.25, 0.05, 'num')).toBe('0.25');
		expect(formatTick(1, 0.5, 'num')).toBe('1.0');
		expect(formatTick(20_000, 5_000, 'num')).toBe('20k');
		expect(formatTick(4e-6, 2e-6, 'exp')).toBe('4e-6');
	});
});

describe('time', () => {
	const now = Date.UTC(2026, 9, 17, 9, 0) / 1000; // Flock reports seconds
	it('formats relative times against a fixed clock', () => {
		expect(relativeTime(now - 3 * 3600, now)).toBe('3h ago');
		expect(relativeTime(now - 22 * 86_400, now)).toBe('3w ago');
	});
	it('formats durations and dates', () => {
		expect(duration(6 * 3600 + 12 * 60)).toBe('6h 12m');
		expect(duration(45)).toBe('45s');
		expect(dateTime(Date.UTC(2026, 8, 5, 10, 37) / 1000)).toBe("Sep 05 '26 10:37");
	});
});

describe('formatValue', () => {
	it('renders JSON cells', () => {
		expect(formatValue(['baseline', 'grpo'])).toBe('baseline · grpo');
		expect(formatValue({ _type: 'image-file' })).toBe('[image-file]');
		expect(formatValue(true)).toBe('true');
		expect(formatValue(null)).toBe('–');
		expect(formatValue(0.5)).toBe('0.5');
	});
});

describe('formatRuntime', () => {
	it.each([
		[0, 1, '0'],
		[45, 15, '45s'],
		[90, 30, '1m 30s'],
		[3600, 900, '1h'],
		[5400, 1800, '1h 30m'],
		[90_000, 3600, '1d 1h'],
		[1.5, 0.5, '1.5s']
	])('%s s (ticks every %s) → %s', (v, step, out) => {
		expect(formatRuntime(v, step)).toBe(out);
	});
});

describe('parseTags', () => {
	it('splits on commas and spaces and drops duplicates', () => {
		expect(parseTags(' baseline, lr-sweep  baseline,,ablation ')).toEqual([
			'baseline',
			'lr-sweep',
			'ablation'
		]);
		expect(parseTags('')).toEqual([]);
	});
});

describe('projectNameError', () => {
	it('accepts wandb project names', () => {
		expect(projectNameError('llama-3.2 sft')).toBeNull();
	});

	it.each(['', '  ', ' padded', 'a/b', 'a:b', 'what?', 'x'.repeat(129)])('rejects %j', (name) =>
		expect(projectNameError(name)).toBeTypeOf('string')
	);
});

describe('orgNameError', () => {
	it('accepts names the dashboard can open', () => {
		expect(orgNameError('cerulean-research')).toBeNull();
	});

	it.each(['admin', 'Login', 'orgs', '_app', 'favicon.ico', 'a/b', ''])('rejects %j', (name) =>
		expect(orgNameError(name)).toBeTypeOf('string')
	);
});

describe('money', () => {
	it.each([
		[12345.67, 'USD', '$12,346'],
		[99.5, 'USD', '$99.50'],
		[0, 'USD', '$0'],
		[25, 'USD', '$25'],
		[2.5, 'USD', '$2.50'],
		[1_000_000, 'TWD', 'NT$1,000,000'],
		[-1200, 'USD', '-$1,200'],
		[250, 'EUR', '€250']
	])('%d %s → %s', (v, currency, out) => expect(money(v, currency)).toBe(out));
});

describe('hours', () => {
	it.each([
		[205.8, '206'],
		[12, '12'],
		[3.46, '3.5'],
		[1520.5, '1,521']
	])('%d → %s', (h, out) => expect(hours(h)).toBe(out));
});

describe('gpus', () => {
	it('names the count and type', () => {
		expect(gpus('H200', 8)).toBe('8 × H200');
		expect(gpus('H200', 1)).toBe('H200');
		expect(gpus(null, 2)).toBe('2 × GPU');
	});

	it('is null without GPUs', () => {
		expect(gpus('H200', 0)).toBeNull();
		expect(gpus(null, null)).toBeNull();
	});
});

describe('months', () => {
	it('names a month', () => expect(monthName('2026-10')).toBe('October 2026'));

	it('steps across years', () => {
		expect(shiftMonth('2026-01', -1)).toBe('2025-12');
		expect(shiftMonth('2026-12', 1)).toBe('2027-01');
	});

	it('takes the UTC month of a time', () => {
		expect(monthOf(Date.UTC(2026, 9, 31, 23, 59) / 1000)).toBe('2026-10');
		expect(monthOf(Date.UTC(2026, 10, 1) / 1000)).toBe('2026-11');
	});
});

describe('avatarFileError', () => {
	const file = (size: number, type: string) => new File([new Uint8Array(size)], 'a', { type });

	it('accepts Flock’s image types up to 1 MB', () => {
		for (const type of ['image/png', 'image/jpeg', 'image/gif', 'image/webp'])
			expect(avatarFileError(file(1024, type))).toBeNull();
		expect(avatarFileError(file(1024 * 1024, 'image/png'))).toBeNull();
	});

	it.each([
		['nothing', null],
		['an empty file', file(0, 'image/png')],
		['an SVG', file(10, 'image/svg+xml')],
		['a larger image', file(1024 * 1024 + 1, 'image/png')]
	])('refuses %s', (_, f) => expect(avatarFileError(f)).toBeTypeOf('string'));
});
