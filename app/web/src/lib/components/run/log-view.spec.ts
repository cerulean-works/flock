import { describe, expect, it } from 'vitest';
import { ansi, parseLine } from '#lib/logs.ts';
import {
	levelCounts,
	levelsOf,
	lineFromHash,
	matchesIn,
	pieces,
	search,
	tokenize
} from './log-view.ts';

const lines = (...texts: string[]) => texts.map((text, line) => parseLine({ line, text }));

/** Each token as `kind:text`. */
const tokens = (text: string) =>
	tokenize(text).map((t) => `${t.kind}:${text.slice(t.start, t.end)}`);

describe('levelsOf', () => {
	it('keeps loggers’ levels and marks every traceback line as an error', () => {
		const ls = lines(
			'INFO starting',
			'Traceback (most recent call last):',
			'  File "train.py", line 3, in <module>',
			'    main()',
			'ValueError: bad',
			'[WARNING] slow',
			'plain'
		);
		expect(levelsOf(ls)).toEqual(['info', 'error', 'error', 'error', 'error', 'warning', null]);
	});

	it('counts dict lines grouped as tables one each', () => {
		const ls = lines("{'a': 1}", "{'a': 2}", 'ERROR boom');
		expect(levelsOf(ls)).toEqual([null, null, 'error']);
	});

	it('marks a traceback cut short by the page’s end', () => {
		expect(levelsOf(lines('Traceback (most recent call last):'))).toEqual(['error']);
	});

	it('counts levels', () => {
		expect(levelCounts(['error', null, 'info', null])).toEqual({
			error: 1,
			warning: 0,
			info: 1,
			debug: 0,
			none: 2
		});
	});
});

describe('tokenize', () => {
	it('picks out step/loss style metrics', () => {
		expect(tokens('step 220 loss 2.4093')).toEqual([
			'key:step',
			'number:220',
			'key:loss',
			'number:2.4093'
		]);
	});

	it('reads key=value and key: value', () => {
		expect(tokens('lr=3e-4 batch_size=32 train/loss: 1.5')).toEqual([
			'key:lr',
			'number:3e-4',
			'key:batch_size',
			'number:32',
			'key:train/loss',
			'number:1.5'
		]);
	});

	it('leaves names with digits and prose alone', () => {
		expect(tokens('starting train.py on gpu-035')).toEqual([]);
		expect(tokens('running on 8 GPUs')).toEqual(['number:8']);
		expect(tokens('version 1.2.3')).toEqual([]);
	});

	it('finds times, levels, URLs and strings', () => {
		expect(tokens('10:43:19 INFO see https://example.com/x for "details"')).toEqual([
			'time:10:43:19',
			'info:INFO',
			'url:https://example.com/x',
			'string:"details"'
		]);
		expect(tokens('an error happened')).toEqual([]);
	});

	it('reads printed dicts', () => {
		expect(tokens("{'loss': '2.573', 'epoch': 0.15}")).toEqual([
			"key:'loss'",
			"number:'2.573'",
			"key:'epoch'",
			'number:0.15'
		]);
	});

	it('reads traceback frames', () => {
		expect(tokens('  File "train.py", line 3, in <module>')).toEqual([
			'string:"train.py"',
			'key:line',
			'number:3'
		]);
	});

	it('leaves apostrophes in prose alone', () => {
		expect(tokens("don't stop, it's fine")).toEqual([]);
	});
});

describe('search', () => {
	it('matches case-insensitively, without overlaps', () => {
		expect(matchesIn('Loss loss LOSS', 'loss')).toEqual([0, 5, 10]);
		expect(matchesIn('aaaa', 'aa')).toEqual([0, 2]);
		expect(matchesIn('anything', '')).toEqual([]);
	});

	it('lists matches across lines, up to a cap', () => {
		const ls = lines('a loss', 'nothing', 'loss loss');
		expect(search(ls, 'loss')).toEqual([
			{ row: 0, start: 2 },
			{ row: 2, start: 0 },
			{ row: 2, start: 5 }
		]);
		expect(search(ls, 'loss', 2)).toHaveLength(2);
	});
});

describe('pieces', () => {
	const kinds = (ps: ReturnType<typeof pieces>) =>
		ps.map((p) => [p.text, p.color ?? null, p.token ?? null, p.mark ?? null, !!p.dim]);

	it('combines ANSI styles, tokens and matches', () => {
		const { text, spans } = ansi('\x1b[31mloss\x1b[0m 2.5');
		const ps = pieces(spans, tokenize(text), [{ start: 2, end: 6, current: true }]);
		expect(kinds(ps)).toEqual([
			['lo', 'red', 'key', null, false],
			['ss', 'red', 'key', 'current', false],
			[' ', null, null, 'current', false],
			['2', null, 'number', 'current', false],
			['.5', null, 'number', null, false]
		]);
	});

	it('marks the logger prefix as dim', () => {
		const { spans } = ansi('INFO: ready');
		const ps = pieces(spans, [], [], 6);
		expect(ps).toEqual([{ text: 'INFO: ', dim: true }, { text: 'ready' }]);
	});

	it('handles empty lines', () => {
		expect(pieces([], [], [])).toEqual([]);
	});
});

it('lineFromHash', () => {
	expect(lineFromHash('#L42')).toBe(41);
	expect(lineFromHash('#L0')).toBeNull();
	expect(lineFromHash('#top')).toBeNull();
	expect(lineFromHash('')).toBeNull();
});
