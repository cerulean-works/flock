<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ChevronUpIcon from '@lucide/svelte/icons/chevron-up';
	import ChevronsDownIcon from '@lucide/svelte/icons/chevrons-down';
	import CopyIcon from '@lucide/svelte/icons/copy';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import SlidersHorizontalIcon from '@lucide/svelte/icons/sliders-horizontal';
	import { onMount, tick, untrack } from 'svelte';
	import type { Attachment } from 'svelte/attachments';
	import { toast } from 'svelte-sonner';
	import { replaceState } from '$app/navigation';
	import { page } from '$app/state';
	import type { ResolvedPathname } from '$app/types';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import BarSearch from '#lib/components/BarSearch.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { clock, elapsed, parseLine, type Level, type LogLine, type Span } from '#lib/logs.ts';
	import type { RunLogs } from '#lib/types.ts';
	import { dateTime, fileSafe, saveBlob } from '#lib/utils.ts';
	import Skeleton from '#lib/components/Skeleton.svelte';
	import {
		LEVEL_KEYS,
		levelCounts,
		levelsOf,
		lineFromHash,
		matchesIn,
		pieces,
		search,
		tokenize,
		type LevelKey,
		type Mark,
		type Piece,
		type TokenKind
	} from './log-view.ts';

	interface Props {
		/** `/api/projects/…/runs/{run}/logs` */
		url: string;
		/** Lines logged when the page loaded. */
		total: number;
		/** The run is still going: new lines are fetched as they come. */
		live: boolean;
		/** When the run started, for times since then. */
		start: number;
		/** For the downloaded file's name. */
		name: string;
		/** The page's view bar: the log's controls go there, rather than in a row of their own. */
		bar?: HTMLElement | null;
	}

	let { url, total, live, start, name, bar = null }: Props = $props();

	/** Moves the controls into the view bar, and takes them out with the log. */
	const into = (target: HTMLElement | null) => (node: HTMLElement) => {
		if (!target) return;
		target.append(node);
		return () => node.remove();
	};

	/** Lines fetched at first, and by each "Show earlier" / "Show later". */
	const CHUNK = 1000;
	const POLL_MS = 5000;
	/** A line's height unwrapped (`leading-5` plus `py-0.5`); wrapped lines are measured. */
	const ROW_H = 24;
	/** Rendered beyond the visible lines, in pixels, so fast scrolls don't flash blank. */
	const OVERSCAN = 800;

	let lines = $state.raw<LogLine[]>([]);
	/** Line number of the first line loaded; earlier ones are fetched on request. */
	let first = $state(0);
	let known = $state(0);
	let loading = $state(true);
	let failed = $state(false);
	let scroller = $state<HTMLDivElement>();
	/** The search: the bar's, an icon that opens into a field, open while it holds text. */
	let field = $state<BarSearch>();
	const openSearch = () => field?.focus();

	// ---------------------------------------------------------------------------- view options

	type Stamps = 'clock' | 'elapsed' | 'hidden';
	const STAMPS: { value: Stamps; label: string }[] = [
		{ value: 'clock', label: 'Time (UTC)' },
		{ value: 'elapsed', label: 'Since the run started' },
		{ value: 'hidden', label: 'Hidden' }
	];
	const LEVEL_LABEL: Record<LevelKey, string> = {
		error: 'Errors',
		warning: 'Warnings',
		info: 'Info',
		debug: 'Debug',
		none: 'No level'
	};
	const PREFS = 'flock:logs';
	let stamps = $state<Stamps>('clock');
	let wrap = $state(true);
	/** Levels filtered out. */
	let hidden = $state<LevelKey[]>([]);
	/** Kept at the end as lines come in, like `tail -f`. */
	let following = $state(true);

	onMount(() => {
		try {
			const saved = JSON.parse(localStorage.getItem(PREFS) ?? '{}');
			if (STAMPS.some((s) => s.value === saved.stamps)) stamps = saved.stamps;
			if (typeof saved.wrap === 'boolean') wrap = saved.wrap;
			if (Array.isArray(saved.hidden))
				hidden = saved.hidden.filter((k: LevelKey) => LEVEL_KEYS.includes(k));
		} catch {
			// Defaults.
		}
	});

	function remember() {
		try {
			localStorage.setItem(PREFS, JSON.stringify({ stamps, wrap, hidden }));
		} catch {
			// For this visit only.
		}
	}

	const stampOf = (l: LogLine) =>
		l.at == null || stamps === 'hidden'
			? ''
			: stamps === 'elapsed'
				? elapsed(l.at, start)
				: clock(l.at);

	// ------------------------------------------------------------------------ levels, filtering

	const levels = $derived(levelsOf(lines));
	const counts = $derived(levelCounts(levels));
	/** The lines shown, after the level filter, and their levels. */
	const shown = $derived.by(() => {
		if (!hidden.length) return { lines, levels };
		const ls: LogLine[] = [];
		const lv: (Level | null)[] = [];
		for (let i = 0; i < lines.length; i++)
			if (!hidden.includes(levels[i] ?? 'none')) {
				ls.push(lines[i]);
				lv.push(levels[i]);
			}
		return { lines: ls, levels: lv };
	});

	function toggleLevel(k: LevelKey, on: boolean) {
		hidden = on ? hidden.filter((h) => h !== k) : [...hidden, k];
		remember();
	}

	// ---------------------------------------------------------------------------------- search

	let query = $state('');
	const q = $derived(query.trim().toLowerCase());
	const matches = $derived(search(shown.lines, q));
	let current = $state(0);
	const cur = $derived(matches.length ? Math.min(current, matches.length - 1) : -1);

	/** A new search starts at the first match from the top of the view. */
	function onquery() {
		const top = rowAt(Math.max(0, scrollTop - headH));
		const i = matches.findIndex((m) => m.row >= top);
		current = i === -1 ? 0 : i;
		if (matches.length) reveal(matches[current].row);
	}

	function step(by: 1 | -1) {
		if (!matches.length) return;
		current = (cur + by + matches.length) % matches.length;
		reveal(matches[current].row);
	}

	function onsearchkey(e: KeyboardEvent) {
		if (e.key === 'Enter') {
			e.preventDefault();
			step(e.shiftKey ? -1 : 1);
		}
	}

	/** Ctrl/⌘-F finds in the log: the browser's own find can't see lines scrolled away. */
	function onwindowkey(e: KeyboardEvent) {
		if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === 'f') {
			e.preventDefault();
			void openSearch();
		} else if (e.key === 'F3' || ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'g')) {
			if (!q) return;
			e.preventDefault();
			step(e.shiftKey ? -1 : 1);
		}
	}

	// ------------------------------------------------------------------------- virtual scrolling

	let scrollTop = $state(0);
	let viewH = $state(0);
	let viewW = $state(0);
	/** Height of what's above the lines in the scroller ("Show earlier lines"). */
	let headH = $state(0);
	/** Wrapped lines' measured heights, by line number. */
	// Not reactive on purpose: a batch of measurements bumps `measured` once.
	// eslint-disable-next-line svelte/prefer-svelte-reactivity
	const heights = new Map<number, number>();
	let measured = $state(0);

	const offsets = $derived.by(() => {
		void measured;
		const ls = shown.lines;
		const o = new Float64Array(ls.length + 1);
		for (let i = 0; i < ls.length; i++)
			o[i + 1] = o[i] + (wrap ? (heights.get(ls[i].line) ?? ROW_H) : ROW_H);
		return o;
	});

	/** The row at `y` pixels into the lines. */
	function rowAt(y: number): number {
		let lo = 0;
		let hi = offsets.length - 2;
		if (hi < 0) return 0;
		while (lo < hi) {
			const mid = (lo + hi + 1) >> 1;
			if (offsets[mid] <= y) lo = mid;
			else hi = mid - 1;
		}
		return lo;
	}

	const range = $derived.by(() => {
		const n = shown.lines.length;
		if (!n) return { from: 0, to: 0 };
		const top = scrollTop - headH;
		const from = rowAt(Math.max(0, top - OVERSCAN));
		const to = Math.min(n, rowAt(Math.max(0, top + Math.max(viewH, 600) + OVERSCAN)) + 1);
		return { from, to };
	});
	const visible = $derived(shown.lines.slice(range.from, range.to));

	// Wrapped lines change height with the width: measure again.
	$effect(() => {
		void viewW;
		void wrap;
		heights.clear();
		untrack(() => measured++);
	});

	let observer: ResizeObserver | undefined;
	onMount(() => {
		observer = new ResizeObserver((entries) => {
			if (!wrap || !scroller) return;
			const top = rowAt(Math.max(0, scroller.scrollTop - headH));
			let changed = false;
			let above = 0;
			for (const e of entries) {
				const el = e.target as HTMLElement;
				const line = Number(el.dataset.line);
				const h = e.borderBoxSize?.[0]?.blockSize ?? el.offsetHeight;
				const old = heights.get(line) ?? ROW_H;
				if (h === old && heights.has(line)) continue;
				heights.set(line, h);
				if (h !== old) {
					changed = true;
					if (Number(el.dataset.index) < top) above += h - old;
				}
			}
			if (!changed) return;
			measured++;
			// Lines above the view grew or shrank: keep what's in view where it is.
			if (following && live) void toEnd();
			else if (above) scroller.scrollTop += above;
		});
		return () => observer?.disconnect();
	});

	const measure: Attachment<HTMLElement> = (el) => {
		observer?.observe(el);
		return () => observer?.unobserve(el);
	};

	const atEnd = () =>
		!scroller || scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 24;

	async function toEnd() {
		await tick();
		if (scroller) scroller.scrollTop = scroller.scrollHeight;
	}

	function onscroll() {
		if (!scroller) return;
		scrollTop = scroller.scrollTop;
		if (live) following = atEnd();
	}

	/** Scrolls `row` (an index into the lines shown) into the middle of the view, if it's not in view. */
	async function reveal(row: number, always = false) {
		await tick();
		if (!scroller) return;
		const y = headH + offsets[row];
		const h = offsets[row + 1] - offsets[row];
		const inView = y >= scroller.scrollTop + 8 && y + h <= scroller.scrollTop + viewH - 8;
		if (always || !inView) scroller.scrollTop = y - viewH / 2 + h / 2;
	}

	function follow() {
		following = !following;
		if (following) void toEnd();
	}

	// ------------------------------------------------------------------------- linked lines

	/** Line number (from 0) picked by its link: `#L42`. */
	let selected = $state<number | null>(null);

	function pick(e: MouseEvent, line: number) {
		if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
		e.preventDefault();
		selected = selected === line ? null : line;
		// The page's own path and query (already resolved), with the line's anchor or without.
		const here = `${page.url.pathname}${page.url.search}${selected == null ? '' : `#L${line + 1}`}`;
		replaceState(here as ResolvedPathname, page.state);
	}

	async function onhash() {
		const target = lineFromHash(location.hash);
		if (target == null) return;
		selected = target;
		if (target < first) await loadFrom(Math.max(0, target - 200));
		const row = shown.lines.findIndex((l) => l.line === target);
		if (row !== -1) {
			following = false;
			void reveal(row, true);
		}
	}

	// ---------------------------------------------------------------------------------- loading

	async function fetchLines(offset: number, limit: number) {
		const res = await fetch(`${url}?offset=${offset}&limit=${limit}`);
		if (!res.ok) throw new Error(res.statusText);
		const page = (await res.json()) as RunLogs;
		return { total: page.total, lines: page.lines.map(parseLine) };
	}

	/** Lines from `from` up to those already loaded. */
	async function loadFrom(from: number) {
		if (from >= first) return;
		loading = true;
		try {
			const page = await fetchLines(from, first - from);
			const height = scroller?.scrollHeight ?? 0;
			lines = [...page.lines, ...lines];
			first = from;
			await tick();
			// Stay on the lines that were in view.
			if (scroller) scroller.scrollTop += scroller.scrollHeight - height;
		} catch {
			failed = true;
		} finally {
			loading = false;
		}
	}

	const earlier = () => loadFrom(Math.max(0, first - CHUNK));

	async function newer() {
		const page = await fetchLines(first + lines.length, CHUNK);
		known = page.total;
		if (!page.lines.length) return;
		lines = [...lines, ...page.lines];
		if (following && live) await toEnd();
	}

	let loadingLater = $state(false);
	async function later() {
		loadingLater = true;
		try {
			await newer();
		} catch {
			toast.error('Couldn’t load more of the console output');
		} finally {
			loadingLater = false;
		}
	}

	onMount(() => {
		const target = lineFromHash(location.hash);
		first = Math.max(0, total - CHUNK);
		// A linked line further back: start from just before it.
		if (target != null && target < first) first = Math.max(0, target - 200);
		known = total;
		void fetchLines(first, CHUNK)
			.then(async (page) => {
				lines = page.lines;
				known = page.total;
				if (target != null) await onhash();
				else await toEnd();
			})
			.catch(() => (failed = true))
			.finally(() => (loading = false));
	});

	$effect(() => {
		if (!live) return;
		const id = setInterval(() => void newer().catch(() => {}), POLL_MS);
		return () => clearInterval(id);
	});

	// ------------------------------------------------------------------------- copy, download

	const asText = (ls: LogLine[]) =>
		ls
			.map((l) => {
				// The time as the log shows it, so a pasted line reads the same.
				const at = stampOf(l);
				return at ? `${at}  ${l.text}` : l.text;
			})
			.join('\n');

	async function copy() {
		try {
			await navigator.clipboard.writeText(asText(shown.lines));
			toast(`Copied ${shown.lines.length.toLocaleString('en-US')} lines`);
		} catch {
			toast.error('Couldn’t copy to the clipboard');
		}
	}

	let downloading = $state(false);
	/** Every line, not just those loaded. */
	async function download() {
		downloading = true;
		try {
			const all: string[] = [];
			for (let offset = 0; offset < Math.max(known, 1); offset += 50_000) {
				const res = await fetch(`${url}?offset=${offset}&limit=50000`);
				if (!res.ok) throw new Error(res.statusText);
				const page = (await res.json()) as RunLogs;
				all.push(...page.lines.map((l) => l.text));
				if (page.lines.length < 50_000) break;
			}
			saveBlob(new Blob([all.join('\n') + '\n'], { type: 'text/plain' }), `${fileSafe(name)}.log`);
		} catch {
			toast.error('Couldn’t download the console output');
		} finally {
			downloading = false;
		}
	}

	// -------------------------------------------------------------------------------- rendering

	const SPAN_COLOR: Record<NonNullable<Span['color']>, string> = {
		red: 'text-ansi-red',
		green: 'text-ansi-green',
		yellow: 'text-ansi-yellow',
		blue: 'text-ansi-blue',
		magenta: 'text-ansi-magenta',
		cyan: 'text-ansi-cyan',
		faint: 'text-faint'
	};

	const TOKEN_COLOR: Record<TokenKind, string> = {
		number: 'text-foreground',
		key: 'text-muted-foreground',
		string: 'text-ansi-green',
		time: 'text-faint',
		url: 'text-link underline decoration-faint underline-offset-2',
		error: 'font-semibold text-ansi-red',
		warning: 'font-semibold text-ansi-yellow',
		info: 'text-ansi-blue',
		debug: 'text-faint'
	};

	const ROW_LEVEL: Record<Level, string> = {
		error: 'border-ansi-red bg-ansi-red/15',
		warning: 'border-ansi-yellow bg-ansi-yellow/10',
		info: 'border-transparent',
		debug: 'border-transparent text-muted-foreground'
	};

	function pieceClass(p: Piece) {
		const level = p.token === 'error' || p.token === 'warning' || p.token === 'info';
		// A match takes its own text colour: a token's colour on the match's fill can't be read.
		return [
			p.mark
				? null
				: p.color
					? SPAN_COLOR[p.color]
					: p.dim && !level
						? 'text-faint'
						: p.token && TOKEN_COLOR[p.token],
			p.bold && 'font-semibold',
			p.italic && 'italic',
			p.underline && 'underline',
			p.mark === 'hit' && 'rounded-[2px] bg-ansi-yellow/25 text-foreground',
			p.mark === 'current' &&
				'rounded-[2px] bg-ansi-yellow text-white outline-1 outline-ansi-yellow dark:text-black'
		];
	}

	/** A shown line's pieces: styled text, tokens and search matches. */
	function render(l: LogLine, row: number): Piece[] {
		const hit = cur === -1 ? null : matches[cur];
		const marks: Mark[] = matchesIn(l.text, q).map((s) => ({
			start: s,
			end: s + q.length,
			current: hit != null && hit.row === row && hit.start === s
		}));
		return pieces(l.spans, tokenize(l.text), marks, l.dim);
	}

	/** The levels this log has (and any hidden one), in order: the menu lists only these. */
	const present = $derived(LEVEL_KEYS.filter((k) => counts[k] > 0 || hidden.includes(k)));
	const levelsShown = $derived(present.filter((k) => !hidden.includes(k)).length);
	const laterCount = $derived(Math.max(0, known - first - lines.length));
