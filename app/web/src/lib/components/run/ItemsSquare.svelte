<script lang="ts">
	import { scheme } from '#lib/theme.svelte.ts';
	import type { ItemKeep, ItemSuites } from '#lib/types.ts';

	/**
	 * One suite of two runs as a small square scatter plot (029): its docs at A's margin across and
	 * B's up, so the zero lines split it into the diff's four cells and a dot far from the dashed
	 * diagonal is a doc the runs disagree on; docs only one run got right are in the reading blue.
	 * A quadrant under the pointer is lifted and named; a click on it picks that suite and cell.
	 */
	interface Props {
		suite: ItemSuites['suites'][number];
		/** Whether it holds the task the list shows, which it marks. */
		current: boolean;
		/** A cell to lift from outside, as when the pointer is on the list's button for it. */
		lifted: ItemKeep | null;
		/** The cells' names, as the list's keeps label them. */
		keeps: { keep: ItemKeep; label: string }[];
		/** Told the cell under the pointer, or null when it leaves. */
		onlift: (keep: ItemKeep | null) => void;
		/** Told a clicked cell. */
		onpick: (keep: ItemKeep) => void;
	}

	let { suite, current, lifted, keeps, onlift, onpick }: Props = $props();

	const SIDE = 112;
	// The cells by quadrant: A's sign picks the column, B's the row.
	const QUADRANT: Partial<Record<ItemKeep, [number, number]>> = {
		only_b: [0, 0],
		both_right: [1, 0],
		both_wrong: [0, 1],
		only_a: [1, 1]
	};
	const CELLS: ItemKeep[] = ['only_a', 'only_b', 'both_wrong', 'both_right'];

	let under = $state<ItemKeep | null>(null);
	const shown = $derived(under ?? (current ? lifted : null));

	// Both axes reach the suite's 98th percentile of absolute margins, the same either way so the
	// diagonal is at 45 degrees; a margin past it sits on the edge. Margins under a
	// length-normalized metric are small, so one fixed scale would crowd them into the middle.
	const scale = $derived.by(() => {
		const sizes = suite.dots.flatMap(([a, b]) => [Math.abs(a), Math.abs(b)]);
		sizes.sort((x, y) => x - y);
		return sizes[Math.floor((sizes.length - 1) * 0.98)] || 1;
	});

	function draw(canvas: HTMLCanvasElement) {
		$effect(() => {
			void scheme.dark;
			const css = getComputedStyle(canvas);
			const ink = css.getPropertyValue('--muted-foreground').trim();
			const rule = css.getPropertyValue('--border').trim();
			const flip = css.getPropertyValue('--primary').trim();
			const dpr = Math.min(window.devicePixelRatio || 1, 2);
			canvas.width = SIDE * dpr;
			canvas.height = SIDE * dpr;
			const ctx = canvas.getContext('2d');
			if (!ctx) return;
			ctx.scale(dpr, dpr);
			const half = SIDE / 2;

			// The lifted quadrant, washed in the reading blue under its dots.
			const quadrant = shown && QUADRANT[shown];
			if (quadrant) {
				const [col, row] = quadrant;
				ctx.fillStyle = flip;
				ctx.globalAlpha = 0.1;
				ctx.fillRect(col * half, row * half, half, half);
				ctx.globalAlpha = 1;
			}

			// The zero lines (the cells' borders) solid, the diagonal (agreement) dashed.
			ctx.strokeStyle = rule;
			ctx.lineWidth = 1;
			ctx.beginPath();
			ctx.moveTo(half, 0);
			ctx.lineTo(half, SIDE);
			ctx.moveTo(0, half);
			ctx.lineTo(SIDE, half);
			ctx.stroke();
			ctx.setLineDash([2, 2]);
			ctx.beginPath();
			ctx.moveTo(0, SIDE);
			ctx.lineTo(SIDE, 0);
			ctx.stroke();
			ctx.setLineDash([]);

			// See-through dots, so a crowd reads darker; the docs the runs split on last, on top.
			const place = (m: number) => (Math.max(-1, Math.min(1, m / scale)) + 1) / 2;
			for (const flips of [false, true]) {
				ctx.fillStyle = flips ? flip : ink;
				for (const [a, b, k] of suite.dots) {
					if (k < 2 !== flips) continue;
					ctx.globalAlpha = shown && CELLS[k] !== shown ? 0.12 : flips ? 0.75 : 0.3;
					ctx.beginPath();
					ctx.arc(place(a) * SIDE, (1 - place(b)) * SIDE, 1.25, 0, 2 * Math.PI);
					ctx.fill();
				}
			}
			ctx.globalAlpha = 1;
		});
	}

	// The cell whose quadrant a pointer event is in.
	function at(e: MouseEvent): ItemKeep {
		const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
		const right = e.clientX - box.left >= box.width / 2;
		const top = e.clientY - box.top < box.height / 2;
		return top ? (right ? 'both_right' : 'only_b') : right ? 'only_a' : 'both_wrong';
	}
	const label = (keep: ItemKeep) => keeps.find((k) => k.keep === keep)?.label ?? keep;
</script>

<figure class="grid flex-none gap-1" style:width="{SIDE}px">
	<div class="relative">
		<!-- Not reachable by keyboard: the task picker and the list's buttons do the same. -->
		<canvas
			{@attach draw}
			class={[
				'block cursor-pointer rounded-md bg-muted/40',
				current && 'ring-1 ring-ring/70 ring-offset-1 ring-offset-background'
			]}
			style:width="{SIDE}px"
			style:height="{SIDE}px"
			role="img"
			aria-label="{suite.suite}: {CELLS.map((k) => `${label(k)} ${suite.counts[k] ?? 0}`).join(
				', '
			)}"
			onmousemove={(e) => {
				const keep = at(e);
				if (keep === under) return;
				under = keep;
				onlift(keep);
			}}
			onmouseleave={() => {
				under = null;
				onlift(null);
			}}
			onclick={(e) => onpick(at(e))}
		></canvas>
		{#if under}
			<span
				class="pointer-events-none absolute inset-x-0 bottom-1 mx-auto w-fit rounded bg-popover/90 px-1 text-[11px] whitespace-nowrap tabular-nums shadow-sm"
				>{label(under)} {suite.counts[under] ?? 0}</span
			>
		{/if}
	</div>
	<figcaption
		class={[
			'truncate font-mono text-[11px]',
			current ? 'text-foreground' : 'text-muted-foreground'
		]}
		title={suite.suite}
	>
		{suite.suite}
	</figcaption>
</figure>
