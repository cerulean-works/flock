import { fileSafe, saveBlob } from '#lib/utils.ts';
import type { Workspace } from '#lib/workspace/workspace.svelte.ts';

/**
 * Long-format CSV (run, x, value) of every plotted run for one panel, on the x axis it plots
 * on (`step`, `runtime`, `train/global_step`...); a panel of several metrics adds the metric
 * to each line.
 */
export function downloadCsv(ws: Workspace, panel: number) {
	const page = ws.page;
	const view = ws.panels[panel];
	const xAxis = ws.axisOf(view);
	const quote = (s: string) => `"${s.replaceAll('"', '""')}"`;
	const axis = xAxis.id.startsWith('_') ? xAxis.id.slice(1) : quote(xAxis.id);
	const several = view.metrics.length > 1;
	const lines = [
		several ? `run_id,run_name,metric,${axis},value` : `run_id,run_name,${axis},value`
	];
	for (const row of ws.visible) {
		const run = page.runs[row];
		const name = quote(run.name);
		for (const m of view.metrics) {
			const series = ws.lineSeries(row, m, xAxis);
			if (!series) continue;
			const head = several
				? `${run.id},${name},${quote(ws.meta.metrics[m].key)}`
				: `${run.id},${name}`;
			const { x, y } = series;
			for (let i = 0; i < series.n; i++) lines.push(`${head},${x[i]},${y[i]}`);
		}
	}
	saveBlob(new Blob([lines.join('\n')], { type: 'text/csv' }), `${fileSafe(view.title)}.csv`);
}

export function downloadPng(canvas: HTMLCanvasElement | null, key: string) {
	canvas?.toBlob((blob) => blob && saveBlob(blob, `${fileSafe(key)}.png`), 'image/png');
}
