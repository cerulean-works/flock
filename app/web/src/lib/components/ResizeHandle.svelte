<script lang="ts">
	import { clampWidth, type Width } from '#lib/widths.ts';

	interface Props {
		/** The width this handle resizes; it sits on that box's right edge. */
		value: number;
		bounds: Width;
		label: string;
		/** Every change while dragging or pressing arrow keys. */
		onresize: (px: number) => void;
		/** Once the width settles: dropped, a key let go, or reset. */
		oncommit: (px: number) => void;
		class?: string;
	}

	let { value, bounds, label, onresize, oncommit, class: className }: Props = $props();

	let dragging = $state(false);

	function onpointerdown(e: PointerEvent) {
		if (e.button !== 0) return;
		e.preventDefault();
		const el = e.currentTarget as HTMLElement;
		el.setPointerCapture(e.pointerId);
		const x0 = e.clientX;
		const w0 = value;
		let last = w0;
		dragging = true;
		const move = (ev: PointerEvent) => {
			last = clampWidth(bounds, w0 + ev.clientX - x0);
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
		const by = { ArrowLeft: -16, ArrowRight: 16 }[e.key];
		if (!by) return;
		e.preventDefault();
		onresize(clampWidth(bounds, value + by));
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
	aria-orientation="vertical"
	aria-label={label}
	aria-valuenow={value}
	aria-valuemin={bounds.min}
	aria-valuemax={bounds.max}
	tabindex="0"
	title="Drag to resize · double-click to reset"
	class={[
		'group/resize absolute inset-y-0 -right-[4px] z-10 w-[7px] cursor-col-resize touch-none outline-none',
		className
	]}
	{onpointerdown}
	{onkeydown}
	onkeyup={(e) => (e.key === 'ArrowLeft' || e.key === 'ArrowRight') && oncommit(value)}
	ondblclick={reset}
>
	<div
		class={[
			'absolute inset-y-0 left-[3px] w-px transition-colors',
			dragging
				? 'bg-ring'
				: 'bg-transparent group-hover/resize:bg-border group-focus-visible/resize:bg-ring'
		]}
	></div>
</div>
