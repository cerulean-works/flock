import type { HomeRun } from '#lib/home/runs.ts';
import type { Budget } from '#lib/types.ts';
import { money, relativeTime } from '#lib/utils.ts';
import { orgHref, runHref } from '#lib/workspace/url.ts';

/** The one line on the command field's top edge (H3): a fact, an action, and how to forget it. */
export interface Status {
	/** Stable for the fact, so a dismissal holds until something new happens. */
	id: string;
	tone: 'failed' | 'warn' | 'done';
	text: string;
	action: { label: string; href: string };
}

/**
 * The most pressing thing since the viewer last looked, by fixed rank: one of their runs failed
 * or crashed, the month's budget is 80% gone, one of their runs finished. Null when there's
 * nothing worth the space.
 */
export function homeStatus(
	mine: HomeRun[],
	budget: Budget | null,
	since: number,
	now: number,
	dismissed: ReadonlySet<string>
): Status | null {
	const out: Status[] = [];
	for (const r of mine)
		if ((r.kind === 'run_failed' || r.kind === 'run_crashed') && r.at > since) {
			out.push({
				id: `failed:${r.project}/${r.id}`,
				tone: 'failed',
				text: `${r.name} ${r.state === 'crashed' ? 'crashed' : 'failed'} ${relativeTime(r.at, now)}`,
				action: { label: 'Logs', href: `${runHref(r.org, r.project, r.id)}?tab=logs` }
			});
			break;
		}
	if (budget?.budget && budget.spent / budget.budget >= 0.8) {
		const pct = Math.round((budget.spent / budget.budget) * 100);
		out.push({
			id: `budget:${budget.month}:${pct >= 100 ? 'over' : 'near'}`,
			tone: 'warn',
			text:
				pct >= 100
					? `Over this month’s budget by ${money(budget.spent - budget.budget, budget.currency)}`
					: `${pct}% of this month’s budget used`,
			action: { label: 'Budget', href: orgHref(budget.org, 'budget') }
		});
	}
	for (const r of mine)
		if (r.kind === 'run_finished' && r.at > since) {
			out.push({
				id: `finished:${r.project}/${r.id}`,
				tone: 'done',
				text: `${r.name} finished ${relativeTime(r.at, now)}`,
				action: { label: 'Open', href: runHref(r.org, r.project, r.id) }
			});
			break;
		}
	return out.find((s) => !dismissed.has(s.id)) ?? null;
}
