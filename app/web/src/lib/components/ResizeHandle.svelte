<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import { clampWidth, type Width } from '#lib/widths.ts';

	interface Props {
		/** The width this handle resizes; it sits on that box's right edge, or its left one. */
		value: number;
		/** The box's edge it sits on: on the left, dragging left widens the box. */
		edge?: 'left' | 'right';
		/** `y`: the handle sits on the box's bottom edge and resizes its height. */
		axis?: 'x' | 'y';
		bounds: Width;
		label: string;
		/** Every change while dragging or pressing arrow keys. */
		onresize: (px: number) => void;
		/** Once the width settles: dropped, a key let go, or reset. */
		oncommit: (px: number) => void;
		class?: string;
	}

	let {
		value,
		edge = 'right',
		axis = 'x',
		bounds,
		label,
		onresize,
		oncommit,
		class: className
	}: Props = $props();

	const sign = $derived(edge === 'left' ? -1 : 1);

	let dragging = $state(false);

	function onpointerdown(e: PointerEvent) {
		if (e.button !== 0) return;
		e.preventDefault();
		const el = e.currentTarget as HTMLElement;
		el.setPointerCapture(e.pointerId);
		const at = (ev: PointerEvent) => (axis === 'x' ? ev.clientX : ev.clientY);
		const p0 = at(e);
		const w0 = value;
		let last = w0;
		dragging = true;
		const move = (ev: PointerEvent) => {
			last = clampWidth(bounds, w0 + sign * (at(ev) - p0));
			onresize(last);
		};
		const up = () => {
			dragging = false;
			el.removeEventListener('pointermove', move);
			el.removeEventListener('pointerup', up);
			el.removeEventListener('pointercancel', up);
			if (last !== w0) oncommit(last);
		};
		el.addEventListener('pointermove', move);
		el.addEventListener('pointerup', up);
		el.addEventListener('pointercancel', up);
	}

	function onkeydown(e: KeyboardEvent) {
		const steps: Record<string, number> =
			axis === 'x' ? { ArrowLeft: -16, ArrowRight: 16 } : { ArrowUp: -16, ArrowDown: 16 };
		const by = steps[e.key];
		if (!by) return;
		e.preventDefault();
		onresize(clampWidth(bounds, value + sign * by));
	}

	function reset() {
		onresize(bounds.initial);
		oncommit(bounds.initial);
	}
</script>

<!-- Straddles the edge: a 7px target with a 1px line that shows on hover or while dragging.
     A focusable separator with a value is a widget in ARIA, which the a11y checks don't know. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
<div
	role="separator"
	aria-orientation={axis === 'x' ? 'vertical' : 'horizontal'}
	aria-label={label}
	aria-valuenow={value}
	aria-valuemin={bounds.min}
	aria-valuemax={bounds.max}
	tabindex="0"
	{@attach hint('Drag to resize, or double-click to reset')}
	class={[
		'group/resize absolute z-10 touch-none outline-none',
		axis === 'y'
			? 'inset-x-0 -bottom-[4px] h-[7px] cursor-row-resize'
			: ['inset-y-0 w-[7px] cursor-col-resize', edge === 'left' ? '-left-[4px]' : '-right-[4px]'],
		className
	]}
	{onpointerdown}
	{onkeydown}
	onkeyup={(e) => e.key.startsWith('Arrow') && oncommit(value)}
	ondblclick={reset}
>
	<div
		class={[
			'absolute transition-colors',
			axis === 'y' ? 'inset-x-0 top-[3px] h-px' : 'inset-y-0 left-[3px] w-px',
			dragging
				? 'bg-ring'
				: 'bg-transparent group-hover/resize:bg-border group-focus-visible/resize:bg-ring'
		]}
	></div>
</div>
