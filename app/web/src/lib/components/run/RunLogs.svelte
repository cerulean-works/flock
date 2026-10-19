<script lang="ts">
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ClockIcon from '@lucide/svelte/icons/clock';
	import CopyIcon from '@lucide/svelte/icons/copy';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import SearchIcon from '@lucide/svelte/icons/search';
	import { onMount, tick } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import {
		blocksOf,
		clock,
		elapsed,
		parseLine,
		type DictValue,
		type Level,
		type LogLine,
		type Span
	} from '#lib/logs.ts';
	import type { RunLogs } from '#lib/types.ts';
	import { dateTime, fileSafe, formatNumber, saveBlob } from '#lib/utils.ts';

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
	}

	let { url, total, live, start, name }: Props = $props();

	/** Lines shown at first, and added by each "Show earlier". */
	const CHUNK = 1000;
	const POLL_MS = 5000;

	let lines = $state.raw<LogLine[]>([]);
	/** Line number of the first line loaded; earlier ones are fetched on request. */
	let first = $state(0);
	let known = $state(0);
	let loading = $state(true);
	let failed = $state(false);
	let scroller = $state<HTMLDivElement>();

	// ---------------------------------------------------------------------------- view options

	type Stamps = 'clock' | 'elapsed' | 'hidden';
	const STAMPS: { value: Stamps; label: string }[] = [
		{ value: 'clock', label: 'Time (UTC)' },
		{ value: 'elapsed', label: 'Since the run started' },
		{ value: 'hidden', label: 'Hidden' }
	];
	const PREFS = 'flock:logs';
	let stamps = $state<Stamps>('clock');
	/** Lines as printed, without tables, bars or grouped tracebacks. */
	let raw = $state(false);
	let query = $state('');

	onMount(() => {
		try {
			const saved = JSON.parse(localStorage.getItem(PREFS) ?? '{}');
			if (STAMPS.some((s) => s.value === saved.stamps)) stamps = saved.stamps;
			raw = saved.raw === true;
		} catch {
			// Defaults.
		}
	});

	function remember() {
		try {
			localStorage.setItem(PREFS, JSON.stringify({ stamps, raw }));
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

	const q = $derived(query.trim().toLowerCase());
	const shown = $derived(q ? lines.filter((l) => l.text.toLowerCase().includes(q)) : lines);
	const blocks = $derived(
		raw ? shown.map((line) => ({ kind: 'line' as const, line })) : blocksOf(shown)
	);

	// ---------------------------------------------------------------------------------- loading

	async function fetchLines(offset: number, limit: number) {
		const res = await fetch(`${url}?offset=${offset}&limit=${limit}`);
		if (!res.ok) throw new Error(res.statusText);
		const page = (await res.json()) as RunLogs;
		return { total: page.total, lines: page.lines.map(parseLine) };
	}

	/** Keeps the view pinned to the end while it's there, like a terminal. */
	const atEnd = () =>
		!scroller || scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 24;

	async function follow(update: () => void) {
		const stick = atEnd();
		update();
		await tick();
		if (stick && scroller) scroller.scrollTop = scroller.scrollHeight;
	}

	async function earlier() {
		const from = Math.max(0, first - CHUNK);
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

	async function newer() {
		const page = await fetchLines(first + lines.length, CHUNK);
		known = page.total;
		if (page.lines.length) await follow(() => (lines = [...lines, ...page.lines]));
	}

	onMount(() => {
		first = Math.max(0, total - CHUNK);
		known = total;
		void fetchLines(first, CHUNK)
			.then((page) => follow(() => ((lines = page.lines), (known = page.total))))
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
		ls.map((l) => (l.at == null ? l.text : `${dateTime(l.at)}  ${l.text}`)).join('\n');

	async function copy() {
		try {
			await navigator.clipboard.writeText(asText(shown));
			toast(q ? `Copied ${shown.length} matching lines` : `Copied ${shown.length} lines`);
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

	const LEVEL: Record<Level, { row: string; badge: string; label: string }> = {
		error: { row: 'bg-ansi-red/7', badge: 'bg-ansi-red/12 text-ansi-red', label: 'Error' },
		warning: {
			row: 'bg-ansi-yellow/7',
			badge: 'bg-ansi-yellow/14 text-ansi-yellow',
			label: 'Warning'
		},
		info: { row: '', badge: 'bg-ansi-blue/10 text-ansi-blue', label: 'Info' },
		debug: { row: 'text-muted-foreground', badge: 'bg-muted text-muted-foreground', label: 'Debug' }
	};

	/** Spans split after `n` characters: a logger's prefix, and the message. */
	function cut(spans: Span[], n: number): [Span[], Span[]] {
		if (!n) return [[], spans];
		const head: Span[] = [];
		const tail: Span[] = [];
		let left = n;
		for (const s of spans) {
			if (left >= s.text.length) head.push(s);
			else if (left <= 0) tail.push(s);
			else {
				head.push({ ...s, text: s.text.slice(0, left) });
				tail.push({ ...s, text: s.text.slice(left) });
			}
			left -= s.text.length;
		}
		return [head, tail];
	}

	/** Pieces of `text` with the search's matches marked. */
	function marked(text: string): { text: string; hit: boolean }[] {
		if (!q) return [{ text, hit: false }];
		const out: { text: string; hit: boolean }[] = [];
		const lower = text.toLowerCase();
		let at = 0;
		for (let i = lower.indexOf(q); i !== -1; i = lower.indexOf(q, at)) {
			if (i > at) out.push({ text: text.slice(at, i), hit: false });
			out.push({ text: text.slice(i, i + q.length), hit: true });
			at = i + q.length;
		}
		if (at < text.length) out.push({ text: text.slice(at), hit: false });
		return out;
	}

	function show(v: DictValue): string {
		if (typeof v === 'number') return formatNumber(v);
		if (v == null) return 'None';
		if (typeof v === 'object') return JSON.stringify(v);
		return String(v);
	}

	const isNumber = (v: DictValue) => typeof v === 'number';
	// `train/loss`, `eval_loss`: the part before the last separator is muted in table headers.
	const split = (key: string) => {
		const i = Math.max(key.lastIndexOf('/'), key.lastIndexOf('.'));
		return i > 0 ? [key.slice(0, i + 1), key.slice(i + 1)] : ['', key];
	};

	const ROW = 'grid grid-cols-subgrid col-span-3 items-baseline gap-x-3 px-5';
</script>

{#snippet text(spans: Span[])}
	{#each spans as s, i (i)}
		<span
			class={[
				s.color && SPAN_COLOR[s.color],
				s.bold && 'font-semibold',
				s.italic && 'italic',
				s.underline && 'underline'
			]}
			>{#each marked(s.text) as m, j (j)}{#if m.hit}<mark
						class="rounded-[3px] bg-selection text-foreground">{m.text}</mark
					>{:else}{m.text}{/if}{/each}</span
		>
	{/each}
{/snippet}

{#snippet gutter(l: LogLine)}
	<!-- stderr's line numbers are tinted: tqdm, warnings and errors print there. -->
	<span
		class={['text-right tabular-nums select-none', l.stderr ? 'text-ansi-red/70' : 'text-faint']}
		title={l.stderr ? 'Printed to stderr' : undefined}>{l.line + 1}</span
	>
	<span
		class="whitespace-nowrap text-muted-foreground tabular-nums select-none"
		title={l.at == null ? undefined : dateTime(l.at)}>{stampOf(l)}</span
	>
{/snippet}

<div class="absolute inset-0 flex flex-col">
	<div class="flex flex-none flex-wrap items-center gap-2 border-b border-divider px-5 py-2.5">
		<DropdownMenu.Root>
			<DropdownMenu.Trigger>
				{#snippet child({ props })}
					<Button {...props} variant="outline" size="sm" title="Timestamps">
						<ClockIcon />
						{STAMPS.find((s) => s.value === stamps)?.label}
						<ChevronDownIcon class="text-muted-foreground" />
					</Button>
				{/snippet}
			</DropdownMenu.Trigger>
			<DropdownMenu.Content align="start" class="w-56">
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
				<DropdownMenu.CheckboxItem checked={raw} onCheckedChange={(v) => ((raw = v), remember())}>
					Show lines as printed
				</DropdownMenu.CheckboxItem>
			</DropdownMenu.Content>
		</DropdownMenu.Root>
		<label
			class="flex h-7 min-w-48 flex-1 items-center gap-2 rounded-[min(var(--radius-md),10px)] border border-input bg-background px-2 focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50"
		>
			<SearchIcon class="size-3.5 flex-none text-muted-foreground" />
			<input
				class="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-faint"
				placeholder="Search the console output"
				aria-label="Search the console output"
				bind:value={query}
				onkeydown={(e) => e.key === 'Escape' && (query = '')}
			/>
			{#if q}
				<span class="flex-none text-xs text-muted-foreground tabular-nums">
					{shown.length.toLocaleString('en-US')} of {lines.length.toLocaleString('en-US')}
				</span>
			{/if}
		</label>
		<Button
			variant="ghost"
			size="icon-sm"
			title={q ? 'Copy matching lines' : 'Copy lines shown'}
			aria-label={q ? 'Copy matching lines' : 'Copy lines shown'}
			disabled={!shown.length}
			onclick={copy}
		>
			<CopyIcon />
		</Button>
		<Button
			variant="ghost"
			size="icon-sm"
			title="Download all of it"
			aria-label="Download the console output"
			disabled={downloading || !known}
			onclick={download}
		>
			<DownloadIcon />
		</Button>
	</div>

	<div class="min-h-0 flex-1 overflow-auto" bind:this={scroller}>
		{#if first > 0}
			<div class="flex items-center gap-3 px-5 pt-3 text-[13px] text-muted-foreground">
				<Button variant="outline" size="sm" onclick={earlier} disabled={loading}>
					{loading ? 'Loading…' : 'Show earlier lines'}
				</Button>
				{first.toLocaleString('en-US')} earlier{q ? ', not searched' : ''}
			</div>
		{/if}
		{#if failed}
			<p class="px-5 py-6 text-destructive">Couldn’t load the console output</p>
		{:else if !lines.length}
			<p class="px-5 py-6 text-muted-foreground">
				{loading ? 'Loading…' : 'Nothing printed to the console'}
			</p>
		{:else if !shown.length}
			<p class="px-5 py-6 text-muted-foreground">No lines match “{query.trim()}”</p>
		{:else}
			<!-- One grid, so line numbers and times line up down the whole log. -->
			<div
				class={[
					'grid py-3 font-mono text-[12.5px] leading-[1.6]',
					stamps === 'hidden'
						? 'grid-cols-[5ch_0_minmax(0,1fr)]'
						: 'grid-cols-[5ch_auto_minmax(0,1fr)]'
				]}
			>
				{#each blocks as b (b.kind === 'line' ? b.line.line : b.kind === 'table' ? `t${b.rows[0].line}` : `x${b.lines[0].line}`)}
					{#if b.kind === 'line'}
						{@const l = b.line}
						<div class={[ROW, 'hover:bg-muted', l.level && LEVEL[l.level].row]}>
							{@render gutter(l)}
							{#if l.dict}
								<!-- A dict on its own: its entries as chips. -->
								<div class="flex flex-wrap gap-1.5 py-0.5 font-sans">
									{#each l.dict as [key, value] (key)}
										<span
											class="inline-flex items-baseline gap-1.5 rounded-md bg-muted px-1.5 py-px text-[12px]"
											title={typeof value === 'string' ? value : undefined}
										>
											<span class="text-muted-foreground">{key}</span>
											<span class={['font-medium', isNumber(value) && 'tabular-nums']}>
												{show(value)}
											</span>
										</span>
									{/each}
								</div>
							{:else if l.progress}
								{@const p = l.progress}
								<div class="flex min-w-0 flex-wrap items-center gap-x-3 font-sans text-[12.5px]">
									{#if p.label}<span class="font-medium">{p.label}</span>{/if}
									<span
										class="h-1.5 w-40 flex-none overflow-hidden rounded-full bg-muted"
										role="progressbar"
										aria-valuenow={p.percent}
										aria-valuemin={0}
										aria-valuemax={100}
										aria-label={p.label || 'Progress'}
									>
										<span
											class={[
												'block h-full rounded-full',
												p.percent === 100 ? 'bg-state-finished' : 'bg-state-running'
											]}
											style:width="{p.percent}%"
										></span>
									</span>
									<span class="tabular-nums">{p.percent}%</span>
									<span class="font-mono text-[12px] text-muted-foreground">{p.rest}</span>
								</div>
							{:else}
								{@const [head, message] = raw ? [[], l.spans] : cut(l.spans, l.dim)}
								<div class="flex min-w-0 items-baseline gap-2">
									{#if l.level && !raw}
										<span
											class={[
												'flex-none rounded px-1 font-sans text-[10.5px] leading-4 font-medium tracking-wide uppercase',
												LEVEL[l.level].badge
											]}>{LEVEL[l.level].label}</span
										>
									{/if}
									<span class="min-w-0 break-words whitespace-pre-wrap"
										>{#if head.length}<span class="text-faint">{@render text(head)}</span
											>{/if}{@render text(message)}</span
									>
								</div>
							{/if}
						</div>
					{:else if b.kind === 'table'}
						<!-- Lines printing the same metrics, as one table. -->
						<div class="col-span-3 px-5 py-1.5">
							<div class="overflow-x-auto rounded-lg ring-1 ring-foreground/10">
								<table class="w-full font-sans text-[12.5px] tabular-nums">
									<thead class="bg-muted/60 text-xs text-muted-foreground">
										<tr>
											<th class="w-[5ch] px-2 py-1.5 text-right font-normal">#</th>
											{#if stamps !== 'hidden'}
												<th class="px-2 py-1.5 text-left font-normal">
													{stamps === 'elapsed' ? 'Since start' : 'Time'}
												</th>
											{/if}
											{#each b.keys as key (key)}
												{@const [head, tail] = split(key)}
												<th
													class="px-2 py-1.5 text-right font-medium whitespace-nowrap text-foreground"
													title={key}
												>
													<span class="font-normal text-muted-foreground">{head}</span>{tail}
												</th>
											{/each}
										</tr>
									</thead>
									<tbody>
										{#each b.rows as l (l.line)}
											<tr class="border-t border-divider hover:bg-muted">
												<td class="px-2 py-1 text-right font-mono text-[12px] text-faint">
													{l.line + 1}
												</td>
												{#if stamps !== 'hidden'}
													<td
														class="px-2 py-1 font-mono text-[12px] whitespace-nowrap text-muted-foreground"
														title={l.at == null ? undefined : dateTime(l.at)}
													>
														{stampOf(l)}
													</td>
												{/if}
												{#each l.dict! as [key, value] (key)}
													<td
														class="px-2 py-1 text-right whitespace-nowrap"
														title={typeof value === 'number' ? String(value) : undefined}
													>
														{show(value)}
													</td>
												{/each}
											</tr>
										{/each}
									</tbody>
								</table>
							</div>
						</div>
					{:else}
						<!-- A Python traceback: the exception up top, the frames under it. -->
						<details class="group/tb col-span-3 px-5 py-1.5" open>
							<summary
								class="flex cursor-pointer list-none items-baseline gap-2 rounded-t-lg border border-ansi-red/25 bg-ansi-red/7 px-3 py-1.5 group-not-open/tb:rounded-b-lg [&::-webkit-details-marker]:hidden"
							>
								<span
									class="flex-none rounded bg-ansi-red/12 px-1 font-sans text-[10.5px] leading-4 font-medium tracking-wide text-ansi-red uppercase"
									>Traceback</span
								>
								<span class="min-w-0 font-medium break-words whitespace-pre-wrap text-ansi-red"
									>{@render text(b.error.spans)}</span
								>
								<span class="ml-auto flex-none font-sans text-xs text-muted-foreground">
									line {b.lines[0].line + 1}{b.lines[0].at != null && stamps !== 'hidden'
										? ` · ${stampOf(b.lines[0])}`
										: ''}
								</span>
							</summary>
							<ol class="overflow-x-auto rounded-b-lg border border-t-0 border-ansi-red/25 py-1.5">
								{#each [...b.lines, b.error] as l (l.line)}
									<li
										class={[
											'flex gap-3 px-3',
											/^\s+File "/.test(l.text) ? 'text-foreground' : 'text-muted-foreground'
										]}
									>
										<span class="w-[5ch] flex-none text-right text-faint select-none"
											>{l.line + 1}</span
										>
										<span class="whitespace-pre">{@render text(l.spans)}</span>
									</li>
								{/each}
							</ol>
						</details>
					{/if}
				{/each}
			</div>
			{#if live}
				<p class="px-5 pb-6 text-xs text-muted-foreground">
					{known.toLocaleString('en-US')} lines · new ones appear as the run prints them
				</p>
			{/if}
		{/if}
	</div>
</div>
