/**
 * How a typed search matches a name, shared by the command menu (scored here) and Flock's run
 * search (built in filters.ts): each word on its own and in any order, `-`, `_`, `.` and
 * spaces ignored inside a word ("bankeval" finds "bank-eval"), a quoted "exact phrase" kept
 * whole, and, in the menu only, a typo or two forgiven ("bnak" finds "bank").
 */
import { computeCommandScore } from 'bits-ui';

/** What's typed: its quoted phrases and its other words, lower case. */
export interface SearchTerms {
	phrases: string[];
	words: string[];
}

const SEPARATORS = /[-_.\s/]+/g;

export function parseSearch(search: string): SearchTerms {
	const phrases: string[] = [];
	// An unclosed quote takes the rest as its phrase, so typing one in doesn't flicker.
	const rest = search.replace(/"([^"]*)("|$)/g, (_, phrase: string) => {
		if (phrase.trim()) phrases.push(phrase.trim().toLowerCase());
		return ' ';
	});
	const words = rest.toLowerCase().split(/\s+/).filter(Boolean);
	return { phrases, words };
}

/** Text with each run of separators one space: "Bank-eval_v2" is "bank eval v2". */
export const spaces = (text: string) => text.toLowerCase().replace(SEPARATORS, ' ').trim();

/** Text without its separators: "Bank-eval_v2" is "bankevalv2". */
export const compact = (text: string) => text.toLowerCase().replace(SEPARATORS, '');

/** At most `max` edits apart (insert, delete, change, swap two neighbours). */
export function near(a: string, b: string, max: number): boolean {
	if (Math.abs(a.length - b.length) > max) return false;
	const rows: number[][] = [];
	for (let i = 0; i <= a.length; i++) {
		rows.push([i]);
		for (let j = 1; j <= b.length; j++) {
			if (i === 0) {
				rows[0].push(j);
				continue;
			}
			const cost = a[i - 1] === b[j - 1] ? 0 : 1;
			let best = Math.min(rows[i - 1][j] + 1, rows[i][j - 1] + 1, rows[i - 1][j - 1] + cost);
			if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1])
				best = Math.min(best, rows[i - 2][j - 2] + 1);
			rows[i].push(best);
		}
		if (Math.min(...rows[i]) > max) return false;
	}
	return rows[a.length][b.length] <= max;
}

/** How many typos a word of this length may have: none under 4 letters. */
const typos = (word: string) => (word.length >= 8 ? 2 : word.length >= 4 ? 1 : 0);

/** One typed word against a row: 0 when it doesn't match. */
function wordScore(word: string, label: string, keywords: string[], text: string): number {
	const fuzzy = computeCommandScore(label, word, keywords);
	if (fuzzy > 0) return fuzzy;
	const bare = compact(word);
	if (bare && compact(text).includes(bare)) return 0.85;
	const max = typos(bare);
	if (!max) return 0;
	// A typo'd word matches a word of the row, or the start of one ("bnak" → "bank-eval").
	for (const part of text.toLowerCase().split(SEPARATORS)) {
		if (near(bare, part, max) || near(bare, part.slice(0, bare.length), max)) return 0.25;
	}
	return 0;
}

/**
 * A row's score for what's typed, 0 to 1 (0: hidden). Typed in the row's own order scores as
 * bits-ui's command score does; any other order a little lower; typos lowest.
 */
export function searchScore(search: string, label: string, keywords: string[] = []): number {
	const { phrases, words } = parseSearch(search);
	if (!phrases.length && !words.length) return 1;
	const text = [label, ...keywords].join(' ');
	if (!phrases.length) {
		const whole = computeCommandScore(label, search.trim(), keywords);
		if (whole > 0) return whole;
	}
	// A phrase keeps its words together and in order; separators count as spaces.
	const spaced = ` ${spaces(text)} `;
	if (!phrases.every((p) => spaced.includes(spaces(p)))) return 0;
	const each = words.map((w) => wordScore(w, label, keywords, text));
	if (each.some((s) => s === 0)) return 0;
	if (!each.length) return 0.9;
	return (
		0.9 *
		Math.pow(
			each.reduce((a, s) => a * s, 1),
			1 / each.length
		)
	);
}
