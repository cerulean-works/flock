<script lang="ts">
	import { signedMargin } from '#lib/evals.ts';
	import { scheme } from '#lib/theme.svelte.ts';
	import type { ItemKeep, ItemSuites } from '#lib/types.ts';

	/**
	 * One suite of two runs as a small square scatter plot (029): its docs at A's margin across and
	 * B's up, so the zero lines split it into the diff's four cells and a dot far from the dashed
	 * diagonal is a doc the runs disagree on; docs only one run got right are in the reading blue.
	 * A quadrant under the pointer is lifted and named, and a click on it picks that suite and cell;
	 * a dot under the pointer names its doc, and a click on it opens that doc.
	 */
	interface Props {
		suite: ItemSuites['suites'][number];
		/** Its width and height in px. */
		side: number;
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
		/** Told a clicked dot's task, cell, place in the cell's default order and id. */
		onopen: (task: string, keep: ItemKeep, index: number, id: string) => void;
	}

	let { suite, side, current, lifted, keeps, onlift, onpick, onopen }: Props = $props();

	// The cells by quadrant: A's sign picks the column, B's the row.
	const QUADRANT: Partial<Record<ItemKeep, [number, number]>> = {
		only_b: [0, 0],
		both_right: [1, 0],
		both_wrong: [0, 1],
		only_a: [1, 1]
	};
	const CELLS: ItemKeep[] = ['only_a', 'only_b', 'both_wrong', 'both_right'];

	let under = $state<ItemKeep | null>(null);
	// The dot under the pointer, an index into `suite.dots`.
	let near = $state<number | null>(null);
	// Where the pointer is in the window, which the doc's tooltip follows.
	let pointer = $state({ x: 0, y: 0 });
	const shown = $derived(under ?? (current ? lifted : null));

	// Both axes reach the suite's 98th percentile of absolute margins, the same either way so the
	// diagonal is at 45 degrees; a margin past it sits on the edge. Margins under a
	// length-normalized metric are small, so one fixed scale would crowd them into the middle.
	const scale = $derived.by(() => {
		const sizes = suite.dots.flatMap(([a, b]) => [Math.abs(a), Math.abs(b)]);
		sizes.sort((x, y) => x - y);
		return sizes[Math.floor((sizes.length - 1) * 0.98)] || 1;
	});
	// A margin's place along an axis, from 0 to 1.
	const place = $derived((m: number) => (Math.max(-1, Math.min(1, m / scale)) + 1) / 2);

	function draw(canvas: HTMLCanvasElement) {
		$effect(() => {
			void scheme.dark;
			const css = getComputedStyle(canvas);
			const ink = css.getPropertyValue('--muted-foreground').trim();
			const rule = css.getPropertyValue('--border').trim();
			const flip = css.getPropertyValue('--primary').trim();
			const dpr = Math.min(window.devicePixelRatio || 1, 2);
			canvas.width = side * dpr;
			canvas.height = side * dpr;
			const ctx = canvas.getContext('2d');
			if (!ctx) return;
			ctx.scale(dpr, dpr);
			const half = side / 2;

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
			ctx.lineTo(half, side);
			ctx.moveTo(0, half);
			ctx.lineTo(side, half);
			ctx.stroke();
			ctx.setLineDash([2, 2]);
			ctx.beginPath();
			ctx.moveTo(0, side);
			ctx.lineTo(side, 0);
			ctx.stroke();
			ctx.setLineDash([]);

			// See-through dots, so a crowd reads darker; the docs the runs split on last, on top.
			for (const flips of [false, true]) {
				ctx.fillStyle = flips ? flip : ink;
				for (const [a, b, k] of suite.dots) {
					if (k < 2 !== flips) continue;
					ctx.globalAlpha = shown && CELLS[k] !== shown ? 0.12 : flips ? 0.75 : 0.3;
					ctx.beginPath();
					ctx.arc(place(a) * side, (1 - place(b)) * side, 1.25, 0, 2 * Math.PI);
					ctx.fill();
				}
			}
			ctx.globalAlpha = 1;

			// The dot under the pointer, ringed.
			if (near !== null) {
				const [a, b] = suite.dots[near];
				ctx.strokeStyle = css.getPropertyValue('--foreground').trim();
				ctx.lineWidth = 1.5;
				ctx.beginPath();
				ctx.arc(place(a) * side, (1 - place(b)) * side, 3.5, 0, 2 * Math.PI);
				ctx.stroke();
			}
		});
	}

	// The dot nearest a pointer event, within 4 px, or null.
	function dotAt(e: MouseEvent): number | null {
		const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
		const x = e.clientX - box.left;
		const y = e.clientY - box.top;
		let best = null;
		let closest = 16; // 4 px, squared
		suite.dots.forEach(([a, b], i) => {
			const d = (place(a) * side - x) ** 2 + ((1 - place(b)) * side - y) ** 2;
			if (d <= closest) {
				closest = d;
				best = i;
			}
		});
		return best;
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

<figure class="grid flex-none snap-start gap-1" style:width="{side}px">
	<div class="relative">
		<!-- Not reachable by keyboard: the task picker and the list's buttons do the same. -->
		<canvas
			{@attach draw}
			class={[
				'block cursor-pointer rounded-md bg-muted/40',
				current && 'ring-1 ring-ring/70 ring-offset-1 ring-offset-background'
			]}
			style:width="{side}px"
			style:height="{side}px"
			role="img"
			aria-label="{suite.suite}: {CELLS.map((k) => `${label(k)} ${suite.counts[k] ?? 0}`).join(
				', '
			)}"
			onmousemove={(e) => {
				near = dotAt(e);
				pointer = { x: e.clientX, y: e.clientY };
				const keep = at(e);
				if (keep === under) return;
				under = keep;
				onlift(keep);
			}}
			onmouseleave={() => {
				under = null;
				near = null;
				onlift(null);
			}}
			onclick={(e) => {
				const dot = dotAt(e);
				if (dot === null) return onpick(at(e));
				const [, , k, t, i, id] = suite.dots[dot];
				onopen(suite.tasks[t], CELLS[k], i, id);
			}}
		></canvas>
		{#if near !== null}
			{@const [a, b, , t, , id] = suite.dots[near]}
			<!-- The doc under the pointer, its bottom corner just off the pointer: up and to the right,
			     or to the left near the window's right edge. Fixed, so the scrolling row can't clip it. -->
			{@const flip = pointer.x > window.innerWidth - 260}
			<span
				class="pointer-events-none fixed z-50 grid max-w-64 -translate-y-full rounded-md border bg-popover px-2 py-1 font-mono text-[11px] leading-snug shadow-md"
				style:left={flip ? null : `${pointer.x + 10}px`}
				style:right={flip ? `${window.innerWidth - pointer.x + 10}px` : null}
				style:top="{pointer.y - 10}px"
			>
				<span class="truncate">{suite.tasks[t]} · {id}</span>
				<span class="text-muted-foreground tabular-nums"
					>A {signedMargin(a)} · B {signedMargin(b)}</span
				>
			</span>
		{:else if under}
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
