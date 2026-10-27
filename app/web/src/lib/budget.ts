import type { BudgetWarning, DeletedProject } from './types.ts';
import { count, hours, monthName } from './utils.ts';

/** What a budget warning says, without the link that goes with it. */
export function warningText(w: BudgetWarning): string {
	if (w.kind === 'unpriced')
		return `No price for ${w.gpu_types
			.map((t) => `${t.gpu_type} (${hours(t.gpu_hours)} h)`)
			.join(', ')}: counted as free.`;
	return `${count(w.runs, 'run')} (${hours(w.hours)} h) with unknown GPUs.`;
}

/** Who dismissed a warning and when, if known: `Dismissed by Ana, Oct 27, 14:02`. */
export function dismissedBy(w: BudgetWarning, when: (ts: number) => string): string {
	const parts = ['Dismissed'];
	if (w.dismissed_by) parts.push(`by ${w.dismissed_by.name}`);
	const text = parts.join(' ');
	return w.dismissed_at != null ? `${text}, ${when(w.dismissed_at)}` : text;
}

/** A deleted project's name, or what stands for the ones deleted before names were kept. */
export const deletedProjectName = (p: DeletedProject) =>
	p.name ?? 'Projects deleted earlier (names not kept)';

/** The months a deleted project spent in: `October 2026`, or `August – October 2026`. */
export function monthSpan(months: { month: string }[]): string {
	if (!months.length) return '';
	const first = monthName(months[0].month);
	const last = monthName(months[months.length - 1].month);
	return first === last ? first : `${first} – ${last}`;
}

/** What the listed deleted projects cost, counted in the budget and left out of it. */
export function deletedTotals(projects: DeletedProject[]) {
	let counted = 0;
	let excluded = 0;
	for (const p of projects) {
		if (p.excluded) excluded += p.cost;
		else counted += p.cost;
	}
	return { counted, excluded };
}
