<script lang="ts">
	import CircleHelpIcon from '@lucide/svelte/icons/circle-help';
	import DicesIcon from '@lucide/svelte/icons/dices';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import XIcon from '@lucide/svelte/icons/x';
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { datasetCall, datasetRef, datasetsApi, pathError, refScope } from '#lib/datasets.ts';
	import type { Scope } from '#lib/scope.ts';
	import { type BlendStart, DEFAULT_SEED, allMatch, randomSeed } from '#lib/forks.ts';
	import type { BlendColumn, BlendPreview, DatasetJob } from '#lib/types.ts';
	import FormError from '../FormError.svelte';
	import BlendColumns from './BlendColumns.svelte';
	import DatasetPicker from './DatasetPicker.svelte';
	import VersionSelect from './VersionSelect.svelte';

	interface Props {
		open: boolean;
		/** Where the blend is made: its sources are browsed and it goes there (022). */
		scope: Scope;
		/** The folder shown, where the blend goes by default. */
		folder: string;
		start?: BlendStart | null;
		onstarted: (job: DatasetJob) => void;
	}

	let { open = $bindable(), scope, folder, start = null, onstarted }: Props = $props();

	interface Row {
		path: string;
		version: string;
		amount: string;
	}

	const blank = (): Row => ({ path: '', version: '', amount: '' });
	/** A source as forks and blends name it (`project:path`), as a path here when it's in this scope. */
	function inScope(ref: string) {
		const where = refScope(scope.org, ref);
		return where.scope.project === scope.project ? where.path : ref;
	}
	/** A path's folder and last segment, for the destination's two fields. */
	const split = (path: string): [string, string] => {
		const at = path.lastIndexOf('/');
		return at < 0 ? ['', path] : [path.slice(0, at), path.slice(at + 1)];
	};

	let rows = $state<Row[]>([blank(), blank()]);
	let mode = $state<'rows' | 'fraction'>('rows');
	let total = $state('');
	let seed = $state(String(DEFAULT_SEED));
	let replace = $state(false);
	let toFolder = $state('');
	let toName = $state('blend');
	/** 1: sources, destination and seed; 2: columns; 3: confirm. */
	let step = $state<1 | 2 | 3>(1);
	/** The map as edited, or null while it's the automatic one. */
	let columns = $state<BlendColumn[] | null>(null);
	let preview = $state<BlendPreview | null>(null);
	let checking = $state(false);
	let error = $state<string | null>(null);
	let pending = $state(false);

	function reset() {
		const s = start;
		rows = s?.sources.length
			? s.sources.map((r) => ({
					path: inScope(r.path),
					version: r.version ?? '',
					amount: r.amount ?? ''
				}))
			: [blank(), blank()];
		while (rows.length < 2) rows.push(blank());
		mode = s?.mode ?? 'rows';
		total = s?.total ?? '';
		seed = String(s?.seed ?? DEFAULT_SEED);
		replace = s?.replace ?? false;
		columns = s?.columns ?? null;
		mappedFor = (s?.sources ?? []).map((r) => r.path).join('\n');
		[toFolder, toName] = s?.to ? split(s.to) : [folder, 'blend'];
		step = 1;
		preview = null;
		error = null;
	}

	$effect(() => {
		if (open) untrack(reset);
	});

	const paths = $derived(rows.map((r) => r.path.trim().replace(/^\/+|\/+$/g, '')));
	// A map edited for other sources no longer fits: back to the automatic one.
	let mappedFor = '';
	$effect(() => {
		const key = paths.join('\n');
		untrack(() => {
			if (columns && key !== mappedFor) columns = null;
		});
	});
	function setColumns(next: BlendColumn[]) {
		columns = next;
		mappedFor = paths.join('\n');
	}

	function amountOf(row: Row): { rows?: number; fraction?: number } {
		const text = row.amount.trim().replace(/,/g, '').replace(/_/g, '');
		if (!text) return {};
		if (mode === 'fraction') return { fraction: Number(text.replace('%', '')) / 100 };
		const suffix = /^(\d+(?:\.\d+)?)([kKmMbB]?)$/.exec(text);
		if (!suffix) return { rows: Number.NaN };
		const scale = { '': 1, k: 1e3, m: 1e6, b: 1e9 }[suffix[2].toLowerCase()] ?? 1;
		return { rows: Math.round(Number(suffix[1]) * scale) };
	}

	const toPath = $derived(
		[toFolder, toName.trim()]
			.filter(Boolean)
			.join('/')
			.replace(/^\/+|\/+$/g, '')
	);
	const body = $derived({
		sources: rows.map((r, i) => ({
			path: datasetRef(scope, paths[i]),
			version: r.version.trim() || null,
			...amountOf(r)
		})),
		total: mode === 'fraction' && total.trim() ? Number(total.replace(/[,_]/g, '')) : null,
		seed: /^\d+$/.test(seed.trim()) ? Number(seed.trim()) : null,
		columns,
		replace,
		to: datasetRef(scope, toPath)
	});
	const ready = $derived(
		paths.filter(Boolean).length >= 2 && paths.every((p) => p && !pathError(p))
	);
	const localProblems = $derived([
		...(body.seed === null ? ['The seed is a whole number.'] : []),
		...(!toName.trim()
			? ['Name the blend.']
			: pathError(toPath)
				? [`Name: ${pathError(toPath)}`]
				: []),
		...(body.sources.some((s) => Number.isNaN(s.rows ?? 0) || Number.isNaN(s.fraction ?? 0))
			? ['Amounts are numbers, such as 100000, 100k or 66%.']
			: [])
	]);
	/** The column map's problems, for step 2; the rest belong to step 1. */
	const columnProblems = $derived(preview?.column_problems ?? []);
	const sourceProblems = $derived([
		...localProblems,
		...(preview?.problems ?? []).filter((p) => !columnProblems.includes(p))
	]);
	const problems = $derived([...sourceProblems, ...columnProblems]);
	const canNext = $derived(
		!!preview &&
			!checking &&
			(step === 1 ? !sourceProblems.length : step === 2 ? !columnProblems.length : false)
	);
	const shownColumns = $derived(columns ?? preview?.columns ?? []);
	const matches = $derived(allMatch(shownColumns, preview?.problems ?? []));

	// The preview, read again a moment after anything changes.
	$effect(() => {
		if (!open || !ready) {
			preview = null;
			return;
		}
		const sent = JSON.stringify(body);
		const controller = new AbortController();
		const timer = setTimeout(async () => {
			checking = true;
			try {
				preview = await datasetCall<BlendPreview>(datasetsApi(scope.org, 'blend/preview'), {
					method: 'POST',
					body: JSON.parse(sent),
					signal: controller.signal
				});
				error = null;
			} catch (e) {
				if (!controller.signal.aborted) {
					preview = null;
					error = (e as Error).message;
				}
			} finally {
				if (!controller.signal.aborted) checking = false;
			}
		}, 300);
		return () => {
			clearTimeout(timer);
			controller.abort();
		};
	});

	async function submit() {
		if (problems.length || !preview) return;
		pending = true;
		error = null;
		try {
			const job = await datasetCall<DatasetJob>(datasetsApi(scope.org, 'blend'), {
				method: 'POST',
				body: { ...body, columns: columns ?? preview.columns }
			});
			toast.success(`Blending into ${job.dest}`);
			open = false;
			onstarted(job);
		} catch (e) {
			error = (e as Error).message;
		} finally {
			pending = false;
		}
	}

	const fmt = (n: number) => n.toLocaleString('en-US');
	const STEPS = ['Sources', 'Columns', 'Confirm'];
	const pct = (n: number | null) =>
		n === null ? '—' : n < 0.001 ? '<0.1%' : `${(n * 100).toFixed(n < 0.1 ? 1 : 0)}%`;
