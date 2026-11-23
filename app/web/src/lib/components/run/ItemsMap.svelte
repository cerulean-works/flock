<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { signedMargin } from '#lib/evals.ts';
	import { scheme } from '#lib/theme.svelte.ts';
	import type { ItemKeep, ItemsMap } from '#lib/types.ts';

	/**
	 * Every doc of a task for two runs on one scatter plot (029): a dot per doc at A's margin across
	 * and B's margin up, so the zero lines split the plot into the diff's four cells, and a dot far
	 * from the diagonal is a doc the runs disagree on. Docs only one run got right stand out in the
	 * reading blue; the rest recede. A clicked dot opens its doc in the list, at its page. The list
	 * stays the way to read docs by keyboard; the plot is for the eye.
	 */
	interface Props {
		/** Run A's path under `/api/projects/`. */
		path: string;
		task: string;
		/** Run B as `entity/project/id`. */
		other: string;
		/** The cells' names, as the list's keeps label them. */
		keeps: { keep: ItemKeep; label: string }[];
		/** Told the cell, the doc's place in its default order and its id when a dot is clicked. */
		onopen: (keep: ItemKeep, index: number, id: string) => void;
	}

	let { path, task, other, keeps, onopen }: Props = $props();

	let map = $state<ItemsMap | null>(null);
	let loading = $state(false);
	let width = $state(0);
	// The doc under the pointer, and where its tooltip sits in the plot's box.
	let hover = $state<{ id: string; margins: (number | null)[]; x: number; y: number } | null>(null);

	// Refused or failed, the plot stays away: the list under it says why.
	$effect(() => {
		const asked = new URLSearchParams({ task, with: other });
		const controller = new AbortController();
		map = null;
		loading = true;
		fetch(`/api/projects/${path}/items/map?${asked}`, { signal: controller.signal })
			.then(async (res) => {
				const body = res.ok ? ((await res.json()) as ItemsMap) : null;
				if (controller.signal.aborted) return;
				map = body;
				loading = false;
			})
			.catch(() => {
				if (!controller.signal.aborted) loading = false;
			});
		return () => controller.abort();
	});

	// A fixed height, so the plot never moves the list under it as it loads or the page narrows.
	const HEIGHT = 300;
	// Room for the axes' numbers on the left and bottom.
	const PAD = { left: 40, right: 8, top: 8, bottom: 22 };
	// Both axes reach the task's 98th percentile of margins, the same either way so the diagonal is
	// at 45 degrees; a margin past it sits on the edge. Margins under a length-normalized metric
	// are small, so one fixed scale would crowd such a task into the middle.
	const scale = $derived.by(() => {
		const all = map?.cells.flatMap((c) => c.margins.flat()).filter((m) => m !== null) ?? [];
		const sizes = all.map(Math.abs).sort((x, y) => x - y);
		return sizes[Math.floor((sizes.length - 1) * 0.98)] || 1;
	});
	// Each dot's place in the plot's box, with its cell and its index in the cell's default order.
	const dots = $derived.by(() => {
		const plotW = Math.max(1, width - PAD.left - PAD.right);
		const plotH = HEIGHT - PAD.top - PAD.bottom;
		const out: { x: number; y: number; keep: ItemKeep; index: number; flip: boolean }[] = [];
		for (const cell of map?.cells ?? []) {
			cell.margins.forEach(([a, b], index) => {
				if (a === null || b === null) return; // no margin, no place on the plot
				const clamp = (m: number) => Math.max(-1, Math.min(1, m / scale));
				out.push({
					x: PAD.left + ((clamp(a) + 1) / 2) * plotW,
					y: PAD.top + ((1 - clamp(b)) / 2) * plotH,
					keep: cell.keep,
					index,
					flip: cell.keep === 'only_a' || cell.keep === 'only_b'
				});
			});
		}
		return out;
	});
	const counts = $derived(
		Object.fromEntries((map?.cells ?? []).map((c) => [c.keep, c.ids.length])) as Partial<
			Record<ItemKeep, number>
		>
	);
	const label = (keep: ItemKeep) => keeps.find((k) => k.keep === keep)?.label ?? keep;

	function draw(canvas: HTMLCanvasElement) {
		$effect(() => {
			void scheme.dark;
			const css = getComputedStyle(canvas);
			const ink = css.getPropertyValue('--muted-foreground').trim();
			const rule = css.getPropertyValue('--border').trim();
			const flip = css.getPropertyValue('--primary').trim();
			const dpr = Math.min(window.devicePixelRatio || 1, 2);
			canvas.width = Math.max(1, width) * dpr;
			canvas.height = HEIGHT * dpr;
			const ctx = canvas.getContext('2d');
			if (!ctx || !width) return;
			ctx.scale(dpr, dpr);
			const left = PAD.left;
			const right = width - PAD.right;
			const top = PAD.top;
			const bottom = HEIGHT - PAD.bottom;
			const midX = (left + right) / 2;
			const midY = (top + bottom) / 2;

			// The zero lines (the cells' borders) solid, the diagonal (agreement) dashed.
			ctx.strokeStyle = rule;
			ctx.lineWidth = 1;
			ctx.strokeRect(left + 0.5, top + 0.5, right - left - 1, bottom - top - 1);
			ctx.beginPath();
			ctx.moveTo(midX, top);
			ctx.lineTo(midX, bottom);
			ctx.moveTo(left, midY);
			ctx.lineTo(right, midY);
			ctx.stroke();
			ctx.setLineDash([3, 3]);
			ctx.beginPath();
			ctx.moveTo(left, bottom);
			ctx.lineTo(right, top);
			ctx.stroke();
			ctx.setLineDash([]);

			// Small, see-through dots, so a crowd reads darker; the docs the runs split on last, on top.
			const radius = dots.length > 2000 ? 1.5 : 2.5;
			for (const flips of [false, true]) {
				ctx.fillStyle = flips ? flip : ink;
				ctx.globalAlpha = flips ? 0.7 : dots.length > 2000 ? 0.18 : 0.35;
				for (const dot of dots) {
					if (dot.flip !== flips) continue;
					ctx.beginPath();
					ctx.arc(dot.x, dot.y, radius, 0, 2 * Math.PI);
					ctx.fill();
				}
			}
			ctx.globalAlpha = 1;

			// Each quadrant's cell and count, in its outer corner; the scale on the axes.
			ctx.fillStyle = ink;
			ctx.font = '11px ui-sans-serif, system-ui, sans-serif';
			const corner = (keep: ItemKeep, x: number, y: number, align: CanvasTextAlign) => {
				ctx.textAlign = align;
				ctx.fillText(`${label(keep)} ${counts[keep] ?? 0}`, x, y);
			};
			corner('only_b', left + 6, top + 14, 'left');
			corner('both_right', right - 6, top + 14, 'right');
			corner('both_wrong', left + 6, bottom - 6, 'left');
			corner('only_a', right - 6, bottom - 6, 'right');
			ctx.textAlign = 'center';
			ctx.fillText(`−${scale.toFixed(2)}`, left + 16, HEIGHT - 6);
			ctx.fillText('A’s margin →', midX, HEIGHT - 6);
			ctx.fillText(`+${scale.toFixed(2)}`, right - 16, HEIGHT - 6);
			ctx.textAlign = 'right';
			ctx.fillText(`+${scale.toFixed(2)}`, left - 4, top + 10);
			ctx.fillText('B ↑', left - 4, midY + 4);
			ctx.fillText(`−${scale.toFixed(2)}`, left - 4, bottom);
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
</script>

<!-- Its width is the page's, not its canvas': a canvas drawn wide must not keep the plot from
     narrowing. Its height is fixed, so it never moves what's under it. -->
<div class="relative [contain:inline-size]" bind:clientWidth={width}>
	{#if map}
		<p class="mb-1 text-xs text-muted-foreground">
			Each dot is a doc: A’s margin across, B’s up. Dots off the dashed diagonal are docs the runs
			disagree on; the blue ones only one run got right. Click one to open it.
		</p>
		<!-- Not reachable by keyboard: the list below holds the same docs, by keys. -->
		<canvas
			{@attach draw}
			class="block w-full cursor-crosshair"
			style:height="{HEIGHT}px"
			role="img"
			aria-label="{dots.length} docs: {(['only_a', 'only_b', 'both_wrong', 'both_right'] as const)
				.map((k) => `${label(k)} ${counts[k] ?? 0}`)
				.join(', ')}"
			onmousemove={(e) => {
				const dot = at(e);
				const cell = dot && map?.cells.find((c) => c.keep === dot.keep);
				hover =
					dot && cell
						? {
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
				const cell = dot && map?.cells.find((c) => c.keep === dot.keep);
				if (dot && cell) onopen(dot.keep, dot.index, cell.ids[dot.index]);
			}}
		></canvas>
		{#if hover}
			<div
				class="pointer-events-none absolute z-10 rounded-md border bg-popover px-2 py-1 font-mono text-[11px] whitespace-nowrap shadow-sm"
				style:left="{Math.min(hover.x + 12, width - 160 /* the tooltip's widest */)}px"
				style:top="{hover.y + 30}px"
			>
				<div>{hover.id}</div>
				<div class="text-muted-foreground">
					A {signedMargin(hover.margins[0])} · B {signedMargin(hover.margins[1])}
				</div>
			</div>
		{/if}
	{:else if loading}
		<p class="flex items-center gap-2 text-xs text-muted-foreground">
			<LoaderCircleIcon class="size-3.5 animate-spin" /> Drawing every doc…
		</p>
	{/if}
</div>
