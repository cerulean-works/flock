/**
 * The log viewer's pure half: what level each line shows as, the tokens worth a colour, search
 * matches, and how ANSI styling, tokens and matches combine into the pieces a line renders as.
 * Lines come parsed by `#lib/logs.ts` (timestamps, ANSI codes, levels, tracebacks).
 */
import { blocksOf, type Level, type LogLine, type Span } from '#lib/logs.ts';

// ------------------------------------------------------------------------------------ levels

/**
 * Each line's level for display and filtering: a logger's own level, and every line of a
 * Python traceback (`Traceback (most recent call last):` down to the exception) as an error.
 */
export function levelsOf(lines: LogLine[]): (Level | null)[] {
	const out = lines.map((l) => l.level);
	let i = 0;
	for (const b of blocksOf(lines)) {
		if (b.kind === 'line') i++;
		else if (b.kind === 'table') i += b.rows.length;
		else {
			const n = b.lines.length + 1;
			for (let k = i; k < i + n; k++) out[k] = 'error';
			i += n;
		}
	}
	// A traceback cut short by the page's end still starts one.
	for (let k = 0; k < lines.length; k++)
		if (out[k] == null && lines[k].text.startsWith('Traceback (most recent call last)'))
			out[k] = 'error';
	return out;
}

/** What the level filter offers: the four levels, and lines with none. */
export type LevelKey = Level | 'none';
export const LEVEL_KEYS: LevelKey[] = ['error', 'warning', 'info', 'debug', 'none'];

export function levelCounts(levels: (Level | null)[]): Record<LevelKey, number> {
	const counts: Record<LevelKey, number> = { error: 0, warning: 0, info: 0, debug: 0, none: 0 };
	for (const l of levels) counts[l ?? 'none']++;
	return counts;
}

// ------------------------------------------------------------------------------------ tokens

export type TokenKind = 'number' | 'key' | 'string' | 'time' | 'url' | Level;

export interface Token {
	start: number;
	end: number;
	kind: TokenKind;
}

const LEVEL_WORDS: Record<string, Level> = {
	CRITICAL: 'error',
	FATAL: 'error',
	ERROR: 'error',
	WARNING: 'warning',
	WARN: 'warning',
	INFO: 'info',
	DEBUG: 'debug'
};

/** Words before a number that aren't the number's name: `on 8 GPUs`, `in 3 s`. */
const NOT_KEYS = new Set(
	'a an and are as at by for from in into is it of on or than that the then to was were with'.split(
		' '
	)
);

const NAME = String.raw`[A-Za-z_][\w./-]*`;
const NUM = String.raw`[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?`;

// One pass, first alternative wins at each position.
const TOKENS = new RegExp(
	[
		String.raw`(?<url>\bhttps?:\/\/[^\s'"<>)\]]+)`,
		String.raw`(?<level>\b(?:CRITICAL|FATAL|ERROR|WARNING|WARN|INFO|DEBUG)\b)`,
		String.raw`(?<time>\b\d{4}-\d\d-\d\d(?:[T ]\d\d:\d\d(?::\d\d(?:[.,]\d+)?)?)?(?:Z|[+-]\d\d:?\d\d)?(?![\w-])|\b\d{1,2}:\d\d:\d\d(?:[.,]\d+)?\b)`,
		String.raw`(?<string>(?<=^|[\s([{:=,])(?:'[^'\n]{0,300}'|"[^"\n]{0,300}")(?=$|[\s)\]}:,.;]))`,
		// `lr=3e-4`, `loss: 2.41`, `step 220`: a name, then its value.
		String.raw`(?<key>(?<![\w./-])${NAME}(?==|:\s*(?:${NUM}|nan|inf)(?![\w])|\s+${NUM}(?![\w]|\.\d)))`,
		String.raw`(?<number>(?<![\w.\-+])(?:${NUM}%?|nan|inf)(?![\w]|\.\d))`
	].join('|'),
	'gi'
);

const QUOTED_NUMBER = new RegExp(String.raw`^(['"])${NUM}\1$`, 'i');

