/** Home's welcome line: picked from what's going on, the same line all day for a slot of it. */

export interface GreetingContext {
	now: Date;
	/** First name, or null; lines drop `{name}` (and its comma) when there's none. */
	name: string | null;
	/** The viewer's live runs. */
	live: { name: string; runtime: number }[];
	/** The last visit in this browser (ms), or null the first time. */
	lastVisit: number | null;
	/** Whether an easter egg already showed today, so one comes at most once a day. */
	eggToday: boolean;
	/** 0..1, for the rare lines. */
	roll: number;
}

export interface Greeting {
	text: string;
	egg: boolean;
}

const DAY = 86_400_000;

/** Fixed dates, `M-D`. */
const DATES: Record<string, string[]> = {
	'1-1': ['Happy New Year, {name}'],
	'1-5': ['Happy Bird Day, {name}'],
	'2-2': ['Happy Groundhog Day'],
	'2-15': ['Happy World Hippo Day, {name}'],
	'3-14': ['Happy π day'],
	'4-25': ['Happy Penguin Day'],
	'8-8': ['Happy Cat Day, {name}'],
	'9-19': ['Ahoy, {name}'],
	'10-1': ['Happy Coffee Day'],
	'10-31': ['Happy Halloween, {name}'],
	'12-24': ['Happy holidays, {name}'],
	'12-25': ['Merry Christmas, {name}']
};

const RARE = ['Hello, world'];

type Slot = 'early' | 'morning' | 'afternoon' | 'evening' | 'late';

const TIME: Record<Slot, string[]> = {
	early: ['Good morning', 'Good morning, {name}', 'Early start, {name}', 'Coffee and Claude time?'],
	morning: [
		'Good morning',
		'Good morning, {name}',
		'Coffee and Claude time?',
		'Hey there, {name}',
		'What’s on your mind, {name}?'
	],
	afternoon: [
		'Good afternoon',
		'Good afternoon, {name}',
		'Back at it, {name}',
		'How’s it going, {name}?',
		'What’s new?'
	],
	evening: ['Good evening', 'Good evening, {name}', 'Evening, {name}', 'How was your day, {name}?'],
	late: [
		'Hello, night owl',
		'What’s on your mind tonight?',
		'Up late, {name}?',
		'The cluster’s all yours'
	]
};

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

/** Each day's own lines, mixed in with the time of day's; late night keeps its own. */
function weekday(day: number, slot: Slot): string[] {
	if (slot === 'late') return [];
	const lines = [`Happy ${WEEKDAYS[day]}, {name}`];
	if (day === 5) lines.push('That Friday feeling');
	if (day === 6) lines.push('Welcome to the weekend, {name}');
	if (day === 0) lines.push('Sunday session, {name}?');
	return lines;
}

function liveLines(n: number): string[] {
	if (n === 0) return [];
	if (n === 1) return ['One run in flight'];
	if (n < 5) return [`${n} runs in flight`];
	return ['The flock’s busy', `${n} runs in flight, {name}`];
}

function fill(line: string, name: string | null): string {
	return name ? line.replaceAll('{name}', name) : line.replace(/,? \{name\}/g, '');
}

/** A stable pick: the same seed gives the same line, so a reload doesn't change it. */
function pick(lines: string[], seed: string): string {
	let h = 2166136261;
	for (let i = 0; i < seed.length; i++) h = Math.imul(h ^ seed.charCodeAt(i), 16777619);
	return lines[(h >>> 0) % lines.length];
}

export function greeting(ctx: GreetingContext): Greeting {
	const { now, name } = ctx;
	const h = now.getHours();
	const slot: Slot =
		h < 5
			? 'late'
			: h < 9
				? 'early'
				: h < 12
					? 'morning'
					: h < 18
						? 'afternoon'
						: h < 22
							? 'evening'
							: 'late';
	const seed = `${now.getFullYear()}-${now.getMonth() + 1}-${now.getDate()}|${slot}`;
	const say = (lines: string[], egg = false): Greeting => {
		// A line that opens on the name says nothing without it.
		const usable = name ? lines : lines.filter((l) => !l.startsWith('{name}'));
		return { text: fill(pick(usable, seed), name), egg };
	};

	if (ctx.lastVisit === null) return say(['Welcome, {name}', 'Welcome to the flock, {name}']);

	if (!ctx.eggToday) {
		const date = DATES[`${now.getMonth() + 1}-${now.getDate()}`];
		if (date) return say(date, true);
		if (h === 3) return say(['It’s 3 am, {name}'], true);
		if (ctx.live.some((r) => /final/i.test(r.name))) return say(['Is this the final one?'], true);
		if (ctx.roll < 0.01) return say(RARE, true);
	}

	const away = now.getTime() - ctx.lastVisit;
	if (away >= 14 * DAY) return say(['Been a while, {name}', '{name} returns!']);
	if (away >= 3 * DAY) return say(['Welcome back, {name}', '{name} returns!']);

	return say([...TIME[slot], ...weekday(now.getDay(), slot), ...liveLines(ctx.live.length)]);
}
