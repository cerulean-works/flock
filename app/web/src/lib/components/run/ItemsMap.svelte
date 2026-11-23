<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { scheme } from '#lib/theme.svelte.ts';
	import type { ItemKeep, ItemsMap } from '#lib/types.ts';

	/**
	 * Every doc of a task for two runs, a canvas per cell of their diff (029): one mark per doc,
	 * A's margin on top and B's below, tinted by sign as the list tints margins, as strong as the
	 * task's 90th percentile of margins. Each cell is in the list's default order, so a clicked mark
	 * lands on its row. The list stays the way to read docs by keyboard; the map is for the eye.
	 */
	interface Props {
		/** Run A's path under `/api/projects/`. */
		path: string;
		task: string;
		/** Run B as `entity/project/id`. */
		other: string;
		/** The cells' names, as the list's keeps label them. */
		keeps: { keep: ItemKeep; label: string }[];
		/** Told the cell, the doc's place in its default order and its id when a mark is clicked. */
		onopen: (keep: ItemKeep, index: number, id: string) => void;
	}

	let { path, task, other, keeps, onopen }: Props = $props();

	let map = $state<ItemsMap | null>(null);
	let loading = $state(false);
	let width = $state(0);
	let root = $state<HTMLElement>();
	// The doc under the pointer, and where its tooltip sits in the map's box.
	let hover = $state<{ id: string; margins: (number | null)[]; x: number; y: number } | null>(null);

	// Refused or failed, the map stays away: the list under it says why.
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

	const docs = $derived(map?.cells.reduce((n, c) => n + c.ids.length, 0) ?? 0);
	// Small marks for a large task, so HellaSwag's 10,042 docs fit a few screens' worth of rows.
	const pitch = $derived(docs > 2000 ? 5 : 9);
	const perRow = $derived(Math.max(1, Math.floor(width / pitch)));
	// Full tint at the task's 90th percentile: margins under a length-normalized metric are small,
	// so one fixed scale would wash such a task out.
	const scale = $derived.by(() => {
		const all = map?.cells.flatMap((c) => c.margins.flat()).filter((m) => m !== null) ?? [];
		const sizes = all.map(Math.abs).sort((x, y) => x - y);
		return sizes[Math.floor(sizes.length * 0.9)] || 1;
	});
	const signed = (m: number | null) =>
		m === null ? '–' : `${m > 0 ? '+' : m < 0 ? '−' : ''}${Math.abs(m).toFixed(2)}`;

	// Draws one cell's marks: the muted ground, then each run's tint over it.
	function draw(canvas: HTMLCanvasElement, cell: ItemsMap['cells'][number]) {
		$effect(() => {
			void scheme.dark;
			const css = getComputedStyle(canvas);
			const ground = css.getPropertyValue('--muted').trim();
			const right = css.getPropertyValue('--state-finished').trim();
			const wrong = css.getPropertyValue('--destructive').trim();
			const dpr = Math.min(window.devicePixelRatio || 1, 2);
			const rows = Math.ceil(cell.ids.length / perRow);
			canvas.width = perRow * pitch * dpr;
			canvas.height = rows * 2 * pitch * dpr;
			canvas.style.width = `${perRow * pitch}px`;
			canvas.style.height = `${rows * 2 * pitch}px`;
			const ctx = canvas.getContext('2d');
			if (!ctx) return;
			ctx.scale(dpr, dpr);
			// A mark is pitch - 1 wide with a 1 px gap; each run's half is pitch - 1 tall, and a
			// gap of 2 separates one row of marks from the next.
			const side = pitch - 1;
			cell.margins.forEach((margins, i) => {
				const x = (i % perRow) * pitch;
				const y = Math.floor(i / perRow) * 2 * pitch;
				margins.forEach((m, run) => {
					ctx.globalAlpha = 1;
					ctx.fillStyle = ground;
					ctx.fillRect(x, y + run * (side - 1), side, side - 1);
					if (m === null) return;
					ctx.globalAlpha = Math.min(Math.abs(m) / scale, 1);
					ctx.fillStyle = m >= 0 ? right : wrong;
					ctx.fillRect(x, y + run * (side - 1), side, side - 1);
				});
			});
		});
	}

	// The doc under a pointer event on a cell's canvas, with its place in the cell.
	function at(e: MouseEvent, cell: ItemsMap['cells'][number]) {
		const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
		const column = Math.floor((e.clientX - box.left) / pitch);
		const index = Math.floor((e.clientY - box.top) / (2 * pitch)) * perRow + column;
		return column < perRow && index < cell.ids.length ? index : null;
	}
</script>

<!-- Its width is the page's, not its canvases': a canvas drawn wide must not keep the map from
     narrowing, or the marks would never wrap again. -->
<div class="relative grid gap-2 [contain:inline-size]" bind:clientWidth={width} bind:this={root}>
	{#if map}
		<p class="flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
			<span>Every doc: A’s margin on top, B’s below; click one to open it.</span>
			<span class="flex items-center gap-1" aria-hidden="true">
				<span class="size-2.5 rounded-sm bg-destructive"></span>−{scale.toFixed(2)}
				<span class="size-2.5 rounded-sm bg-muted"></span>0
				<span class="size-2.5 rounded-sm bg-state-finished"></span>+{scale.toFixed(2)}
			</span>
		</p>
		{#each map.cells as cell (cell.keep)}
			{#if cell.ids.length}
				{@const label = keeps.find((k) => k.keep === cell.keep)?.label ?? cell.keep}
				<div>
					<p class="mb-0.5 text-xs">
						{label}
						<span class="text-muted-foreground tabular-nums">{cell.ids.length}</span>
					</p>
					<!-- Not reachable by keyboard: the list below holds the same docs, by keys. -->
					<canvas
						{@attach (c) => draw(c, cell)}
						class="block cursor-pointer"
						role="img"
						aria-label="{label}: {cell.ids.length} docs"
						onmousemove={(e) => {
							const i = at(e, cell);
							const box = root?.getBoundingClientRect();
							hover =
								i === null || !box
									? null
									: {
											id: cell.ids[i],
											margins: cell.margins[i],
											x: e.clientX - box.left,
											y: e.clientY - box.top
										};
						}}
						onmouseleave={() => (hover = null)}
						onclick={(e) => {
							const i = at(e, cell);
							if (i !== null) onopen(cell.keep, i, cell.ids[i]);
						}}
					></canvas>
				</div>
			{/if}
		{/each}
		{#if hover}
			<div
				class="pointer-events-none absolute z-10 rounded-md border bg-popover px-2 py-1 font-mono text-[11px] whitespace-nowrap shadow-sm"
				style:left="{Math.min(hover.x + 12, width - 160)}px"
				style:top="{hover.y + 14}px"
			>
				<div>{hover.id}</div>
				<div class="text-muted-foreground">
					A {signed(hover.margins[0])} · B {signed(hover.margins[1])}
				</div>
			</div>
		{/if}
	{:else if loading}
		<p class="flex items-center gap-2 text-xs text-muted-foreground">
			<LoaderCircleIcon class="size-3.5 animate-spin" /> Drawing every doc…
		</p>
	{/if}
</div>
