/**
 * The create page's Auto tab: the assistant proposes groups in a conversation, and the user
 * creates the ones they keep. Pure, so it is tested apart from the page: how a proposal's
 * coverage grid is laid out and tinted, which color each new group gets, what a create call
 * sends, and what the empty conversation offers to ask.
 */

import { GROUP_HUES, valueKey } from '#lib/chart/grouping.ts';
import {
	classifyColumns,
	configOf,
	displayKey,
	keysOf,
	type BestOrder,
	type Proposal,
	type ProposalCell,
	type ProposalGrid,
	type RunRow
} from '#lib/groups.ts';

/** A cell of the laid-out grid: its runs, and how good its best is (0 worst, 1 best). */
export interface GridCell extends ProposalCell {
	/** Null when the cell has no best value (or no metric ranks the group). */
	strength: number | null;
	isBest: boolean;
}

/** A proposal's grid as rows of cells (`null`: a combination no run covers). */
export interface GridLayout {
	xs: unknown[];
	/** `[null]` for a one-axis grid: one row. */
	ys: unknown[];
	rows: (GridCell | null)[][];
	/** Cells with runs, and every combination of the axes' values. */
	covered: number;
	possible: number;
}

/**
 * How good each cell's best is, from 0 (the worst cell) to 1 (the best), by `order` (lowest
 * best when `asc`): its rank among the cells, so a single outlier doesn't wash the rest out to
 * one shade. Equal values share a rank; all equal are all 1. A cell without a value, or a grid
 * no metric ranks (`order` null), has none.
 */
export function strengths(
	cells: readonly Pick<ProposalCell, 'best'>[],
	order: BestOrder | null
): (number | null)[] {
	const values = cells.map((c) => (order && Number.isFinite(c.best) ? (c.best as number) : null));
	const distinct = [...new Set(values.filter((v) => v !== null))].sort((a, b) =>
		order === 'asc' ? b - a : a - b
	);
	if (distinct.length <= 1) return values.map((v) => (v === null ? null : 1));
	const rank = new Map(distinct.map((v, i) => [v, i / (distinct.length - 1)]));
	return values.map((v) => (v === null ? null : rank.get(v)!));
}

/**
 * A proposal's grid laid out: rows by `ys` (one row without a second axis), columns by `xs`,
 * each cell with its strength, and the best cell marked (the one holding the proposal's best
 * run, else the strongest).
 */
export function layoutGrid(
	grid: ProposalGrid,
	order: BestOrder | null,
	bestRun: string | null = null
): GridLayout {
	const ys = grid.y && grid.ys?.length ? grid.ys : [null];
	const two = ys[0] !== null || ys.length > 1;
	const t = strengths(grid.cells, order);
	let best = bestRun === null ? -1 : grid.cells.findIndex((c) => c.run === bestRun && c.runs > 0);
	if (best < 0) {
		let top = -1;
		t.forEach((s, i) => {
			if (s !== null && s > top) [top, best] = [s, i];
		});
	}
	const at = new Map<string, GridCell>();
	grid.cells.forEach((c, i) => {
		const key = `${valueKey(c.x)}|${two ? valueKey(c.y) : ''}`;
		at.set(key, { ...c, strength: t[i], isBest: i === best });
	});
	const rows = ys.map((y) =>
		grid.xs.map((x) => {
			const cell = at.get(`${valueKey(x)}|${two ? valueKey(y) : ''}`);
			return cell && cell.runs > 0 ? cell : null;
		})
	);
	return {
		xs: grid.xs,
		ys,
		rows,
		covered: rows.flat().filter(Boolean).length,
		possible: grid.xs.length * ys.length
	};
}

/**
 * A cell's tint: its group's hue mixed into the page, faint for the worst cell and strong for
 * the best, so the eye finds the best by color; a cell nothing ranks gets a flat middle tint.
 */
export const cellTint = (hue: string, strength: number | null) =>
	`color-mix(in oklab, ${hue} ${Math.round(strength === null ? 18 : 10 + strength * 55)}%, transparent)`;

/** Whether a tint is strong enough that its text reads better on a darker scrim. */
export const strongTint = (strength: number | null) => strength !== null && strength > 0.75;

/**
 * The colors `n` new groups get, in order: the group palette's hues that no saved group has
 * yet (`used`), then round again. The same hue the proposal's card shows is the one saved.
 */
export function proposalColors(n: number, used: readonly string[]): string[] {
	const palette: readonly string[] = GROUP_HUES.light;
	const taken = new Set(used.map((c) => c.toLowerCase()));
	const free = palette.filter((c) => !taken.has(c.toLowerCase()));
	const order = [...free, ...palette.filter((c) => taken.has(c.toLowerCase()))];
	return Array.from({ length: n }, (_, i) => order[i % order.length]);
}

