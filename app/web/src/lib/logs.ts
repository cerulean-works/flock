/**
 * Console output made readable. wandb captures a run's stdout and stderr as lines, each
 * prefixed with an ISO timestamp, and stderr's with `ERROR ` before it (whatever was printed:
 * tqdm and Python's warnings write there too). Training scripts print a few recognizable
 * shapes into them:
 *
 * - metric dicts, Python's repr or JSON (`{'loss': '2.573', 'grad_norm': 0.084}`, as Hugging
 *   Face's Trainer prints them): a run of them with the same keys becomes one table;
 * - tqdm progress bars (`45%|████▌     | 450/1000 [00:12<00:15, 36.1it/s]`), redrawn with
 *   carriage returns: the last redraw is kept;
 * - Python tracebacks, from `Traceback (most recent call last):` to the exception: one block;
 * - log levels (`INFO`, `WARNING`, `ERROR`, ...) and ANSI colors.
 *
 * Everything else is plain text.
 */

export interface RawLine {
	line: number;
	text: string;
}

export type Level = 'debug' | 'info' | 'warning' | 'error';

/** A value in a printed dict: numbers (quoted or not) as numbers, the rest as printed. */
export type DictValue = number | string | boolean | null | DictValue[] | { [k: string]: DictValue };

export interface Progress {
	label: string;
	/** 0–100. */
	percent: number;
	/** What follows the bar: `450/1000 [00:12<00:15, 36.1it/s]`. */
	rest: string;
}

/** A run of text with one style, from ANSI codes. */
export interface Span {
	text: string;
	color?: 'red' | 'green' | 'yellow' | 'blue' | 'magenta' | 'cyan' | 'faint';
	bold?: boolean;
	italic?: boolean;
	underline?: boolean;
}

export interface LogLine {
	line: number;
	/** Seconds since the epoch, from wandb's prefix; null when the line has none. */
	at: number | null;
	/** Printed to stderr rather than stdout. */
	stderr: boolean;
	/** The line without its timestamp, carriage-return redraws or ANSI codes. */
	text: string;
	spans: Span[];
	level: Level | null;
	/** Characters before the message to mute: a logger's own time, level and name. */
	dim: number;
	dict: [string, DictValue][] | null;
	progress: Progress | null;
}

export type LogBlock =
	| { kind: 'line'; line: LogLine }
	| { kind: 'table'; keys: string[]; rows: LogLine[] }
	| { kind: 'traceback'; lines: LogLine[]; error: LogLine };

// ---------------------------------------------------------------------------------- one line

const STAMP = /^(ERROR )?(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)(Z|[+-]\d\d:?\d\d)?\s/;

// SGR codes set styles; every other escape sequence (cursor moves, line clears) is dropped.
// eslint-disable-next-line no-control-regex -- matching terminal escapes is the point
const ESCAPE = /\x1b\[([\d;]*)([A-Za-z])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]/g;

const COLORS: Record<number, Span['color']> = {
	30: 'faint',
	31: 'red',
	32: 'green',
	33: 'yellow',
	34: 'blue',
	35: 'magenta',
	36: 'cyan',
	90: 'faint',
	91: 'red',
	92: 'green',
	93: 'yellow',
	94: 'blue',
	95: 'magenta',
	96: 'cyan'
};

/** Text and its ANSI styling. */
export function ansi(text: string): { text: string; spans: Span[] } {
	const spans: Span[] = [];
	let style: Omit<Span, 'text'> = {};
	let plain = '';
	let last = 0;
	const push = (s: string) => {
		if (!s) return;
		plain += s;
		const prev = spans[spans.length - 1];
		if (prev && sameStyle(prev, style)) prev.text += s;
		else spans.push({ text: s, ...style });
	};
	for (const m of text.matchAll(ESCAPE)) {
		push(text.slice(last, m.index));
		last = m.index + m[0].length;
		if (m[2] !== 'm') continue;
		const codes = (m[1] || '0').split(';').map(Number);
		for (let i = 0; i < codes.length; i++) {
			const c = codes[i];
			if (c === 0) style = {};
			else if (c === 1) style = { ...style, bold: true };
			else if (c === 2) style = { ...style, color: 'faint' };
			else if (c === 3) style = { ...style, italic: true };
			else if (c === 4) style = { ...style, underline: true };
			else if (c === 22) style = { ...style, bold: false };
			else if (c === 23) style = { ...style, italic: false };
			else if (c === 24) style = { ...style, underline: false };
			else if (c === 39) style = { ...style, color: undefined };
			else if (COLORS[c]) style = { ...style, color: COLORS[c] };
			// 256-color and RGB foregrounds and any background: their arguments are skipped.
			else if (c === 38 || c === 48) i += codes[i + 1] === 5 ? 2 : 4;
		}
	}
	push(text.slice(last));
	return { text: plain, spans };
}