</script>

<Dialog.Root bind:open={() => open, (o) => (open = pending ? true : o)}>
	<Dialog.Content class="max-h-[92vh] overflow-y-auto sm:max-w-4xl" showCloseButton={!pending}>
		<Dialog.Header>
			<Dialog.Title>New blend</Dialog.Title>
			<Dialog.Description>
				A new dataset with a sample of each source’s rows, picked by the seed. No rows are copied,
				and the same seed always picks the same rows.
			</Dialog.Description>
		</Dialog.Header>
		<ol class="flex gap-4 text-[13px]" aria-label="Steps">
			{#each STEPS as name, i (name)}
				<li
					class={[
						'flex items-center gap-1.5',
						step === i + 1 ? 'font-medium' : 'text-muted-foreground'
					]}
					aria-current={step === i + 1 ? 'step' : undefined}
				>
					<span
						class={[
							'grid size-5 place-items-center rounded-full text-[11px] tabular-nums',
							step === i + 1 ? 'bg-primary text-primary-foreground' : 'bg-secondary'
						]}>{i + 1}</span
					>
					{name}
				</li>
			{/each}
		</ol>
		<form
			class="grid gap-5"
			onsubmit={(e) => {
				e.preventDefault();
				if (step < 3) {
					if (canNext) step = (step + 1) as 2 | 3;
				} else void submit();
			}}
		>
			{#if step === 1}
				<section class="grid gap-2" aria-labelledby="blend-sources">
					<div class="flex flex-wrap items-center gap-3">
						<h3 id="blend-sources" class="text-[13px] font-medium">Sources</h3>
						<div
							class="ml-auto flex rounded-md border p-0.5 text-xs"
							role="radiogroup"
							aria-label="Amounts as"
						>
							{#each [['rows', 'Row counts'], ['fraction', 'Percentages of a total']] as [m, text] (m)}
								<button
									type="button"
									role="radio"
									aria-checked={mode === m}
									class={[
										'rounded px-2 py-1',
										mode === m ? 'bg-secondary text-foreground' : 'text-muted-foreground'
									]}
									onclick={() => (mode = m as 'rows' | 'fraction')}>{text}</button
								>
							{/each}
						</div>
					</div>
					<!-- One grid for every row, so the total lines up with the amounts above it. -->
					<div class="grid grid-cols-[minmax(0,1fr)_15rem_8rem_auto] items-center gap-2">
						{#each rows as row, i (i)}
							{@const chosen = !!paths[i] && !pathError(paths[i])}
							<!-- Until a dataset is chosen it has no version, so the picker takes that space too. -->
							<div class={!chosen ? 'col-span-2' : undefined}>
								<DatasetPicker
									{scope}
									{folder}
									aria-label="Source {i + 1}"
									bind:value={row.path}
									disabled={pending}
								/>
							</div>
							{#if chosen}
								<VersionSelect
									{scope}
									path={paths[i]}
									aria-label="Version of source {i + 1}"
									bind:value={row.version}
									disabled={pending}
								/>
							{/if}
							<Input
								class="h-8 text-right tabular-nums"
								placeholder={mode === 'rows' ? '100k' : '50%'}
								aria-label={mode === 'rows'
									? `Rows from source ${i + 1}`
									: `Percentage from source ${i + 1}`}
								bind:value={row.amount}
								disabled={pending}
							/>
							<!-- Out of the tab order, so Tab goes from one input to the next. -->
							<Button
								variant="ghost"
								size="icon-sm"
								tabindex={-1}
								aria-label="Remove source {i + 1}"
								disabled={pending || rows.length <= 2}
								onclick={() => (rows = rows.filter((_, j) => j !== i))}><XIcon /></Button
							>
						{/each}
						<div class="justify-self-start">
							<Button
								variant="outline"
								size="sm"
								disabled={pending || rows.length >= 16}
								onclick={() => (rows = [...rows, blank()])}
							>
								<PlusIcon /> Add a source
							</Button>
						</div>
						{#if mode === 'fraction'}
							<Label for="blend-total" class="justify-self-end text-[13px]">Total rows</Label>
							<Input
								id="blend-total"
								class="h-8 text-right tabular-nums"
								placeholder="300000"
								bind:value={total}
								disabled={pending}
							/>
						{/if}
					</div>
					{#if sourceProblems.length}
						<ul class="grid gap-1 text-[13px] text-destructive" role="alert">
							{#each sourceProblems as p (p)}<li>{p}</li>{/each}
						</ul>
					{/if}
				</section>

				<section class="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_14rem]">
					<div class="grid gap-2">
						<Label>In folder</Label>
						<DatasetPicker
							{scope}
							pick="folder"
							aria-label="In folder"
							bind:value={toFolder}
							disabled={pending}
						/>
					</div>
					<div class="grid gap-2">
						<Label for="blend-name">Name</Label>
						<Input
							id="blend-name"
							class="h-8 font-mono text-[12.5px]"
							bind:value={toName}
							disabled={pending}
							spellcheck="false"
						/>
					</div>
					<div class="grid gap-2">
						<div class="flex items-center gap-1">
							<Label for="blend-seed">Seed</Label>
							<span class="group relative inline-flex">
								<button
									type="button"
									class="rounded-full text-muted-foreground outline-none hover:text-foreground focus-visible:text-foreground"
									aria-label="What the seed does"
									aria-describedby="blend-seed-help"
								>
									<CircleHelpIcon class="size-3.5" />
								</button>
								<span
									id="blend-seed-help"
									role="tooltip"
									class="pointer-events-none invisible absolute right-0 bottom-full z-50 mb-1.5 w-72 rounded-md bg-popover px-2.5 py-2 text-xs leading-relaxed text-popover-foreground opacity-0 shadow-md ring-1 ring-foreground/10 transition-opacity group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100"
								>
									The seed decides which rows are taken from each source. The same sources, amounts
									and seed always give the same blend, so it can be made again exactly; another seed
									gives another sample of the same mix.
								</span>
							</span>
						</div>
						<div class="flex gap-1">
							<Input
								id="blend-seed"
								class="h-8 tabular-nums"
								bind:value={seed}
								disabled={pending}
								inputmode="numeric"
							/>
							<Button
								variant="outline"
								size="icon-sm"
								class="size-8"
								aria-label="Use a random seed"
								title="Use a random seed"
								disabled={pending}
								onclick={() => (seed = String(randomSeed()))}
							>
								<DicesIcon />
							</Button>
						</div>
					</div>
					<label class="flex items-center gap-2 text-[13px] sm:col-span-3">
						<Checkbox bind:checked={replace} disabled={pending} />
						Allow taking a source more than once, when it has fewer rows than asked
					</label>
				</section>
			{:else if step === 2 && preview}
				<section class="grid gap-2" aria-labelledby="blend-columns">
					<p class="flex items-center gap-2 text-[13px]">
						<span id="blend-columns" class="font-medium">Columns</span>
						<span class="text-muted-foreground">
							{#if matches}
								{shownColumns.length}, all sources match
							{:else}
								{shownColumns.length} · {columnProblems.length
									? `${columnProblems.length} need${columnProblems.length === 1 ? 's' : ''} a decision`
									: 'some renamed, cast or filled'}
							{/if}
						</span>
					</p>
					<BlendColumns
						bind:columns={() => shownColumns, setColumns}
						{preview}
						sources={paths}
						disabled={pending}
					/>
					{#if columnProblems.length}
						<ul class="grid gap-1 text-[13px] text-destructive" role="alert">
							{#each columnProblems as p (p)}<li>{p}</li>{/each}
						</ul>
					{/if}
				</section>
			{:else if step === 3 && preview}
				{@const written = [...new Set(preview.writes.map((w) => w.column))]}
				<section class="grid gap-4 text-[13px]" aria-label="Confirm">
					<p>
						Blend <span class="tabular-nums">{fmt(preview.total)}</span> rows into
						<span class="font-mono text-[12.5px]">{body.to}</span> with seed
						<span class="tabular-nums">{body.seed}</span>{replace ? ', oversampled' : ''}.
					</p>
					<table class="w-full tabular-nums">
						<thead class="text-left text-xs text-muted-foreground">
							<tr class="border-b">
								<th class="py-1.5 font-normal">Source</th>
								<th class="py-1.5 font-normal">Version</th>
								<th class="py-1.5 text-right font-normal">Rows taken</th>
								<th class="py-1.5 text-right font-normal">Of its rows</th>
							</tr>
						</thead>
						<tbody>
							{#each preview.sources as s, i (s.path)}
								{@const asked = rows[i]?.version.trim() ?? ''}
								<tr class="border-b last:border-0">
									<td class="py-1.5 font-mono text-[12.5px]">{s.path}</td>
									<td class="py-1.5">
										v{s.version}{!asked ? ' · latest' : /^\d+$/.test(asked) ? '' : ` · ${asked}`}
									</td>
									<td class="py-1.5 text-right">{fmt(s.rows)}</td>
									<td class="py-1.5 text-right">{pct(s.share)} of {fmt(s.live)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
					<p>
						{shownColumns.length} column{shownColumns.length === 1 ? '' : 's'}:
						<span class="font-mono text-[12.5px]">{shownColumns.map((c) => c.name).join(', ')}</span
						>.
						{#if written.length}
							Writes only the {written.join(', ')} column{written.length === 1 ? '' : 's'} for the sources
							that fill or cast {written.length === 1 ? 'it' : 'them'}; every other value is read
							from the sources in place.
						{:else}
							No row data is written: every value is read from the sources in place.
						{/if}
					</p>
					{#if problems.length}
						<ul class="grid gap-1 text-destructive" role="alert">
							{#each problems as p (p)}<li>{p}</li>{/each}
						</ul>
					{/if}
				</section>
			{/if}
			<FormError {error} />
			<Dialog.Footer>
				{#if step === 1}
					<Button type="button" variant="outline" disabled={pending} onclick={() => (open = false)}
						>Cancel</Button
					>
				{:else}
					<Button
						type="button"
						variant="outline"
						disabled={pending}
						onclick={() => (step = (step - 1) as 1 | 2)}>Back</Button
					>
				{/if}
				{#if step < 3}
					<Button type="submit" disabled={!canNext}>
						{#if checking}<LoaderCircleIcon class="animate-spin" />{/if}
						Next
					</Button>
				{:else}
					<Button type="submit" disabled={pending || checking || !preview || problems.length > 0}>
						{#if pending || checking}<LoaderCircleIcon class="animate-spin" />{/if}
						{pending ? 'Starting…' : 'Create blend'}
					</Button>
				{/if}
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
