<script lang="ts">
	import { SYSTEM_X_AXES, X_AXES, axisChoiceLabel } from '#lib/chart/axes.ts';
	import type { MetricStream } from '#lib/types.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import Autocomplete, { type AutocompleteOption } from './Autocomplete.svelte';

	/**
	 * W&B's x-axis picker: Step, the times, or any logged metric, searchable. With `inherit`,
	 * its first option follows the workspace (a null value).
	 */

	interface Props {
		value: string | null;
		onchange: (value: string | null) => void;
		/** System metrics can only go on time axes. */
		stream?: MetricStream;
		/** Label of the option that follows the workspace; none without it. */
		inherit?: string;
		class?: string;
	}

	let { value, onchange, stream = 'history', inherit, class: className }: Props = $props();

	const ws = getWorkspace();

	const labelOf = (v: string | null) => (v == null ? (inherit ?? '') : axisChoiceLabel(v));

	const options = $derived.by((): AutocompleteOption[] => {
		const out: AutocompleteOption[] = [];
		if (inherit) out.push({ value: inherit });
		for (const a of X_AXES)
			if (stream === 'history' || SYSTEM_X_AXES.includes(a.value)) out.push({ value: a.label });
		if (stream === 'history') {
			const names = new Set(out.map((o) => o.value));
			for (const m of ws.meta.metrics)
				if (m.stream === 'history' && !names.has(m.key)) out.push({ value: m.key, hint: 'metric' });
		}
		return out;
	});

	// What the input shows: the choice, until something is typed; back to it on leaving.
	let text = $derived(labelOf(value));

	function pick(o: AutocompleteOption) {
		const v =
			o.value === inherit ? null : (X_AXES.find((a) => a.label === o.value)?.value ?? o.value);
		text = labelOf(v);
		if (v !== value) onchange(v);
	}
</script>

<div class="contents" onfocusout={() => (text = labelOf(value))}>
	<Autocomplete
		value={text}
		{options}
		onValueChange={(v) => (text = v)}
		onPick={pick}
		placeholder="Step, time or a metric…"
		aria-label="X axis"
		class={className}
	/>
</div>
