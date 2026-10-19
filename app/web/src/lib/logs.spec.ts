import { describe, expect, it } from 'vitest';
import {
	ansi,
	blocksOf,
	elapsed,
	levelOf,
	parseDict,
	parseLine,
	prefixOf,
	progressOf
} from './logs.ts';

const lines = (...texts: string[]) => texts.map((text, line) => parseLine({ line, text }));

describe('parseLine', () => {
	it('splits off wandb’s timestamp', () => {
		const l = parseLine({ line: 0, text: '2026-10-18T05:08:09.123456 starting' });
		expect(l.text).toBe('starting');
		expect(l.at).toBeCloseTo(Date.UTC(2026, 9, 18, 5, 8, 9, 123) / 1000, 2);
	});

	it('reads wandb’s stderr marker', () => {
		const l = parseLine({ line: 0, text: 'ERROR 2026-10-18T05:08:09.1 UserWarning: x' });
		expect(l).toMatchObject({ stderr: true, text: 'UserWarning: x', level: 'warning' });
		expect(parseLine({ line: 0, text: '2026-10-18T05:08:09.1 ok' }).stderr).toBe(false);
	});

	it('keeps a line without one', () => {
		expect(parseLine({ line: 3, text: 'hello' })).toMatchObject({ at: null, text: 'hello' });
	});

	it('keeps the last carriage-return redraw', () => {
		const l = parseLine({ line: 0, text: ' 10%|█ | 1/10\r 50%|█████ | 5/10\r' });
		expect(l.progress).toEqual({ label: '', percent: 50, rest: '5/10' });
	});
});

describe('ansi', () => {
	it('styles colored runs and drops other escapes', () => {
		const { text, spans } = ansi('\x1b[2K\x1b[1;31mfailed\x1b[0m ok');
		expect(text).toBe('failed ok');
		expect(spans).toEqual([{ text: 'failed', bold: true, color: 'red' }, { text: ' ok' }]);
	});

	it('skips 256-color arguments', () => {
		expect(ansi('\x1b[38;5;208mx\x1b[39m').spans).toEqual([{ text: 'x' }]);
	});
});

describe('parseDict', () => {
	it('reads Python reprs, quoted numbers as numbers', () => {
		expect(
			parseDict("{'loss': '2.573', 'epoch': 0.15, 'ok': True, 'x': None, 'lr': 9.4e-05}")
		).toEqual([
			['loss', 2.573],
			['epoch', 0.15],
			['ok', true],
			['x', null],
			['lr', 9.4e-5]
		]);
	});

	it('reads JSON and nested values', () => {
		expect(parseDict('{"a": [1, 2], "b": {"c": "d"}}')).toEqual([
			['a', [1, 2]],
			['b', { c: 'd' }]
		]);
	});

	it('refuses anything else', () => {
		expect(parseDict('{not a dict}')).toBeNull();
		expect(parseDict("{'a': 1} trailing")).toBeNull();
		expect(parseDict('{}')).toBeNull();
		expect(parseDict('step 3 loss 2')).toBeNull();
	});
});

describe('levelOf and progressOf', () => {
	it('finds levels near the start', () => {
		expect(levelOf('2026 | INFO | ready')).toBe('info');
		expect(levelOf('[WARNING] slow')).toBe('warning');
		expect(levelOf('UserWarning: deprecated')).toBe('warning');
		expect(levelOf('RuntimeError: CUDA out of memory')).toBe('error');
		expect(levelOf('E1018 12:00:00 failed')).toBe('error');
		expect(levelOf('the loss is fine')).toBeNull();
	});

	it('finds the logger prefix to mute', () => {
		const a = '2026-10-18 12:46:17,934 | ERROR | trainer | NCCL timed out';
		expect(a.slice(prefixOf(a))).toBe('NCCL timed out');
		const b = '/x/train.py:10: UserWarning: deprecated';
		expect(levelOf(b)).toBe('warning');
		expect(b.slice(prefixOf(b))).toBe('UserWarning: deprecated');
		expect(prefixOf('INFO: ready')).toBe(6);
		expect(prefixOf('nothing here')).toBe(0);
	});

	it('reads tqdm bars', () => {
		expect(progressOf('Epoch 1:  45%|████▌     | 450/1000 [00:12<00:15, 36.1it/s]')).toEqual({
			label: 'Epoch 1',
			percent: 45,
			rest: '450/1000 [00:12<00:15, 36.1it/s]'
		});
	});
});

describe('blocksOf', () => {
	it('tables runs of dicts with the same keys', () => {
		const b = blocksOf(lines("{'a': 1, 'b': 2}", "{'a': 3, 'b': 4}", "{'a': 5}", 'text'));
		expect(b.map((x) => x.kind)).toEqual(['table', 'line', 'line']);
		expect(b[0]).toMatchObject({ keys: ['a', 'b'] });
	});

	it('groups a traceback with its exception', () => {
		const b = blocksOf(
			lines(
				'before',
				'Traceback (most recent call last):',
				'  File "train.py", line 3, in <module>',
				'    main()',
				'ValueError: bad',
				'after'
			)
		);
		expect(b.map((x) => x.kind)).toEqual(['line', 'traceback', 'line']);
		expect(b[1].kind === 'traceback' && b[1].error.text).toBe('ValueError: bad');
	});

	it('keeps chained tracebacks together', () => {
		const b = blocksOf(
			lines(
				'Traceback (most recent call last):',
				'  File "a.py", line 1',
				'KeyError: x',
				'',
				'During handling of the above exception, another exception occurred:',
				'',
				'Traceback (most recent call last):',
				'  File "b.py", line 2',
				'RuntimeError: y',
				''
			)
		);
		expect(b[0].kind === 'traceback' && b[0].error.text).toBe('RuntimeError: y');
		expect(b.map((x) => x.kind)).toEqual(['traceback', 'line']);
	});
});

it('elapsed', () => expect(elapsed(3725, 0)).toBe('+1:02:05'));
