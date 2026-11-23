<script lang="ts">
	import { signedMargin } from '#lib/evals.ts';
	import { scheme } from '#lib/theme.svelte.ts';
	import type { ItemKeep, ItemsMap } from '#lib/types.ts';

	/**
	 * One suite's docs for two runs on a square scatter plot (029): a dot per doc at A's margin
	 * across and B's margin up, so the zero lines split it into the diff's four cells and a dot far
	 * from the dashed diagonal is a doc the runs disagree on. Docs only one run got right are drawn
	 * in the reading blue, the rest recede. A clicked dot opens its doc in the list.
	 */
	interface Props {
		map: ItemsMap;
		/** Whether it holds the task the list shows, which it marks. */
		current: boolean;
		/** Told a clicked dot's task, cell, place in the cell's default order and id. */
		onopen: (task: string, keep: ItemKeep, index: number, id: string) => void;
	}

	let { map, current, onopen }: Props = $props();

	let side = $state(0);
	// The doc under the pointer, and where its tooltip sits in the square.
	let hover = $state<{
		task: string;
		id: string;
		margins: (number | null)[];
		x: number;
		y: number;
	} | null>(null);

	// Both axes reach the suite's 98th percentile of absolute margins, the same either way so the
	// diagonal is at 45 degrees; a margin past it sits on the edge. Margins under a
	// length-normalized metric are small, so one fixed scale would crowd them into the middle.
	const scale = $derived.by(() => {
		const sizes: number[] = [];
		for (const task of map.tasks)
			for (const cell of task.cells)
				for (const pair of cell.margins)
					for (const m of pair) if (m !== null) sizes.push(Math.abs(m));
		sizes.sort((x, y) => x - y);
		return sizes[Math.floor((sizes.length - 1) * 0.98)] || 1;
	});
	// Each dot's place in the square, with what a click on it opens.
	const dots = $derived.by(() => {
		const out: {
			x: number;
			y: number;
			task: string;
			keep: ItemKeep;
			index: number;
			flip: boolean;
		}[] = [];
		const place = (m: number) => (Math.max(-1, Math.min(1, m / scale)) + 1) / 2;
		for (const task of map.tasks)
			for (const cell of task.cells)
				cell.margins.forEach(([a, b], index) => {
					if (a === null || b === null) return; // no margin, no place on the plot
					out.push({
						x: place(a) * side,
						y: (1 - place(b)) * side,
						task: task.task,
						keep: cell.keep,
						index,
						flip: cell.keep === 'only_a' || cell.keep === 'only_b'
					});
				});
		return out;
	});
	const counts = $derived.by(() => {
		const out: Record<string, number> = {};
		for (const task of map.tasks)
			for (const cell of task.cells) out[cell.keep] = (out[cell.keep] ?? 0) + cell.ids.length;
		return out;
	});

	function draw(canvas: HTMLCanvasElement) {
		$effect(() => {
			void scheme.dark;
			const css = getComputedStyle(canvas);
			const ink = css.getPropertyValue('--muted-foreground').trim();
			const rule = css.getPropertyValue('--border').trim();
			const flip = css.getPropertyValue('--primary').trim();
			const dpr = Math.min(window.devicePixelRatio || 1, 2);
			canvas.width = Math.max(1, side) * dpr;
			canvas.height = Math.max(1, side) * dpr;
			const ctx = canvas.getContext('2d');
			if (!ctx || !side) return;
			ctx.scale(dpr, dpr);

			// The zero lines (the cells' borders) solid, the diagonal (agreement) dashed.
			ctx.strokeStyle = rule;
			ctx.lineWidth = 1;
			ctx.beginPath();
			ctx.moveTo(side / 2, 0);
			ctx.lineTo(side / 2, side);
			ctx.moveTo(0, side / 2);
			ctx.lineTo(side, side / 2);
			ctx.stroke();
			ctx.setLineDash([3, 3]);
			ctx.beginPath();
			ctx.moveTo(0, side);
			ctx.lineTo(side, 0);
			ctx.stroke();
			ctx.setLineDash([]);

			// Small, see-through dots, so a crowd reads darker; the docs the runs split on last.
			const radius = dots.length > 2000 ? 1.25 : 2;
			for (const flips of [false, true]) {
				ctx.fillStyle = flips ? flip : ink;
				ctx.globalAlpha = flips ? 0.7 : dots.length > 2000 ? 0.15 : 0.3;
				for (const dot of dots) {
					if (dot.flip !== flips) continue;
					ctx.beginPath();
					ctx.arc(dot.x, dot.y, radius, 0, 2 * Math.PI);
					ctx.fill();
				}
			}
			ctx.globalAlpha = 1;
		});
	}

	// The dot nearest a pointer event, within 6 px.
	function at(e: MouseEvent) {
		const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
		const x = e.clientX - box.left;
		const y = e.clientY - box.top;
		let best = null;
		let near = 36; // 6 px, squared
		for (const dot of dots) {
			const d = (dot.x - x) ** 2 + (dot.y - y) ** 2;
			if (d <= near) {
				near = d;
				best = dot;
			}
		}
		return best;
	}

	const docOf = (dot: { task: string; keep: ItemKeep; index: number }) =>
		map.tasks.find((t) => t.task === dot.task)?.cells.find((c) => c.keep === dot.keep);
</script>

<figure class="grid min-w-0 gap-1">
	<figcaption class="flex items-baseline justify-between gap-2 text-xs">
		<span class={['truncate font-mono', current ? 'text-foreground' : 'text-muted-foreground']}
			>{map.suite}</span
		>
		<span class="text-[11px] whitespace-nowrap text-muted-foreground tabular-nums">
			A only {counts.only_a ?? 0} · B only {counts.only_b ?? 0}
		</span>
	</figcaption>
	<div
		class={['relative aspect-square rounded-md bg-muted/40', current && 'ring-1 ring-ring/60']}
		bind:clientWidth={side}
	>
		<!-- Not reachable by keyboard: the list below holds the same docs, by keys. -->
		<canvas
			{@attach draw}
			class="absolute inset-0 size-full cursor-crosshair"
			role="img"
			aria-label="{map.suite}: {dots.length} docs, {counts.only_a ??
				0} only A got right, {counts.only_b ?? 0} only B, {counts.both_wrong ??
				0} both wrong, {counts.both_right ?? 0} both right"
			onmousemove={(e) => {
				const dot = at(e);
				const cell = dot && docOf(dot);
				hover =
					dot && cell
						? {
								task: dot.task,
								id: cell.ids[dot.index],
								margins: cell.margins[dot.index],
								x: dot.x,
								y: dot.y
							}
						: null;
			}}
			onmouseleave={() => (hover = null)}
			onclick={(e) => {
				const dot = at(e);
				const cell = dot && docOf(dot);
				if (dot && cell) onopen(dot.task, dot.keep, dot.index, cell.ids[dot.index]);
			}}
		></canvas>
		{#if hover}
			<div
				class="pointer-events-none absolute z-10 rounded-md border bg-popover px-2 py-1 font-mono text-[11px] whitespace-nowrap shadow-sm"
				style:left="{Math.min(hover.x + 10, side - 150 /* the tooltip's widest */)}px"
				style:top="{hover.y + 12}px"
			>
				<div>{hover.task} · {hover.id}</div>
				<div class="text-muted-foreground">
					A {signedMargin(hover.margins[0])} · B {signedMargin(hover.margins[1])}
				</div>
			</div>
		{/if}
	</div>
</figure>
