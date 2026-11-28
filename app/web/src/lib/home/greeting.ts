/** Home's welcome line: picked from what's going on, the same line all day for a slot of it. */

export interface GreetingContext {
	now: Date;
	/** First name, or null; lines drop `{name}` (and its comma) when there's none. */
	name: string | null;
	/** The viewer's live runs. */
	live: { name: string; runtime: number }[];
	/** The viewer's runs that finished since the last visit. */
	landed?: number;
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
	'12-25': ['Merry Christmas, {name}'],
	'12-31': ['Last run of the year?']
};

/** Lunar dates, as `YYYY-M-D`: they move each year (2026 to 2030). */
const LUNAR: Record<string, string[]> = {};
const lunar = (lines: string[], days: string[]) => days.forEach((d) => (LUNAR[d] = lines));
lunar(
	['新年快樂, {name}', 'Happy Lunar New Year, {name}'],
	['2026-2-17', '2027-2-6', '2028-1-26', '2029-2-13', '2030-2-3']
);
lunar(['Happy Lantern Festival'], ['2026-3-3', '2027-2-20', '2028-2-9', '2029-2-27', '2030-2-17']);
lunar(
	['Happy Dragon Boat Festival, {name}'],
	['2026-6-19', '2027-6-9', '2028-5-28', '2029-6-16', '2030-6-5']
);
lunar(
	['Happy Mid-Autumn, {name}', 'Mooncakes after this run?'],
	['2026-9-25', '2027-9-15', '2028-10-3', '2029-9-22', '2030-9-12']
);

const RARE = ['Hello, world'];

type Slot = 'early' | 'morning' | 'afternoon' | 'evening' | 'late';

const TIME: Record<Slot, string[]> = {
	early: [
		'Good morning',
		'Good morning, {name}',
		'Early start, {name}',
		'Coffee and Claude time?',
		'Up with the birds, {name}',
		'Still in warmup?'
	],
	morning: [
		'Good morning',
		'Good morning, {name}',
		'Coffee and Claude time?',
		'Hey there, {name}',
		'What’s on your mind, {name}?',
		'Morning, {name}',
		'Fresh start'
	],
	afternoon: [
		'Good afternoon',
		'Good afternoon, {name}',
		'Back at it, {name}',
		'How’s it going, {name}?',
		'What’s new?',
		'Afternoon, {name}',
		'Hope lunch was good'
	],
	evening: [
		'Good evening',
		'Good evening, {name}',
		'Evening, {name}',
		'How was your day, {name}?',
		'Winding down, {name}?',
		'One more run?'
	],
	late: [
		'Hello, night owl',
		'What’s on your mind tonight?',
		'Up late, {name}?',
		'The cluster’s all yours',
		'Still here, {name}?',
		'The night shift'
	]
};

/** Now and then, in among the rest: dry, never more than a nod. */
const NODS = ['Loss looks down, {name}', 'Another epoch, another day', 'Gradients flowing?'];

/** By month, once in a while (Taiwan's seasons). */
function season(month: number): string[] {
	if (month >= 5 && month <= 7) return ['Stay cool, {name}'];
	if (month === 11 || month <= 1) return ['Warm drink weather'];
	if (month === 4) return ['Plum rain season'];
	return [];
}

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

function liveLines(live: GreetingContext['live']): string[] {
	const n = live.length;
	if (n === 0) return ['All quiet on the cluster'];
	// A run past two days earns a line of its own.
	const days = Math.floor(Math.max(...live.map((r) => r.runtime)) / 86_400);
	if (days >= 2)
		return [`One run’s on day ${days + 1}`, `${n === 1 ? 'One run' : `${n} runs`} in flight`];
	if (n === 1) return ['One run in flight'];
	if (n < 5) return [`${n} runs in flight`];
	if (n >= 20) return ['The flock’s busy', 'Full flock today', `${n} runs in flight, {name}`];
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
		const moon = LUNAR[`${now.getFullYear()}-${now.getMonth() + 1}-${now.getDate()}`];
		if (moon) return say(moon, true);
		if (h === 3) return say(['It’s 3 am, {name}'], true);
		if (ctx.live.some((r) => /final/i.test(r.name))) return say(['Is this the final one?'], true);
		if (ctx.roll < 0.01) return say(RARE, true);
	}

	const away = now.getTime() - ctx.lastVisit;
	if (away >= 14 * DAY) return say(['Been a while, {name}', '{name} returns!']);
	if (away >= 3 * DAY) return say(['Welcome back, {name}', '{name} returns!']);

	// What came in while away comes first: it's news.
	const landed = ctx.landed ?? 0;
	if (landed === 1) return say(['One run landed while you were away']);
	if (landed > 1) return say([`${landed} runs landed while you were away`]);

	return say([
		...TIME[slot],
		...weekday(now.getDay(), slot),
		...liveLines(ctx.live),
		...NODS,
		...season(now.getMonth())
	]);
}
