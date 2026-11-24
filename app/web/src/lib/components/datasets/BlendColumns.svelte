<script lang="ts">
	import ArrowDownIcon from '@lucide/svelte/icons/arrow-down';
	import ArrowUpIcon from '@lucide/svelte/icons/arrow-up';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import { Switch } from '#lib/components/ui/switch/index.js';
	import {
		addedColumn,
		cellMark,
		columnProblems,
		parseValue,
		safeCast,
		sourceColumn,
		typeChoices
	} from '#lib/forks.ts';
	import type { BlendColumn, BlendPreview, ColumnFrom } from '#lib/types.ts';

	// The blend's column map (D175): a row per output column, a cell per source, each a menu of
	// that source's columns, Null, or a value.
	interface Props {
		columns: BlendColumn[];
		preview: BlendPreview | null;
		/** The sources' paths, in the dialog's order. */
		sources: string[];
		disabled?: boolean;
	}

	let { columns = $bindable(), preview, sources, disabled = false }: Props = $props();

	const schemaOf = (source: string) =>
		preview?.sources.find((s) => s.path === source)?.schema ?? [];
	const problems = $derived(preview?.problems ?? []);
	const names = $derived(new Set(columns.map((c) => c.name)));
	const MARKS = { renamed: 'renamed', cast: 'cast', null: 'null', value: 'value' } as const;

	/** One source shown at a time on small screens. */
	let shownSource = $state(0);

	function update(i: number, change: Partial<BlendColumn>) {
		columns = columns.map((c, j) => (j === i ? { ...c, ...change } : c));
	}

	function setFrom(i: number, source: string, from: ColumnFrom) {
		update(i, { from: { ...columns[i].from, [source]: from } });
	}

	function choose(i: number, source: string, choice: string) {
		const column = columns[i];
		if (choice === 'null') return setFrom(i, source, { null: true });
		if (choice === 'value') return setFrom(i, source, { value: '' });
		const name = choice.slice('col:'.length);
		const field = schemaOf(source).find((f) => f.name === name);
		setFrom(
			i,
			source,
			field && field.type !== column.type ? { column: name, cast: true } : { column: name }
		);
	}

	/** A type change keeps each column cell, marking casts again for the new type. */
	function retype(i: number, type: string) {
		const from: Record<string, ColumnFrom> = {};
		for (const [source, f] of Object.entries(columns[i].from)) {
			if ('column' in f) {
				const field = schemaOf(source).find((x) => x.name === f.column);
				from[source] =
					field && field.type !== type ? { column: f.column, cast: true } : { column: f.column };
			} else from[source] = f;
		}
		update(i, { type, from });
	}

	function move(i: number, by: -1 | 1) {
		const next = [...columns];
		[next[i], next[i + by]] = [next[i + by], next[i]];
		columns = next;
	}

	const choiceOf = (f: ColumnFrom | undefined) =>
		!f ? '' : 'null' in f ? 'null' : 'value' in f ? 'value' : `col:${f.column}`;

	function label(f: ColumnFrom | undefined): string {
		if (!f) return 'Choose…';
		if ('null' in f) return 'Null';
		if ('value' in f) return 'Value';
		return f.column;
	}

	const unused = $derived(
		sources.flatMap((s) => (preview?.unused[s] ?? []).map((c) => ({ source: s, ...c })))
	);
	const hasSource = $derived(
		columns.some((c) => sources.every((s) => c.from[s] && 'value' in c.from[s]))
	);
</script>

