<script lang="ts" module>
	/** A column of a `DataTable`. */
	export interface Column {
		id: string;
		/** The header's label; also the screen-reader name of an unlabelled column. */
		label: string;
		/**
		 * How it's sized. `fit` (numbers, times, states): as wide as its widest value, and never
		 * narrower than 6rem, so the columns along the right keep an even rhythm. `fill` (names,
		 * projects, people): its content's width, plus an equal share of what's left. Right-aligned
		 * columns fit by default; the others fill.
		 */
		size?: 'fit' | 'fill';
		/**
		 * The column's grid track as written (`6rem`, `minmax(12rem,2fr)`), in place of `size`:
		 * for tables stacked on one page whose columns line up, such as a run's suites and tasks.
		 */
		width?: string;
		/** Right-aligned, for numbers and times. */
		align?: 'end';
		/** Shown from this window width up; always shown when omitted. */
		from?: 'sm' | 'md' | 'lg' | 'xl' | '2xl';
		/** The header says nothing: for a column whose content explains itself. */
		quiet?: boolean;
	}

	/**
	 * Put on the link in a row's main cell: it then covers the whole row, while the row's
	 * checkbox and menu stay above it.
	 */
	export const ROW_LINK = 'after:absolute after:inset-0';
</script>

<script lang="ts" generics="T">
	import type { Snippet } from 'svelte';
	import RowCheckbox from '#lib/components/RowCheckbox.svelte';
	import { listKeys } from '#lib/keys.svelte.ts';
	import type { Selection } from '#lib/selection.svelte.ts';

	interface Props {
		columns: Column[];
		rows: readonly T[];
		/** Each row's key: its selection key too, when rows can be checked. */
		key: (row: T) => string;
		/** One cell of a row, by column id. */
		cell: Snippet<[T, string]>;
		/**
		 * Before the first column, as the first thing on each row: an icon. A row that can be
		 * checked shows its checkbox here instead while hovered, or once anything is checked.
		 */
		lead?: Snippet<[T]>;
		/** Checkboxes in front of the rows that `selectable` allows (every row by default). */
		selection?: Selection | null;
		selectable?: (row: T) => boolean;
		/** The row's name, for its checkbox's label and the row's own (screen readers). */
		name?: (row: T) => string;
		/** The row this page is showing (a detail page's version): marked for screen readers. */
		current?: (row: T) => boolean;
		/** A ⋯ menu after the last column. */
		menu?: Snippet<[T]>;
		/** Rows with one line of content are shorter. */
		dense?: boolean;
		/** In place of the rows when there are none. */
		empty?: Snippet;
		/** Under the rows: a "Show more" button, an error. */
		footer?: Snippet;
		/** The table's name, for screen readers. */
		label?: string;
		/** A header cell's content, in place of its label: a column that filters, say. */
		head?: Snippet<[Column]>;
	}

	let {
		columns,
		rows,
		key,
		cell,
		lead,
		selection = null,
		selectable = () => true,
		name = key,
		current,
		menu,
		dense = false,
		empty,
		footer,
		label,
		head
	}: Props = $props();

	// The grid's tracks at each width, from the columns shown there: an edge on each side, so
	// rows and their hover reach the page's edges while the gaps keep the content in from them.
	// Every row shares the table's columns (a subgrid), so a column is as wide as its widest
	// value in any row, as a real table's would be. Tailwind reads the variables from fixed
	// class names, so any set of columns works without new classes.
	const BREAKPOINTS = ['base', 'sm', 'md', 'lg', 'xl', '2xl'] as const;
	const RANK = { base: 0, sm: 1, md: 2, lg: 3, xl: 4, '2xl': 5 };
	const fits = (c: Column) => (c.size ?? (c.align === 'end' ? 'fit' : 'fill')) === 'fit';
	const track = (c: Column) => c.width ?? (fits(c) ? 'minmax(6rem,max-content)' : 'minmax(0,auto)');
	const tracks = $derived(
		Object.fromEntries(
			BREAKPOINTS.map((at) => [
				at,
				[
					'0px',
					...columns.filter((c) => RANK[c.from ?? 'base'] <= RANK[at]).map(track),
					// A little more room on the right, matching the header's buttons above.
					'0.5rem'
				].join(' ')
			])
		)
	);
	/** The first column starts with a slot for the icon or the checkbox. */
	const slotted = $derived(!!lead || !!selection);
	const ROW = 'col-span-full grid grid-cols-subgrid items-center';
	const SHOWN = {
		sm: 'hidden sm:block',
		md: 'hidden md:block',
		lg: 'hidden lg:block',
		xl: 'hidden xl:block',
		'2xl': 'hidden 2xl:block'
	};
	// The first column shown at a width starts after the edge track, whichever it is there (a
	// column shown only from `sm` up hands that place to the next on a phone).
	const START = {
		base: 'col-start-2',
		sm: 'sm:col-start-2',
		md: 'md:col-start-2',
		lg: 'lg:col-start-2',
		xl: 'xl:col-start-2',
		'2xl': '2xl:col-start-2'
	};
	const AUTO = {
		base: 'col-start-auto',
		sm: 'sm:col-start-auto',
		md: 'md:col-start-auto',
		lg: 'lg:col-start-auto',
		xl: 'xl:col-start-auto',
		'2xl': '2xl:col-start-auto'
	};
	const firsts = $derived(
		BREAKPOINTS.map((at) => columns.find((c) => RANK[c.from ?? 'base'] <= RANK[at])?.id)
	);
	const startClass = (c: Column) =>
		BREAKPOINTS.map((at, k) => {
			const first = firsts[k] === c.id;
			const before = k > 0 && firsts[k - 1] === c.id;
			if (first && !before) return START[at];
			if (!first && before) return AUTO[at];
			return false;
		});
	const cellClass = (c: Column, i: number) => [
		'min-w-0',
		i === 0 ? 'col-start-2' : startClass(c),
		c.from && SHOWN[c.from],
		c.align === 'end' && 'text-right tabular-nums'
	];