/**
 * What `POST .../groups` takes to create a proposal as a group, named `name`, in `color`: the
 * body the API made for it (`create`), else one built from the proposal's own fields.
 */
export function createBody(p: Proposal, color: string, name = p.name) {
	const body: Record<string, unknown> = p.create
		? { ...p.create }
		: {
				name: p.name,
				runs: p.runs,
				filter: p.filter,
				best_key: p.best_key,
				best_order: p.best_order
			};
	body.name = name.trim() || String(body.name);
	body.color = color;
	if (!body.filter || !Object.keys(body.filter).length) delete body.filter;
	if (!body.best_key) {
		delete body.best_key;
		delete body.best_order;
	} else body.best_order ??= 'asc';
	return body;
}

/**
 * Runs that stay ungrouped when only `chosen` of a set of proposals are created: those the
 * answer counted (`base`: in no proposal and no saved group), and those of the proposals left
 * out that no chosen one holds.
 */
export function ungroupedCount(
	base: number,
	all: readonly Pick<Proposal, 'runs'>[],
	chosen: readonly Pick<Proposal, 'runs'>[]
) {
	const kept = new Set(chosen.flatMap((p) => p.runs));
	const dropped = new Set(all.flatMap((p) => p.runs).filter((r) => !kept.has(r)));
	return base + dropped.size;
}

/**
 * How a proposal's repeats read: "3 seeds each" when every configuration has as many, else
 * "2–3 seeds each"; null without a repeat key.
 */
export function repeatText(p: Pick<Proposal, 'repeat_key' | 'grid'>): string | null {
	if (!p.repeat_key) return null;
	const key = displayKey(p.repeat_key);
	const label = key.endsWith('s') ? key : `${key}s`;
	const counts = (p.grid?.cells ?? []).map((c) => c.runs).filter((n) => n > 0);
	if (!counts.length) return `averaged over ${label}`;
	const lo = Math.min(...counts);
	const hi = Math.max(...counts);
	if (hi <= 1) return null;
	return lo === hi ? `${lo} ${label} each` : `${lo}–${hi} ${label} each`;
}

/** What the empty conversation offers to ask, from the project's own runs where they say enough. */
export function autoPrompts(rows: readonly RunRow[]): string[] {
	const varies = keysOf(classifyColumns(rows.map(configOf)), 'varies');
	const named = varies.find((k) => /optim|sched|model|arch|data|token|loss/i.test(k)) ?? varies[0];
	const failed = rows.some((r) => r.state === 'crashed' || r.state === 'failed');
	return [
		named ? `Split by ${displayKey(named)}` : 'Split by optimizer',
		failed ? 'Leave out crashed runs' : 'Only finished runs',
		'Only runs from this week'
	];
}

/** What the thinking line says after `seconds`, so a long wait reads as progress. */
export function thinkingText(seconds: number, runs: number, keys: number, revising: boolean) {
	if (revising) {
		if (seconds < 6) return 'Reading your request…';
		if (seconds < 20) return 'Regrouping the runs…';
		return 'Checking each group’s best run…';
	}
	if (seconds < 5) return `Reading ${runs} runs…`;
	if (seconds < 14) return keys ? `Comparing ${keys} config keys that vary…` : 'Comparing configs…';
	if (seconds < 28) return 'Drafting groups that each compare one change…';
	return 'Checking each group’s best run…';
}

/** How an axis's values show: as chips, or (many numbers) as a range with a strip of dots. */
export type AxisSummary =
	| { kind: 'chips'; values: unknown[] }
	| { kind: 'range'; lo: number; hi: number; n: number; log: boolean; at: number[] };

/** More distinct numbers than this read as a range rather than chips. */
export const MAX_CHIPS = 8;

/**
 * An axis's values as a card shows them: a few, as chips; many numbers (a random search) as
 * their range and where each falls in it, on a log scale when they span 2 decades or more.
 */
export function axisSummary(values: readonly unknown[]): AxisSummary {
	const numbers = values.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
	if (values.length <= MAX_CHIPS || numbers.length < values.length)
		return { kind: 'chips', values: [...values] };
	const lo = Math.min(...numbers);
	const hi = Math.max(...numbers);
	const log = lo > 0 && hi / lo >= 100;
	const pos = (v: number) =>
		hi === lo ? 0.5 : log ? Math.log(v / lo) / Math.log(hi / lo) : (v - lo) / (hi - lo);
	return { kind: 'range', lo, hi, n: numbers.length, log, at: numbers.map(pos) };
}

/** The axes a card lists first: the grid's, then the rest in the proposal's order. */
export function orderedAxes(p: Pick<Proposal, 'axes' | 'grid'>): string[] {
	const first = [p.grid?.x, p.grid?.y].filter((k): k is string => !!k && p.axes.includes(k));
	return [...first, ...p.axes.filter((k) => !first.includes(k))];
}
