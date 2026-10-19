import { fileSafe, saveBlob } from '#lib/utils.ts';
import type { Workspace } from '#lib/workspace/workspace.svelte.ts';
import { slotOf } from './series.ts';

/** Long-format CSV (run, step, value) of every plotted run for one metric. */
export function downloadCsv(ws: Workspace, metric: number) {
	const page = ws.page;
	const key = ws.meta.metrics[metric].key;
	const axis = ws.meta.metrics[metric].stream === 'system' ? 'runtime' : 'step';
	const lines = [`run_id,run_name,${axis},value`];
	for (const row of ws.visible) {
		const run = page.runs[row];
		const series = page.series[slotOf(page.metricCount, row, metric)];
		if (!series) continue;
		const name = `"${run.name.replaceAll('"', '""')}"`;
		const { x, y } = series;
		for (let i = 0; i < series.n; i++) lines.push(`${run.id},${name},${x[i]},${y[i]}`);
	}
	saveBlob(new Blob([lines.join('\n')], { type: 'text/csv' }), `${fileSafe(key)}.csv`);
}

export function downloadPng(canvas: HTMLCanvasElement | null, key: string) {
	canvas?.toBlob((blob) => blob && saveBlob(blob, `${fileSafe(key)}.png`), 'image/png');
}