</script>

<!--
	The full-page table: a sticky header and full-bleed rows across the page, as on an org's
	runs, datasets, models and storage. Pages lay it in a `ListPage`; only the cells are theirs.
-->
<div
	class="group/table grid grid-cols-(--cols-base) gap-x-5 text-[13px] sm:grid-cols-(--cols-sm) md:grid-cols-(--cols-md) lg:grid-cols-(--cols-lg) xl:grid-cols-(--cols-xl) 2xl:grid-cols-(--cols-2xl)"
	role="table"
	aria-label={label}
	style:--cols-base={tracks.base}
	style:--cols-sm={tracks.sm}
	style:--cols-md={tracks.md}
	style:--cols-lg={tracks.lg}
	style:--cols-xl={tracks.xl}
	style:--cols-2xl={tracks['2xl']}
>
	<div
		class={[
			ROW,
			'sticky top-0 z-2 h-9 border-b border-divider bg-background text-xs font-medium text-muted-foreground'
		]}
		role="row"
	>
		{#each columns as c, i (c.id)}
			<span
				class={[cellClass(c, i), i === 0 && slotted && 'flex items-center gap-2.5']}
				role="columnheader"
			>
				{#if i === 0 && slotted}
					<span class="relative z-1 flex size-4 flex-none items-center justify-center">
						{#if selection}<RowCheckbox {selection} />{/if}
					</span>
				{/if}
				{#if c.quiet}<span class="sr-only">{c.label}</span>{:else if head}{@render head(
						c
					)}{:else}{c.label}{/if}
			</span>
		{/each}
	</div>
	<div class="col-span-full grid grid-cols-subgrid" role="rowgroup" {@attach listKeys()}>
		{#each rows as row (key(row))}
			{@const on = !!selection?.has(key(row))}
			<div
				class={[
					ROW,
					'group/row relative border-b border-divider',
					dense ? 'min-h-9 py-1' : 'min-h-9 py-1.5',
					on
						? 'bg-selection/45'
						: 'hover:bg-muted data-cursor:bg-muted data-cursor:shadow-[inset_2px_0_0_var(--ring)]'
				]}
				data-keyrow
				role="row"
				aria-label={name(row)}
				aria-selected={selection ? on : undefined}
				aria-current={current?.(row) ? 'true' : undefined}
			>
				{#each columns as c, i (c.id)}
					{#if i === 0 && slotted}
						{@const checkable = !!selection && selectable(row)}
						<div class={[cellClass(c, i), 'flex items-center gap-2.5']} role="cell">
							<span class="relative z-1 grid size-4 flex-none place-items-center">
								{#if lead}
									<!-- The icon gives way to the checkbox, which takes its place. -->
									<span
										class={[
											'col-start-1 row-start-1 flex text-muted-foreground [&_svg]:size-4',
											checkable &&
												(selection?.size
													? 'invisible'
													: 'group-focus-within/row:invisible group-hover/row:invisible pointer-coarse:invisible')
										]}
									>
										{@render lead(row)}
									</span>
								{/if}
								{#if checkable && selection}
									<span class="col-start-1 row-start-1 flex">
										<RowCheckbox {selection} key={key(row)} name={name(row)} />
									</span>
								{/if}
							</span>
							<div class="min-w-0 flex-1">{@render cell(row, c.id)}</div>
						</div>
					{:else}
						<!-- The last cell gives way to the row's menu, which takes its place on hover. -->
						{@const under = menu && i === columns.length - 1}
						<div
							class={[
								cellClass(c, i),
								under &&
									'group-focus-within/row:invisible group-hover/row:invisible group-has-data-[state=open]/row:invisible pointer-coarse:invisible'
							]}
							role="cell"
						>
							{@render cell(row, c.id)}
						</div>
					{/if}
				{/each}
				{#if menu}
					<!--
						Over the row's end rather than in a column of its own, so every table's last
						column ends at the page's edge. Shown on hover, focus or while open; always on
						touch screens.
					-->
					<span
						class="absolute inset-y-0 right-0 z-1 flex items-center pr-6 opacity-0 group-focus-within/row:opacity-100 group-hover/row:opacity-100 has-data-[state=open]:opacity-100 pointer-coarse:bg-none pointer-coarse:pr-0.5 pointer-coarse:pl-0 pointer-coarse:opacity-100"
						role="cell"
					>
						{@render menu(row)}
					</span>
				{/if}
			</div>
		{:else}
			{#if empty}
				<div class={[ROW, 'border-b border-divider py-6 text-muted-foreground']} role="row">
					<span class="col-span-full pr-7 pl-5" role="cell">{@render empty()}</span>
				</div>
			{/if}
		{/each}
	</div>
</div>
{#if footer}
	<div class="flex items-center gap-3 pt-4 pr-7 pl-5">{@render footer()}</div>
{/if}