</script>

<svelte:window onkeydown={onwindowkey} onhashchange={onhash} />

<!-- A log panel in the page's own theme, one step deeper than the page like a chart panel. -->
<div class="absolute inset-0 flex flex-col bg-card text-foreground">
	<!-- The log's controls: in the page's view bar when it gives one (`bar`), quiet like every
	     control there; otherwise a row of their own above the lines. -->
	<div
		class={[
			'flex flex-none items-center gap-0.5',
			!bar && 'min-h-9 flex-wrap border-b border-divider bg-background px-5 py-1'
		]}
		{@attach into(bar)}
	>
		<BarSearch
			bind:this={field}
			bind:value={query}
			things="the log"
			keys="⌘ F"
			clearable={false}
			class="max-sm:w-28"
			oninput={onquery}
			onkeydown={onsearchkey}
		>
			{#snippet trailing()}
				{#if q}
					<span class="flex-none text-xs text-muted-foreground tabular-nums" aria-live="polite">
						{#if matches.length}
							{(cur + 1).toLocaleString('en-US')} / {matches.length.toLocaleString(
								'en-US'
							)}{matches.length >= 10_000 ? '+' : ''}
						{:else}
							No matches
						{/if}
					</span>
					<Button
						variant="ghost"
						size="icon-xs"
						{@attach hint('Previous match  Shift Enter')}
						aria-label="Previous match"
						disabled={!matches.length}
						onclick={() => step(-1)}
					>
						<ChevronUpIcon />
					</Button>
					<Button
						variant="ghost"
						size="icon-xs"
						{@attach hint('Next match  Enter')}
						aria-label="Next match"
						disabled={!matches.length}
						onclick={() => step(1)}
					>
						<ChevronDownIcon />
					</Button>
				{/if}
			{/snippet}
		</BarSearch>

		<!-- How the lines show, in one menu as a view's Display: the levels shown (only those the
		     log has), the timestamps, wrapping. Lit while it hides levels. -->
		<DropdownMenu.Root>
			<DropdownMenu.Trigger>
				{#snippet child({ props })}
					<Button
						{...props}
						variant={hidden.length ? 'quiet-on' : 'quiet'}
						size="icon-bar"
						aria-label={hidden.length
							? `Display, ${levelsShown} of ${present.length} levels shown`
							: 'Display'}
						{@attach hint(
							hidden.length
								? `Display: ${levelsShown} of ${present.length} levels shown`
								: 'Display'
						)}
					>
						<SlidersHorizontalIcon />
					</Button>
				{/snippet}
			</DropdownMenu.Trigger>
			<DropdownMenu.Content align="end" class="w-56">
				{#if present.length > 1}
					<DropdownMenu.Group>
						<DropdownMenu.Label>Levels</DropdownMenu.Label>
						{#each present as k (k)}
							<DropdownMenu.CheckboxItem
								checked={!hidden.includes(k)}
								onCheckedChange={(on) => toggleLevel(k, on)}
							>
								<span
									class={[
										'size-2 flex-none rounded-full',
										k === 'error'
											? 'bg-ansi-red'
											: k === 'warning'
												? 'bg-ansi-yellow'
												: k === 'info'
													? 'bg-ansi-blue'
													: 'bg-faint'
									]}
								></span>
								{LEVEL_LABEL[k]}
								<span class="ml-auto text-xs text-muted-foreground tabular-nums">
									{counts[k].toLocaleString('en-US')}
								</span>
							</DropdownMenu.CheckboxItem>
						{/each}
						{#if hidden.length}
							<DropdownMenu.Item onSelect={() => ((hidden = []), remember())}>
								<span class="size-4" aria-hidden="true"></span> Show all levels
							</DropdownMenu.Item>
						{/if}
					</DropdownMenu.Group>
					<DropdownMenu.Separator />
				{/if}
				<DropdownMenu.Group>
					<DropdownMenu.Label>Timestamps</DropdownMenu.Label>
					<DropdownMenu.RadioGroup
						value={stamps}
						onValueChange={(v) => ((stamps = v as Stamps), remember())}
					>
						{#each STAMPS as s (s.value)}
							<DropdownMenu.RadioItem value={s.value}>{s.label}</DropdownMenu.RadioItem>
						{/each}
					</DropdownMenu.RadioGroup>
				</DropdownMenu.Group>
				<DropdownMenu.Separator />
				<DropdownMenu.CheckboxItem
					checked={wrap}
					onCheckedChange={(on) => ((wrap = on), remember())}
				>
					Wrap long lines
				</DropdownMenu.CheckboxItem>
			</DropdownMenu.Content>
		</DropdownMenu.Root>
		{#if live}
			<Button
				variant={following ? 'quiet-on' : 'quiet'}
				size="icon-bar"
				{@attach hint(
					following ? 'Stop following new lines' : 'Follow new lines as they’re printed'
				)}
				aria-label="Follow new lines"
				aria-pressed={following}
				onclick={follow}
			>
				<ChevronsDownIcon />
			</Button>
		{/if}

		{#if !bar}<span class="ml-auto"></span>{/if}
		<Button
			variant="quiet"
			size="icon-bar"
			class={bar ? 'max-sm:hidden' : undefined}
			{@attach hint('Copy the lines loaded')}
			aria-label="Copy the lines loaded"
			disabled={!shown.lines.length}
			onclick={copy}
		>
			<CopyIcon />
		</Button>
		<Button
			variant="quiet"
			size="icon-bar"
			class={bar ? 'max-sm:hidden' : undefined}
			{@attach hint('Download all of it')}
			aria-label="Download the console output"
			disabled={downloading || !known}
			onclick={download}
		>
			<DownloadIcon />
		</Button>
	</div>

	<!-- A stop of its own, so the keyboard can scroll the log (the line links are left out). -->
	<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
	<div
		class="relative min-h-0 flex-1 overflow-auto outline-none [overflow-anchor:none] focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-inset"
		tabindex="0"
		aria-label="Console output"
		bind:this={scroller}
		bind:clientHeight={viewH}
		bind:clientWidth={viewW}
		{onscroll}
	>
		<div bind:offsetHeight={headH}>
			{#if first > 0 && lines.length}
				<div class="flex items-center gap-3 px-5 pt-3 pb-2 text-[13px] text-muted-foreground">
					<Button variant="outline" size="sm" onclick={earlier} pending={loading}>
						Show earlier lines
					</Button>
					{first.toLocaleString('en-US')} earlier{q ? ', not searched' : ''}
				</div>
			{/if}
		</div>

		{#if failed}
			<EmptyState tone="error">Couldn’t load the console output</EmptyState>
		{:else if !lines.length && loading}
			<Skeleton shape="lines" count={8} class="px-4 py-3" />
		{:else if !lines.length}
			<EmptyState>Nothing printed to the console</EmptyState>
		{:else if !shown.lines.length}
			<EmptyState>
				No lines at the levels shown.
				<button class="text-link hover:underline" onclick={() => ((hidden = []), remember())}>
					Show all levels
				</button>
			</EmptyState>
		{:else}
			<div class="relative" style:height="{offsets[shown.lines.length]}px">
				<div
					class={[
						'absolute top-0 left-0 font-mono text-[12px] leading-5',
						wrap ? 'right-0' : 'w-max min-w-full'
					]}
					style:transform="translateY({offsets[range.from]}px)"
				>
					{#each visible as l, k (l.line)}
						{@const row = range.from + k}
						{@const level = shown.levels[row]}
						<div
							class={[
								'group/line flex border-l-2 py-0.5 pr-5',
								selected === l.line
									? 'border-link bg-selection/45'
									: level
										? ROW_LEVEL[level]
										: 'border-transparent',
								selected !== l.line && 'hover:bg-foreground/6'
							]}
							data-line={l.line}
							data-index={row}
							{@attach wrap && measure}
						>
							<!-- Out of the Tab order: a stop per line would trap the keyboard in the log. -->
							<a
								href="#L{l.line + 1}"
								tabindex="-1"
								class={[
									'w-[8ch] flex-none pr-[2ch] text-right tabular-nums select-none hover:text-foreground hover:underline',
									selected === l.line
										? 'text-foreground'
										: l.stderr
											? 'text-ansi-red/60'
											: 'text-faint/70'
								]}
								{@attach hint(
									l.stderr ? 'Link to this line (printed to stderr)' : 'Link to this line'
								)}
								onclick={(e) => pick(e, l.line)}>{l.line + 1}</a
							>
							{#if stamps !== 'hidden'}
								<span
									class={[
										'flex-none pr-[1.5ch] select-none',
										stamps === 'clock' ? 'w-[23ch]' : 'w-[12.5ch]'
									]}
								>
									{#if l.at != null}
										<span class="text-muted-foreground tabular-nums" {@attach hint(dateTime(l.at))}
											>{stampOf(l)}</span
										>
									{/if}
								</span>
							{/if}
							<span
								class={[
									'min-w-0 flex-1',
									// The error itself, rather than a traceback's frames.
									l.level === 'error' && selected !== l.line && 'text-ansi-red',
									wrap ? 'break-words whitespace-pre-wrap' : 'whitespace-pre'
								]}
								>{#each render(l, row) as p, j (j)}<span class={pieceClass(p)}>{p.text}</span
									>{/each}</span
							>
						</div>
					{/each}
				</div>
			</div>
			<div class="px-5 py-3 text-xs text-muted-foreground">
				{#if laterCount > 0}
					<Button variant="quiet" size="bar" onclick={later} pending={loadingLater}>
						Show later lines
					</Button>
					<span class="ml-2"
						>{laterCount.toLocaleString('en-US')} later{q ? ', not searched' : ''}</span
					>
				{/if}
			</div>
		{/if}
	</div>
</div>
