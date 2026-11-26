/**
 * How a group's charts color and average its runs (D222): each Group by value gets a hue of its
 * own, its runs are shaded faint to strong by their Color by value, and repeats (runs that differ
 * only in their seed) can be drawn as one mean line with a ± std band. Pure, so it is tested
 * apart from the canvas; the workspace's charts draw what it returns (`Workspace.style`).
 */

import { formatValue } from '#lib/utils.ts';
import type { BandKind } from './aggregate.ts';

/** Key of a run without a value, which sorts and reads as such. */
export const MISSING = '\u0000missing';

/** A value as a key: equal values (`1` and `1.0`, equal lists) get equal keys. */
export const valueKey = (v: unknown): string =>
	v === undefined || v === null ? MISSING : JSON.stringify(v);

/** A value as the charts name it: `0.0003`, or `none`. */
export const valueLabel = (v: unknown): string =>
	v === undefined || v === null ? 'none' : formatValue(v);

const isNumber = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** One value of a key, as `orderValues` lists them. */
export interface OrderedValue {
	key: string;
	value: unknown;
	label: string;
}

/**
 * The distinct values a key takes, in order: ascending when they are all numbers (so a sweep's
 * values run small to large), else by label in natural order (`run-9` before `run-10`); no
 * value last.
 */
export function orderValues(values: readonly unknown[]): OrderedValue[] {
	const byKey = new Map<string, unknown>();
	for (const v of values) byKey.set(valueKey(v), v);
	const present = [...byKey].filter(([k]) => k !== MISSING);
	if (present.every(([, v]) => isNumber(v)))
		present.sort(([, a], [, b]) => (a as number) - (b as number));
	else
		present.sort(([, a], [, b]) =>
			valueLabel(a).localeCompare(valueLabel(b), 'en', { numeric: true })
		);
	const out = present.map(([key, value]) => ({ key, value, label: valueLabel(value) }));
	if (byKey.has(MISSING))
		out.push({ key: MISSING, value: undefined, label: valueLabel(undefined) });
	return out;
}

/**
 * A hue per Group by value, in this order: blue, orange, green, violet, pink, amber, red, teal.
 * The run palette's CVD-checked hues (see `workspace.svelte.ts`), with its two greens and its
 * second blue left out so neighbouring groups never look alike; lifted on a dark page.
 */
export const GROUP_HUES = {
	light: ['#2a78d6', '#eb6834', '#1baf7a', '#4a3aa7', '#e87ba4', '#eda100', '#e34948', '#127a7a'],
	dark: ['#3d8be6', '#eb6834', '#1baf7a', '#8b7cf0', '#e87ba4', '#eda100', '#ec5f5e', '#3fb8b8']
} as const;

/** The hue of runs without a Group by value. */
export const NO_GROUP_HUE = { light: '#7b88a0', dark: '#8a919c' } as const;

/** The page behind the lines, and the far end from it, that shades mix toward. */
const ENDS = {
	light: { page: '#ffffff', far: '#000000' },
	dark: { page: '#17181b', far: '#ffffff' }
} as const;

/** How far a shade mixes toward the page (faintest) and away from it (strongest). */
const FAINT = 0.62;
const STRONG = 0.38;

