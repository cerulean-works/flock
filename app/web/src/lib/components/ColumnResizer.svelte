<script lang="ts">
	interface Props {
		/** The column's width now, in px. */
		width: number;
		/** Each new width while the edge is dragged; never under 56 px. */
		onresize: (width: number) => void;
	}

	let { width, onresize }: Props = $props();

	function start(e: PointerEvent) {
		e.preventDefault();
		e.stopPropagation();
		const el = e.currentTarget as HTMLElement;
		el.setPointerCapture(e.pointerId);
		const x0 = e.clientX;
		const w0 = width;
		const move = (ev: PointerEvent) => onresize(Math.max(56, Math.round(w0 + ev.clientX - x0)));
		const up = () => {
			el.removeEventListener('pointermove', move);
			el.removeEventListener('pointerup', up);
		};
		el.addEventListener('pointermove', move);
		el.addEventListener('pointerup', up);
	}
</script>

<!-- A column's right edge, in a header cell marked `relative group/h`: drag it to resize. -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	class="absolute top-2 -right-[3px] bottom-2 z-1 w-[7px] cursor-col-resize after:absolute after:inset-y-0 after:left-[3px] after:w-px after:bg-divider group-hover/h:after:bg-border"
	onpointerdown={start}
></div>
