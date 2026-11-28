import type { ColumnKind, SortOrder } from '#lib/types.ts';

/** Words in a metric's name that mean lower is better: a loss, an error, a time taken. */
const LOWER =
	/(^|[^a-z])(loss|nll|perplexity|ppl|bpb|bpc|bits|err|error|errors|wer|cer|mse|mae|rmse|latency|time|duration|runtime|elapsed|seconds|secs|ms|cost|regret|distance|dist|kl|entropy|grad_norm)([^a-z]|$)/;
/** Words that mean higher is better: a score, an accuracy, a throughput. */
const HIGHER =
	/(^|[^a-z])(acc|accuracy|score|f1|auc|bleu|rouge|reward|return|precision|recall|win|winrate|pass|em|exact|match|map|ndcg|mrr|throughput|tps|mfu|tokens_per_sec|samples_per_sec|it_s|correct|right|elo)([^a-z]|$)/;

/**
 * The way a column sorts first (Harry): what a person looking at it wants on top. A loss, an
 * error or a time lowest first; an accuracy or a score highest first; dates newest first;
 * names A to Z. A metric named after neither ends lowest first, as losses are the common case.
 */
export function naturalOrder(key: string, kind?: ColumnKind): SortOrder {
	if (kind === 'time') return 'desc';
	if (kind === 'num' || kind === 'duration' || kind === undefined) {
		const name = key.toLowerCase().replace(/^(summary|config)\./, '');
		// The name's last part decides ("eval/acc_loss" is a loss); a part matching both, the
		// later word wins.
		const last = name.split(/[/.]/).pop() ?? name;
		for (const part of [last, name]) {
			const lower = part.search(LOWER);
			const higher = part.search(HIGHER);
			if (lower >= 0 || higher >= 0) return higher > lower ? 'desc' : 'asc';
		}
		return 'asc';
	}
	return 'asc';
}

/** How each way reads for a column: "Lowest first", "Newest first", "A to Z". */
export function orderWords(kind?: ColumnKind): Record<SortOrder, string> {
	if (kind === 'time') return { asc: 'Oldest first', desc: 'Newest first' };
	if (kind === 'num' || kind === 'duration') return { asc: 'Lowest first', desc: 'Highest first' };
	return { asc: 'A to Z', desc: 'Z to A' };
}