function rgb(hex: string): [number, number, number] {
	const n = parseInt(hex.slice(1), 16);
	return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function mix(a: string, b: string, t: number): string {
	const [x, y] = [rgb(a), rgb(b)];
	const out = x.map((c, i) => Math.round(c + (y[i] - c) * t));
	return `#${out.map((c) => c.toString(16).padStart(2, '0')).join('')}`;
}

/**
 * `hue` shaded `t` (0..1) of the way from faint to strong: 0 mixes it into the page, ½ is the
 * hue itself, 1 deepens it (light page) or lightens it (dark page), so the largest value stands
 * out most against either.
 */
export function shade(hue: string, t: number, dark = false): string {
	const { page, far } = ENDS[dark ? 'dark' : 'light'];
	const u = Math.max(0, Math.min(1, Number.isFinite(t) ? t : 0.5)) * 2 - 1;
	return u < 0 ? mix(hue, page, -u * FAINT) : mix(hue, far, u * STRONG);
}

/** A run as the group's charts see it, by page row. */
export interface StyledRun {
	/** Its Group by value (undefined without one, or without a Group by key). */
	group?: unknown;
	/** Its Color by value. */
	color?: unknown;
	/**
	 * What its configuration is (its values of every varying key but the repeat key), when
	 * repeats are averaged: runs with equal ones are one mean line. Null to draw it alone.
	 */
	config?: string | null;
	name: string;
}

export interface StyleOptions {
	/** The Group by and Color by keys' names, as a mean's label shows them. */
	groupBy: string | null;
	colorBy: string | null;
	band: BandKind;
	dark?: boolean;
}

/** What a group's charts draw differently from the workspace's line per run. */
export interface ChartStyle {
	/** Per page row, its line's color; null for the run palette (no Group by nor Color by). */
	colors: string[] | null;
	/** Runs drawn as one mean line with a band (page rows), with the line's name. */
	means: { rows: number[]; label: string }[];
	band: BandKind;
	/** What a panel's legend names in place of runs: the groups, or the Color by values. */
	legend: { key: string; label: string; color: string }[];
	/** The only page rows the charts draw ("Show best"), or null for every visible run. */
	only?: ReadonlySet<number> | null;
}

/**
 * The colors, means and legend of a group's charts (D222), or null when nothing sets them (no
 * Group by, no Color by and no means): the workspace's own colors then.
 *
 * - Each Group by value gets a hue (`GROUP_HUES`, in `orderValues` order), all runs of a group
 *   share it; without a Group by every run is of the first, blue.
 * - Within it, runs are shaded faint to strong by their Color by value's place among every
 *   value the key takes, so one value has one shade in every group.
 * - Runs with the same `config` (more than one) are one mean line, named by its Group by and
 *   Color by values.
 */
export function chartStyle(runs: readonly StyledRun[], o: StyleOptions): ChartStyle | null {
	const hues = GROUP_HUES[o.dark ? 'dark' : 'light'];
	const groups = o.groupBy ? orderValues(runs.map((r) => r.group)) : [];
	const shades = o.colorBy ? orderValues(runs.map((r) => r.color)) : [];
	const hueOf = new Map<string, string>(
		groups.map((g, i) => [
			g.key,
			g.key === MISSING ? NO_GROUP_HUE[o.dark ? 'dark' : 'light'] : hues[i % hues.length]
		])
	);
	// No value of the Color by key is neither faint nor strong.
	const present = shades.filter((s) => s.key !== MISSING);
	const tOf = new Map<string, number>(
		present.map((s, i) => [s.key, present.length > 1 ? i / (present.length - 1) : 0.5])
	);
	const toneOf = (r: StyledRun) => (o.colorBy ? (tOf.get(valueKey(r.color)) ?? 0.5) : 0.5);
	const hue = (r: StyledRun) => (o.groupBy ? hueOf.get(valueKey(r.group))! : hues[0]);
	const colors = o.groupBy || o.colorBy ? runs.map((r) => shade(hue(r), toneOf(r), o.dark)) : null;

	const byConfig = new Map<string, number[]>();
	runs.forEach((r, row) => {
		if (r.config == null) return;
		const rows = byConfig.get(r.config);
		if (rows) rows.push(row);
		else byConfig.set(r.config, [row]);
	});
	const means = [...byConfig.values()]
		.filter((rows) => rows.length > 1)
		.map((rows) => {
			const first = runs[rows[0]];
			const parts = [
				o.groupBy && `${o.groupBy} ${valueLabel(first.group)}`,
				o.colorBy && o.colorBy !== o.groupBy && `${o.colorBy} ${valueLabel(first.color)}`
			].filter(Boolean);
			const of = `mean of ${rows.length}`;
			return {
				rows,
				label: parts.length ? `${parts.join(' · ')} · ${of}` : `${first.name} · ${of}`
			};
		});

	if (!o.groupBy && !o.colorBy && !means.length) return null;
	const legend = !colors
		? []
		: o.groupBy
			? groups.map((g) => ({
					key: g.key,
					label: `${o.groupBy} ${g.label}`,
					color: hueOf.get(g.key)!
				}))
			: shades.map((s) => ({
					key: s.key,
					label: `${o.colorBy} ${s.label}`,
					color: shade(hues[0], tOf.get(s.key) ?? 0.5, o.dark)
				}));
	return { colors, means, band: o.band, legend };
}

/** A run as `bestRows` weighs it: its Group by value, its config (null alone), its metric. */
export interface RankedRun {
	group: unknown;
	config: string | null;
	value: unknown;
}

/**
 * The page rows of the best of each Group by value ("Show best"): the run with the best value of
 * the Best by metric, or with repeats averaged (`config` set), every run of the configuration
 * whose mean is best, so its mean line is what's drawn. Runs without a number never win; a
 * group none of whose runs has one shows nothing.
 */
export function bestRows(runs: readonly RankedRun[], order: 'asc' | 'desc'): Set<number> {
	const better = (a: number, b: number) => (order === 'asc' ? a < b : a > b);
	// Each group's units (a configuration, or a lone run) with their rows and numbers.
	const groups = new Map<string, Map<string, { rows: number[]; values: number[] }>>();
	runs.forEach((r, row) => {
		const g = valueKey(r.group);
		let units = groups.get(g);
		if (!units) groups.set(g, (units = new Map()));
		const u = r.config == null ? `row:${row}` : `config:${r.config}`;
		let unit = units.get(u);
		if (!unit) units.set(u, (unit = { rows: [], values: [] }));
		unit.rows.push(row);
		if (typeof r.value === 'number' && Number.isFinite(r.value)) unit.values.push(r.value);
	});
	const out = new Set<number>();
	for (const units of groups.values()) {
		let best: { rows: number[]; score: number } | null = null;
		for (const unit of units.values()) {
			if (!unit.values.length) continue;
			const score = unit.values.reduce((a, b) => a + b, 0) / unit.values.length;
			if (!best || better(score, best.score)) best = { rows: unit.rows, score };
		}
		best?.rows.forEach((row) => out.add(row));
	}
	return out;
}
