<script lang="ts">
	import { Label } from '#lib/components/ui/label/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import { Slider } from '#lib/components/ui/slider/index.js';
	import {
		NO_SMOOTHING,
		SMOOTHING_METHODS,
		SMOOTHING_TYPES,
		clampSmoothing,
		type Smoothing,
		type SmoothingType
	} from '#lib/chart/series.ts';

	/**
	 * W&B's smoothing settings: a method, and a slider for its parameter. With `inherit`, its
	 * first option follows the workspace (a null value).
	 */

	interface Props {
		value: Smoothing | null;
		onchange: (value: Smoothing | null) => void;
		/** The smoothing a null value follows; no such option without it. */
		inherit?: Smoothing;
		label?: string;
		id?: string;
	}

	let { value, onchange, inherit, label = 'Method', id = 'smoothing' }: Props = $props();

	const inherits = $derived(value == null && !!inherit);
	const shown = $derived(value ?? inherit ?? NO_SMOOTHING);
	const method = $derived(SMOOTHING_METHODS[shown.type]);
	const inheritLabel = $derived(
		inherit ? `Workspace (${SMOOTHING_METHODS[inherit.type].label})` : ''
	);

	const format = (s: Smoothing) =>
		s.type === 'average' ? String(s.param) : s.param.toFixed(s.type === 'gaussian' ? 1 : 2);

	function pick(v: string) {
		if (v === 'inherit') return onchange(null);
		const type = v as SmoothingType;
		onchange({ type, param: type === shown.type ? shown.param : SMOOTHING_METHODS[type].start });
	}
</script>

<div class="grid gap-3">
	<div class="grid gap-2">
		<Label for={id}>{label}</Label>
		<Select.Root type="single" value={inherits ? 'inherit' : shown.type} onValueChange={pick}>
			<Select.Trigger {id} size="sm" class="w-full">
				{inherits ? inheritLabel : method.label}
			</Select.Trigger>
			<Select.Content>
				{#if inherit}
					<Select.Item value="inherit">{inheritLabel}</Select.Item>
				{/if}
				{#each SMOOTHING_TYPES as type (type)}
					<Select.Item value={type}>{SMOOTHING_METHODS[type].label}</Select.Item>
				{/each}
			</Select.Content>
		</Select.Root>
	</div>
	{#if shown.type !== 'none'}
		<div class="grid gap-2">
			<div class="flex items-center justify-between gap-4 text-sm">
				<span class={['text-muted-foreground', inherits && 'opacity-50']}>{method.param}</span>
				<output class="text-muted-foreground tabular-nums">{format(shown)}</output>
			</div>
			<Slider
				type="single"
				value={shown.param}
				onValueChange={(v) => onchange({ type: shown.type, param: clampSmoothing(shown.type, v) })}
				min={method.min}
				max={method.max}
				step={method.step}
				disabled={inherits}
				aria-label={method.param}
			/>
		</div>
	{/if}
</div>
