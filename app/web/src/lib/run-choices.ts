/**
 * The compare pickers' runs (Cmd+K's Compare and the compare page's A and B): the project's
 * own, newest first, and once the search is specific enough the org's other projects' too, in
 * one list ranked by how well each matches.
 */
import { searchScore } from '#lib/search.ts';
import type { RunChoice } from '#lib/types.ts';

/** How many a picker lists. */
export const CHOICES = 30;

/**
 * Whether a search reaches past the project: three letters or more, so one or two don't fill
 * the list with every project's runs.
 */
export const reachesOut = (q: string) => q.replace(/\s+/g, '').length >= 3;

/**
 * The runs ranked: best match first (name, then id, project and model), the home project's
 * first among equals, then the newest. Without a search, the home project's newest first. Every
 * run given matched Flock's own search, so one the typo-forgiving score misses still shows, last.
 */
export function rankChoices(
	q: string,
	home: { entity: string; project: string },
	runs: readonly RunChoice[],
	limit = CHOICES
): RunChoice[] {
	const own = (r: RunChoice) => r.entity === home.entity && r.project === home.project;
	const search = q.trim();
	const scored = runs.map((run) => ({
		run,
		score: search
			? Math.max(
					1e-3,
					searchScore(search, run.display_name ?? run.id, [
						run.id,
						run.project,
						...(run.model ? [run.model] : [])
					])
				)
			: 1
	}));
	scored.sort(
		(x, y) =>
			y.score - x.score ||
			Number(own(y.run)) - Number(own(x.run)) ||
			y.run.created_at - x.run.created_at
	);
	return scored.slice(0, limit).map((s) => s.run);
}