/** The parts of a line worth a colour: numbers, metric names, strings, times, URLs, levels. */
export function tokenize(text: string): Token[] {
	const out: Token[] = [];
	for (const m of text.matchAll(TOKENS)) {
		const g = m.groups!;
		const start = m.index;
		const end = start + m[0].length;
		let kind: TokenKind;
		if (g.url) kind = 'url';
		else if (g.level) {
			// Level words count in capitals only: "error" in prose is just a word.
			const level = LEVEL_WORDS[m[0]];
			if (!level) continue;
			kind = level;
		} else if (g.time) kind = 'time';
		else if (g.string) {
			// A quoted dict key (`'loss':`), or a number printed as a string (`'2.573'`).
			if (text[end] === ':') kind = 'key';
			else if (QUOTED_NUMBER.test(m[0])) kind = 'number';
			else kind = 'string';
		} else if (g.key) {
			if (/\s/.test(text[end] ?? '') && NOT_KEYS.has(m[0].toLowerCase())) continue;
			kind = 'key';
		} else kind = 'number';
		out.push({ start, end, kind });
	}
	return out;
}

// ------------------------------------------------------------------------------------ search

/** Where `q` (lowercase) starts in `text`, case-insensitively and without overlaps. */
export function matchesIn(text: string, q: string): number[] {
	if (!q) return [];
	const out: number[] = [];
	const lower = text.toLowerCase();
	for (let i = lower.indexOf(q); i !== -1; i = lower.indexOf(q, i + q.length)) out.push(i);
	return out;
}

export interface Match {
	/** Index into the lines searched. */
	row: number;
	start: number;
}

/** Every match of `q` in `lines`, in order, up to `cap`. */
export function search(lines: LogLine[], q: string, cap = 10_000): Match[] {
	const out: Match[] = [];
	if (!q) return out;
	for (let row = 0; row < lines.length && out.length < cap; row++)
		for (const start of matchesIn(lines[row].text, q)) {
			out.push({ row, start });
			if (out.length >= cap) break;
		}
	return out;
}

// ------------------------------------------------------------------------------------ pieces

export interface Mark {
	start: number;
	end: number;
	current: boolean;
}

/** A run of a line's text with one look: ANSI style, token, search match. */
export interface Piece extends Omit<Span, 'text'> {
	text: string;
	token?: TokenKind;
	/** Part of the dimmed logger prefix. */
	dim?: boolean;
	mark?: 'hit' | 'current';
}

/**
 * A line's text cut where its ANSI style, token or search match changes. ANSI colours win over
 * token colours: the program chose them.
 */
export function pieces(spans: Span[], tokens: Token[], marks: Mark[], dim = 0): Piece[] {
	const cuts = new Set<number>([0]);
	let total = 0;
	const spanStarts: number[] = [];
	for (const s of spans) {
		spanStarts.push(total);
		cuts.add(total);
		total += s.text.length;
	}
	cuts.add(total);
	if (dim > 0 && dim < total) cuts.add(dim);
	for (const t of tokens) cuts.add(Math.min(t.start, total)).add(Math.min(t.end, total));
	for (const m of marks) cuts.add(Math.min(m.start, total)).add(Math.min(m.end, total));
	const points = [...cuts].sort((a, b) => a - b);

	const out: Piece[] = [];
	let si = 0;
	let ti = 0;
	let mi = 0;
	for (let p = 0; p < points.length - 1; p++) {
		const a = points[p];
		const b = points[p + 1];
		if (a === b) continue;
		while (si < spans.length - 1 && spanStarts[si + 1] <= a) si++;
		while (ti < tokens.length && tokens[ti].end <= a) ti++;
		while (mi < marks.length && marks[mi].end <= a) mi++;
		const span = spans[si];
		const offset = a - spanStarts[si];
		const piece: Piece = { text: span.text.slice(offset, offset + (b - a)) };
		if (span.color) piece.color = span.color;
		if (span.bold) piece.bold = true;
		if (span.italic) piece.italic = true;
		if (span.underline) piece.underline = true;
		const token = tokens[ti];
		if (token && token.start <= a) piece.token = token.kind;
		if (a < dim) piece.dim = true;
		const mark = marks[mi];
		if (mark && mark.start <= a) piece.mark = mark.current ? 'current' : 'hit';
		out.push(piece);
	}
	return out;
}

// ------------------------------------------------------------------------------------- links

/** `#L42` → line index 41; null for anything else. */
export function lineFromHash(hash: string): number | null {
	const m = /^#L(\d+)$/.exec(hash);
	if (!m) return null;
	const n = Number(m[1]);
	return n >= 1 ? n - 1 : null;
}
