import type { ComponentProps } from 'svelte';
import type ColumnFilterPanel from '#lib/components/ColumnFilterPanel.svelte';
import { columnChoices, type RunValues } from '#lib/data/run-values.svelte.ts';
import type { ColumnKind } from '#lib/types.ts';
import {
	columnFilterKind,
	readColumnFilter,
	setColumnFilter,
	type People,
	type RunFilter
} from './filters.ts';

export type ColumnFilterProps = Omit<
	ComponentProps<typeof ColumnFilterPanel>,
	'onclose' | 'onback'
>;

/**
 * What a column's filter shows and does, from its header or from the filter's column list:
 * its kind, its values with counts, what it holds now, and `apply` with the filters it makes.
 */
export function columnFilterProps(
	column: { key: string; kind?: ColumnKind },
	label: string,
	filters: readonly RunFilter[],
	counted: RunValues,
	people: People,
	me: number | undefined,
	apply: (filters: RunFilter[]) => void
): ColumnFilterProps {
	const { key, kind } = column;
	const as = columnFilterKind(key, kind);
	const now = readColumnFilter(filters, key, kind);
	return {
		label,
		active: now.active,
		kind: as === 'date' ? 'range' : as,
		date: as === 'date',
		values: as === 'values' ? columnChoices(kind, counted[key], people, me) : [],
		selected: now.values,
		range: now.range,
		text: now.text,
		onselect: (values) => apply(setColumnFilter(filters, key, kind, { values })),
		onrange: (range) => apply(setColumnFilter(filters, key, kind, { range })),
		ontext: (text) => apply(setColumnFilter(filters, key, kind, { text })),
		onclear: () => apply(setColumnFilter(filters, key, kind))
	};
}
