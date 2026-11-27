/**
 * Why Flock refuses to compare two eval runs doc by doc, in one plain sentence. Flock's reason
 * (eval's diff, or a missing results file) names a run by its source, `run:entity/project/id`;
 * `names` turns those paths into the runs' names. An unknown reason gets a general sentence.
 */
export function refusalSentence(reason: string, names: Record<string, string>): string {
	const named = (source: string | undefined) =>
		(source && names[source.replace(/^run:/, '')]) || 'One of the runs';
	// `run:a/b/c has no results.json`
	const missing = /^(\S+) has no results\.json$/.exec(reason);
	if (missing) return `${named(missing[1])} has no eval results.`;
	if (reason.startsWith('different instrument:'))
		return 'These runs were evaluated with different settings, so their items can’t be paired.';
	if (/ has the same hash in .* but other doc ids/.test(reason))
		return 'These runs list a task’s docs differently, so their items can’t be paired.';
	// A run's predictions disagree with its own results: too few rows, a malformed or repeated
	// row, or a score its rows don't add up to.
	const damaged = /^(run:\S+?): /.exec(reason);
	if (damaged) return `${named(damaged[1])} has eval results that don’t add up.`;
	return 'These runs can’t be compared item by item.';
}
