import { describe, expect, it } from 'vitest';
import { greeting, type GreetingContext } from './greeting.ts';

// A plain Wednesday, mid-morning, back from yesterday.
const base: GreetingContext = {
	now: new Date(2026, 10, 25, 10, 0),
	name: 'Harry',
	live: [],
	lastVisit: new Date(2026, 10, 24, 10, 0).getTime(),
	eggToday: false,
	roll: 0.5
};

describe('greeting', () => {
	it('welcomes a first visit', () => {
		expect(greeting({ ...base, lastVisit: null }).text).toMatch(
			/^Welcome(, | to the flock, )Harry$/
		);
	});

	it('drops the name and its comma when there is none', () => {
		expect(greeting({ ...base, name: null, lastVisit: null }).text).toMatch(
			/^Welcome( to the flock)?$/
		);
	});

	it('never opens on a missing name', () => {
		const away = { ...base, name: null, lastVisit: base.now.getTime() - 20 * 86_400_000 };
		expect(greeting(away).text).toBe('Been a while');
	});

	it('gives the same line on a reload', () => {
		expect(greeting({ ...base, roll: 0.9 }).text).toBe(greeting(base).text);
	});

	it('marks a dated egg, and skips eggs once one showed today', () => {
		const hippo = { ...base, now: new Date(2026, 1, 15, 10, 0) };
		expect(greeting(hippo)).toEqual({ text: 'Happy World Hippo Day, Harry', egg: true });
		expect(greeting({ ...hippo, eggToday: true }).egg).toBe(false);
	});

	it('notices a run called final', () => {
		expect(greeting({ ...base, live: [{ name: 'sft-final_v2', runtime: 60 }] }).text).toBe(
			'Is this the final one?'
		);
	});

	it('says welcome back after a gap', () => {
		const away = { ...base, lastVisit: base.now.getTime() - 20 * 86_400_000 };
		expect(greeting(away).text).toMatch(/^(Been a while, Harry|Harry returns!)$/);
	});

	it('marks a lunar date as an egg', () => {
		const ny = greeting({ ...base, now: new Date(2027, 1, 6, 10, 0) });
		expect(ny.egg).toBe(true);
		expect(ny.text).toMatch(/新年快樂, Harry|Happy Lunar New Year, Harry/);
	});

	it('leads with runs that landed while away', () => {
		expect(greeting({ ...base, landed: 3 }).text).toBe('3 runs landed while you were away');
		expect(greeting({ ...base, landed: 1 }).text).toBe('One run landed while you were away');
	});
});