const sameStyle = (a: Omit<Span, 'text'>, b: Omit<Span, 'text'>) =>
	a.color === b.color &&
	!a.bold === !b.bold &&
	!a.italic === !b.italic &&
	!a.underline === !b.underline;

// `INFO`, `[WARNING]`, `W1018`, `| ERROR |`, `UserWarning:`: within a line's first few words.
const LEVELS: [RegExp, Level][] = [
	[/\b(ERROR|CRITICAL|FATAL)\b|^E\d{4} |(Error|Exception):/, 'error'],
	[/\b(WARNING|WARN)\b|^W\d{4} |Warning:/, 'warning'],
	[/\bINFO\b|^I\d{4} /, 'info'],
	[/\bDEBUG\b|^D\d{4} /, 'debug']
];

// Python's warnings: `path/to/file.py:10: UserWarning: message`, however long the path.
const PY_WARNING = /^(\S+:\d+: )\w*Warning: /;
// A logger's prefix ends after its level word and the name and separators that follow it:
// `2026-10-18 12:46:17,934 | INFO | trainer | `, `[2026-10-18 12:46:17] WARNING: `.
const PREFIX =
	/^.{0,40}?\b(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)\b\]?(\s*[|:-]\s*(?:[\w.]+\s*[|:]\s*)?|\s+)/;

export function levelOf(text: string): Level | null {
	if (PY_WARNING.test(text)) return 'warning';
	const head = text.slice(0, 48);
	for (const [re, level] of LEVELS) if (re.test(head)) return level;
	return null;
}

/** How much of a line is its logger's prefix rather than the message. */
export function prefixOf(text: string): number {
	const warning = PY_WARNING.exec(text);
	if (warning) return warning[1].length;
	return PREFIX.exec(text)?.[0].length ?? 0;
}

// label, percent, bar, rest: `Epoch 1:  45%|████▌     | 450/1000 [00:12<00:15, 36.1it/s]`
const TQDM = /^(.*?)(\d{1,3})%\|([^|]*)\|\s?(.*)$/;

export function progressOf(text: string): Progress | null {
	const m = TQDM.exec(text);
	if (!m || Number(m[2]) > 100) return null;
	return { label: m[1].trim().replace(/:$/, ''), percent: Number(m[2]), rest: m[4].trim() };
}

export function parseLine(raw: RawLine): LogLine {
	let text = raw.text;
	let at: number | null = null;
	const stamp = STAMP.exec(text);
	if (stamp) {
		const t = Date.parse(`${stamp[2]}${stamp[3] ?? 'Z'}`); // wandb's are UTC
		at = Number.isNaN(t) ? null : t / 1000;
		text = text.slice(stamp[0].length);
	}
	// A bar redrawn in place: what the terminal would show is the last redraw.
	if (text.includes('\r')) {
		const parts = text.split('\r').filter((p) => p.trim());
		text = parts[parts.length - 1] ?? '';
	}
	const styled = ansi(text.replace(/\s+$/, ''));
	const plain = styled.text;
	const dict = parseDict(plain);
	const progress = dict ? null : progressOf(plain);
	const level = dict || progress ? null : levelOf(plain);
	return {
		line: raw.line,
		at,
		stderr: !!stamp?.[1],
		text: plain,
		spans: styled.spans,
		level,
		dim: level ? prefixOf(plain) : 0,
		dict,
		progress
	};
}

// ------------------------------------------------------------------------------ printed dicts

/**
 * A line that is a dict, as Python's repr or JSON prints it, as its entries; null otherwise.
 * Quoted numbers (`'2.573'`) are read as numbers: that's how they were meant.
 */
export function parseDict(text: string): [string, DictValue][] | null {
	const s = text.trim();
	if (s.length < 2 || s[0] !== '{' || s[s.length - 1] !== '}') return null;
	try {
		const p = new Parser(s);
		const v = p.value();
		p.end();
		if (!v || typeof v !== 'object' || Array.isArray(v)) return null;
		const entries = Object.entries(v);
		return entries.length ? entries : null;
	} catch {
		return null;
	}
}

const NUMBER = /^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i;

class Parser {
	i = 0;
	constructor(readonly s: string) {}

	ws() {
		while (/\s/.test(this.s[this.i] ?? '')) this.i++;
	}

	end() {
		this.ws();
		if (this.i !== this.s.length) throw new Error('trailing text');
	}

	eat(c: string) {
		this.ws();
		if (this.s[this.i] !== c) throw new Error(`expected ${c}`);
		this.i++;
	}

	value(): DictValue {
		this.ws();
		const c = this.s[this.i];
		if (c === '{') return this.object();
		if (c === '[' || c === '(') return this.list(c === '[' ? ']' : ')');
		if (c === "'" || c === '"') {
			const str = this.string();
			return NUMBER.test(str) ? Number(str) : str;
		}
		const m =
			/^(-?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?|-?inf|nan|True|False|None|true|false|null)/i.exec(
				this.s.slice(this.i)
			);
		if (!m) throw new Error('unexpected value');
		this.i += m[0].length;
		const w = m[0];
		if (w === 'True' || w === 'true') return true;
		if (w === 'False' || w === 'false') return false;
		if (w === 'None' || w === 'null') return null;
		if (/^-?inf$/i.test(w)) return w.startsWith('-') ? -Infinity : Infinity;
		if (/^nan$/i.test(w)) return NaN;
		return Number(w);
	}