<div class="grid gap-3">
	{#if sources.length > 1}
		<div class="flex flex-wrap gap-1 sm:hidden" role="tablist" aria-label="Source shown">
			{#each sources as s, k (s)}
				<Button
					size="sm"
					variant={k === shownSource ? 'secondary' : 'ghost'}
					role="tab"
					aria-selected={k === shownSource}
					onclick={() => (shownSource = k)}>{s}</Button
				>
			{/each}
		</div>
	{/if}
	<div class="overflow-x-auto">
		<table class="w-full border-collapse text-[13px]">
			<thead>
				<tr class="border-b text-left text-xs text-muted-foreground">
					<th class="min-w-56 px-2 py-2 font-medium">Column in the blend</th>
					{#each sources as s, k (s)}
						<th
							class={[
								'min-w-44 px-2 py-2 font-medium',
								k !== shownSource && 'hidden sm:table-cell'
							]}
						>
							<span class="block truncate font-mono" title={s}>{s}</span>
						</th>
					{/each}
					<th class="w-24"><span class="sr-only">Order and remove</span></th>
				</tr>
			</thead>
			<tbody>
				{#each columns as column, i (i)}
					{@const own = columnProblems(problems, column.name)}
					<tr class="border-b border-divider align-top">
						<td class="px-2 py-2">
							<div class="grid gap-1.5">
								<Input
									class="h-8 font-mono text-[12.5px]"
									value={column.name}
									aria-label="Name of column {i + 1}"
									{disabled}
									oninput={(e) => update(i, { name: e.currentTarget.value })}
								/>
								<div class="flex items-center gap-2">
									<Select.Root
										type="single"
										value={column.type}
										onValueChange={(v) => retype(i, v)}
										{disabled}
									>
										<Select.Trigger
											size="sm"
											class="h-7 min-w-0 flex-1 font-mono text-xs"
											aria-label="Type of {column.name}"
										>
											<span class="truncate">{column.type}</span>
										</Select.Trigger>
										<Select.Content>
											{#each typeChoices(preview, column.type) as t (t)}
												<Select.Item value={t} label={t}
													><span class="font-mono text-xs">{t}</span></Select.Item
												>
											{/each}
										</Select.Content>
									</Select.Root>
									<label class="flex items-center gap-1.5 text-xs text-muted-foreground">
										<Switch
											checked={column.nullable}
											onCheckedChange={(on) => update(i, { nullable: on })}
											aria-label="{column.name} can be null"
											{disabled}
										/>
										Nullable
									</label>
								</div>
								{#each own as p (p)}
									<p class="text-xs text-destructive">{p.slice(column.name.length + 1).trim()}</p>
								{/each}
							</div>
						</td>
						{#each sources as s, k (s)}
							{@const from = column.from[s]}
							{@const mark = cellMark(column, s)}
							{@const bad = own.some((p) => p.includes(s))}
							<td class={['px-2 py-2', k !== shownSource && 'hidden sm:table-cell']}>
								<div class="grid gap-1.5">
									<Select.Root
										type="single"
										value={choiceOf(from)}
										onValueChange={(v) => choose(i, s, v)}
										{disabled}
									>
										<Select.Trigger
											size="sm"
											class={[
												'h-8 w-full font-mono text-xs',
												bad && 'border-destructive text-destructive'
											]}
											aria-label="{column.name} from {s}"
										>
											<span class="truncate">{label(from)}</span>
										</Select.Trigger>
										<Select.Content>
											{#each [...schemaOf(s)].sort((a, b) => Number(b.type === column.type) - Number(a.type === column.type)) as f (f.name)}
												{@const ok = f.type === column.type || safeCast(f.type, column.type)}
												<Select.Item value="col:{f.name}" label={f.name} disabled={!ok}>
													<span class="font-mono text-xs">{f.name}</span>
													<span class="ml-auto text-[11px] text-muted-foreground">
														{f.type === column.type
															? f.type
															: ok
																? `${f.type} → ${column.type}`
																: `${f.type}, can’t become ${column.type}`}
													</span>
												</Select.Item>
											{/each}
											<Select.Separator />
											<Select.Item value="null" label="Null">Null</Select.Item>
											<Select.Item value="value" label="Value">Value…</Select.Item>
										</Select.Content>
									</Select.Root>
									{#if from && 'value' in from}
										<Input
											class="h-7 font-mono text-xs"
											value={String(from.value)}
											placeholder="Value for every row"
											aria-label="Value of {column.name} for {s}"
											{disabled}
											oninput={(e) =>
												setFrom(i, s, { value: parseValue(e.currentTarget.value, column.type) })}
										/>
									{/if}
									{#if mark}
										<span
											class={[
												'w-fit rounded px-1.5 text-[11px]',
												mark === 'cast'
													? 'bg-amber-500/15 text-amber-700 dark:text-amber-300'
													: 'bg-muted text-muted-foreground'
											]}>{MARKS[mark]}</span
										>
									{/if}
								</div>
							</td>
						{/each}
						<td class="px-1 py-2 text-right whitespace-nowrap">
							<Button
								variant="ghost"
								size="icon-sm"
								aria-label="Move {column.name} up"
								disabled={disabled || i === 0}
								onclick={() => move(i, -1)}><ArrowUpIcon /></Button
							>
							<Button
								variant="ghost"
								size="icon-sm"
								aria-label="Move {column.name} down"
								disabled={disabled || i === columns.length - 1}
								onclick={() => move(i, 1)}><ArrowDownIcon /></Button
							>
							<Button
								variant="ghost"
								size="icon-sm"
								aria-label="Remove {column.name}"
								{disabled}
								onclick={() => (columns = columns.filter((_, j) => j !== i))}><Trash2Icon /></Button
							>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>

	{#if !hasSource}
		<Button
			variant="outline"
			size="sm"
			class="w-fit"
			{disabled}
			onclick={() => (columns = [...columns, sourceColumn(sources, names)])}
		>
			<PlusIcon /> Add a column naming each row’s source
		</Button>
	{/if}

	{#if unused.length}
		<div>
			<h4 class="text-xs font-medium text-muted-foreground">Not in the blend</h4>
			<ul class="mt-1 flex flex-wrap gap-1.5" aria-label="Source columns not in the blend">
				{#each unused as u (`${u.source}:${u.name}`)}
					<li>
						<Button
							variant="outline"
							size="sm"
							class="h-7 text-xs"
							{disabled}
							title="Add {u.name} from {u.source}, null for the other sources"
							onclick={() =>
								(columns = [...columns, addedColumn(u.name, u.type, u.source, sources, names)])}
						>
							<PlusIcon /> <span class="font-mono">{u.name}</span>
							<span class="text-muted-foreground">{u.type} · {u.source}</span>
						</Button>
					</li>
				{/each}
			</ul>
		</div>
	{/if}
</div>