	string(): string {
		const q = this.s[this.i++];
		let out = '';
		while (this.i < this.s.length) {
			const c = this.s[this.i++];
			if (c === q) return out;
			if (c === '\\') {
				const e = this.s[this.i++];
				out += e === 'n' ? '\n' : e === 't' ? '\t' : e;
			} else out += c;
		}
		throw new Error('unterminated string');
	}

	object(): { [k: string]: DictValue } {
		this.eat('{');
		const out: { [k: string]: DictValue } = {};
		this.ws();
		if (this.s[this.i] === '}') {
			this.i++;
			return out;
		}
		for (;;) {
			const key = this.value();
			if (typeof key === 'object' && key !== null) throw new Error('bad key');
			this.eat(':');
			out[String(key)] = this.value();
			this.ws();
			if (this.s[this.i] === ',') {
				this.i++;
				this.ws();
				if (this.s[this.i] === '}') {
					this.i++;
					return out;
				}
				continue;
			}
			this.eat('}');
			return out;
		}
	}

	list(close: string): DictValue[] {
		this.i++;
		const out: DictValue[] = [];
		this.ws();
		while (this.s[this.i] !== close) {
			out.push(this.value());
			this.ws();
			if (this.s[this.i] === ',') this.i++;
			else if (this.s[this.i] !== close) throw new Error(`expected ${close}`);
			this.ws();
		}
		this.i++;
		return out;
	}
}

// ------------------------------------------------------------------------------------ blocks

const TRACEBACK = /^Traceback \(most recent call last\):/;
const CHAINED =
	/^(During handling of the above exception|The above exception was the direct cause)/;
// The exception line that ends a traceback: `ValueError: …`, `torch.OutOfMemoryError: …`.
const EXCEPTION = /^[A-Za-z_][\w.]*(Error|Exception|Interrupt|Exit|Warning)\b|^[A-Za-z_][\w.]*:\s/;

const sameKeys = (a: [string, DictValue][], b: [string, DictValue][]) =>
	a.length === b.length && a.every(([k], i) => b[i][0] === k);

/** Lines as blocks: metric dicts with the same keys as one table, tracebacks as one block. */
export function blocksOf(lines: LogLine[]): LogBlock[] {
	const out: LogBlock[] = [];
	for (let i = 0; i < lines.length; i++) {
		const l = lines[i];
		if (TRACEBACK.test(l.text)) {
			const block: LogLine[] = [l];
			let error: LogLine | null = null;
			let j = i + 1;
			/** Past blank lines from `k`: where a chained traceback ("During handling…") goes on. */
			const chained = (k: number) => {
				while (k < lines.length && !lines[k].text.trim()) k++;
				const t = lines[k]?.text ?? '';
				return CHAINED.test(t) || TRACEBACK.test(t) ? k : -1;
			};
			while (j < lines.length) {
				const t = lines[j].text;
				if (/^\s/.test(t) || TRACEBACK.test(t) || CHAINED.test(t)) {
					block.push(lines[j++]);
					continue;
				}
				// A blank line, or an exception, followed by a chained traceback belongs with it.
				const k = !t.trim() || EXCEPTION.test(t) ? chained(j + 1) : -1;
				if (k !== -1) {
					block.push(...lines.slice(j, k));
					j = k;
					continue;
				}
				if (t.trim() && EXCEPTION.test(t)) error = lines[j++];
				break;
			}
			// Cut short (by the page's end, say): its last line stands in for the exception.
			error ??= block.length > 1 ? block.pop()! : null;
			if (error) {
				out.push({ kind: 'traceback', lines: block, error });
				i = j - 1;
				continue;
			}
		}
		if (l.dict) {
			let j = i + 1;
			while (j < lines.length && lines[j].dict && sameKeys(lines[j].dict!, l.dict)) j++;
			if (j - i > 1) {
				out.push({ kind: 'table', keys: l.dict.map(([k]) => k), rows: lines.slice(i, j) });
				i = j - 1;
				continue;
			}
		}
		out.push({ kind: 'line', line: l });
	}
	return out;
}

/** `+1:02:03` since `start`. */
export function elapsed(at: number, start: number): string {
	const s = Math.max(0, Math.floor(at - start));
	const p = (n: number) => String(n).padStart(2, '0');
	return `+${Math.floor(s / 3600)}:${p(Math.floor(s / 60) % 60)}:${p(s % 60)}`;
}

/** `2026-10-08 05:08:09`, in UTC like the rest of the dashboard's times. */
export function clock(at: number): string {
	return new Date(at * 1000).toISOString().slice(0, 19).replace('T', ' ');
}
