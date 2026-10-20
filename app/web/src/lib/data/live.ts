import { Series, slotOf } from '#lib/chart/series.ts';
import {
	LIVE_STATES,
	type MetricDefinition,
	type MetricStream,
	type ProjectMeta,
	type RunPage
} from '#lib/types.ts';
import type { Workspace } from '#lib/workspace/workspace.svelte.ts';
import { projectUrl } from './cache.ts';

/**
 * Live updates for the page being shown, from Flock's event stream.
 *
 * The stream is opened with each run's history and system line counts from the snapshot
 * (`run.lines`, `run.systemLines`), so Flock first replays anything logged since the page
 * was rendered, then streams new points as runs log them: `history` events for logged metrics,
 * `system` events for system metrics. Events mutate the page in place (series grow, cells and states
 * change) and are coalesced into one version bump per animation frame: only panels of metrics
 * that received points repaint, and the table re-renders its visible cells once.
 *
 * Points carry their line numbers; anything at or below a run's cursor for that stream is
 * already on the page and is dropped, so replays after a reconnect never duplicate points.
 * Points of a run's x-axis mappings (`_runtime`, `_timestamp`, its step metrics) grow those
 * too, so curves keep growing on any x axis.
 *
 * Changes made elsewhere arrive too: edited attributes update the page in place; runs created,
 * moved in, deleted or moved away shift page boundaries, so they are offered as a refresh. A
 * renamed or deleted project (or a deleted org) ends the stream and is handed to `onproject`.
 */

interface HelloEvent {
	active: number;
	total: number;
	runs: Record<string, { state: string; lines: number }>;
}

/** A `history` or `system` event. */
interface PointsEvent {
	run: string;
	lines: number;
	series: Record<string, { rows: number[]; x: number[]; y: (number | null)[] }>;
}

interface RunEvent {
	run: string;
	state: string;
	lines: number;
	summary?: Record<string, unknown>;
	/** Attributes someone edited: display_name, tags, notes, group, job_type, description. */
	attributes?: Record<string, unknown>;
	/** The run's metric definitions, when they changed. */
	metric_defs?: MetricDefinition[];
}

interface CreatedEvent {
	run: string;
	state: string;
}

interface DeletedEvent {
	runs: string[];
	/** Set when the runs were moved to another project of the entity rather than deleted. */
	moved_to?: string;
}

/** The project was renamed or deleted (an org's ID never changes). The stream ends after it. */
export interface ProjectEvent {
	renamed_to?: string;
	deleted?: boolean;
}

const RETRY_MS = [500, 1000, 2000, 5000];

export class LiveConnection {
	#ws: Workspace;
	#source: EventSource | null = null;
	#page: RunPage | null = null;
	#meta: ProjectMeta | null = null;
	#retries = 0;
	#timer: ReturnType<typeof setTimeout> | undefined;
	#frame = 0;
	#dirtyMetrics = new Set<string>();
	#dirtyTable = false;
	#known = new Set<string>();
	#unseenMetrics = new Set<string>();
	#rows = new Map<string, number>();
	/** Metric index by stream and key; a system metric can share a logged metric's name. */
	#metrics: Record<MetricStream, Map<string, number>> = { history: new Map(), system: new Map() };
	#columns = new Map<string, number>();
	#onproject: (event: ProjectEvent) => void;
	/** Runs counted as gone; a run deleted after being moved away is not counted twice. */
	#removed = new Set<string>();

	constructor(ws: Workspace, onproject: (event: ProjectEvent) => void) {
		this.#ws = ws;
		this.#onproject = onproject;
	}

	/** Watch the runs of `page`; replaces any previous subscription. */
	connect(meta: ProjectMeta, page: RunPage) {
		this.close();
		this.#meta = meta;
		this.#page = page;
		this.#known = new Set(page.runs.map((r) => r.id));
		this.#unseenMetrics = new Set();
		this.#ws.newMetrics = 0;
		this.#removed = new Set();
		this.#ws.removedRuns = 0;
		this.#ws.removing.clear();
		this.#rows = new Map(page.runs.map((r, i) => [r.id, i]));
		this.#metrics = { history: new Map(), system: new Map() };
		meta.metrics.forEach((m, i) => this.#metrics[m.stream].set(m.key, i));
		this.#columns = new Map(meta.columns.map((c, i) => [c.key, i]));
		this.#open();
	}

	close() {
		clearTimeout(this.#timer);
		cancelAnimationFrame(this.#frame);
		this.#frame = 0;
		this.#flush();
		this.#source?.close();
		this.#source = null;
	}

	#open() {
		const meta = this.#meta!;
		const page = this.#page!;
		// Cursors are read now, not when the page loaded: a reconnect resumes where we are.
		const runs = page.runs.map((r) => `${r.id}:${r.lines}:${r.systemLines}`).join(',');
		const url = `${projectUrl(meta.entity, meta.project)}/live?runs=${encodeURIComponent(runs)}`;
		const source = new EventSource(url);
		this.#source = source;
		this.#ws.connection = 'connecting';

		source.addEventListener('hello', (e) => {
			this.#retries = 0;
			this.#ws.connection = 'live';
			this.#hello(JSON.parse((e as MessageEvent).data));
		});
		for (const stream of ['history', 'system'] as const) {
			source.addEventListener(stream, (e) =>
				this.#points(stream, JSON.parse((e as MessageEvent).data))
			);
		}
		source.addEventListener('run', (e) => this.#run(JSON.parse((e as MessageEvent).data)));
		source.addEventListener('created', (e) => this.#created(JSON.parse((e as MessageEvent).data)));
		source.addEventListener('deleted', (e) => this.#deleted(JSON.parse((e as MessageEvent).data)));
		source.addEventListener('project', (e) => {
			// Flock closes the stream next; it must not be reopened at the old address.
			this.close();
			this.#onproject(JSON.parse((e as MessageEvent).data));
		});
		source.onerror = () => {
			// Reconnect ourselves (rather than let EventSource retry the same URL) so the new
			// connection carries current cursors and replays only what was missed.
			source.close();
			if (this.#source !== source) return;
			this.#source = null;
			this.#ws.connection = 'offline';
			const delay = RETRY_MS[Math.min(this.#retries++, RETRY_MS.length - 1)];
			this.#timer = setTimeout(() => this.#open(), delay);
		};
	}

	#row(id: string): number {
		return this.#rows.get(id) ?? -1;
	}

	#hello(data: HelloEvent) {
		const page = this.#page!;
		this.#ws.activeRuns = data.active;
		// Runs created since this page was read (they'd shift its rows, so they're offered
		// as a refresh rather than inserted).
		this.#ws.newRuns = Math.max(0, data.total - page.projectTotal);
		for (const [id, info] of Object.entries(data.runs)) {
			const run = page.runs[this.#row(id)];
			if (run && run.state !== info.state) {
				run.state = info.state;
				this.#dirtyTable = true;
			}
		}
		this.#schedule();
	}

	#points(stream: MetricStream, data: PointsEvent) {
		const page = this.#page!;
		const r = this.#row(data.run);
		if (r < 0) return;
		const run = page.runs[r];
		const field = stream === 'history' ? 'lines' : 'systemLines';
		const cursor = run[field];
		const axes = page.axes[r]?.[stream];
		for (const [key, points] of Object.entries(data.series)) {
			// A step metric defined since the page loaded has none: its curve maps instead.
			const axis = axes?.get(key);
			if (axis) {
				let from = 0;
				while (from < points.rows.length && points.rows[from] < cursor) from++;
				if (axis.append(points.x.slice(from), points.y.slice(from))) this.#dirtyMetrics.add(key);
			}
			if (key.startsWith('_')) continue; // wandb's times: mappings only
			const m = this.#metrics[stream].get(key);
			if (m === undefined) {
				// A metric no run had logged when the page loaded: offered as a refresh.
				this.#unseenMetrics.add(`${stream}:${key}`);
				this.#ws.newMetrics = this.#unseenMetrics.size;
				continue;
			}
			let start = 0;
			while (start < points.rows.length && points.rows[start] < cursor) start++;
			if (start === points.rows.length) continue;
			const slot = slotOf(page.metricCount, r, m);
			const x = points.x.slice(start);
			const y = points.y.slice(start);
			let series = page.series[slot];
			if (!series) {
				series = page.series[slot] = new Series(new Float32Array(0), new Float64Array(0));
			}
			if (series.append(x, y)) this.#dirtyMetrics.add(key);
		}
		run[field] = Math.max(cursor, data.lines);
		this.#schedule();
	}

	#run(data: RunEvent) {
		const page = this.#page!;
		const r = this.#row(data.run);
		if (r < 0) return;
		const run = page.runs[r];
		const wasLive = LIVE_STATES.has(run.state);
		if (run.state !== data.state) {
			run.state = data.state;
			this.#dirtyTable = true;
			const isLive = LIVE_STATES.has(data.state);
			if (wasLive !== isLive) this.#ws.activeRuns += isLive ? 1 : -1;
		}
		if (data.summary) {
			for (const [key, value] of Object.entries(data.summary)) {
				const c = this.#columns.get(key);
				if (c === undefined) continue; // a new summary key; shown after a reload
				const nums = page.num[c];
				if (nums) nums[r] = typeof value === 'number' ? value : NaN;
				else if (page.text[c]) page.text[c]![r] = value;
				this.#dirtyTable = true;
			}
		}
		if (data.attributes) this.#attributes(r, data.attributes);
		if (data.metric_defs) {
			run.defs = data.metric_defs;
			this.#ws.defsVersion += 1;
		}
		this.#schedule();
	}

	/** Edited attributes: the run's label, and the cells of columns already on the page. */
	#attributes(r: number, attributes: Record<string, unknown>) {
		const page = this.#page!;
		for (const [key, value] of Object.entries(attributes)) {
			if (key === 'display_name') page.runs[r].name = (value as string | null) || page.runs[r].id;
			const c = this.#columns.get(key);
			if (c !== undefined && page.text[c]) page.text[c]![r] = value;
		}
		this.#dirtyTable = true;
	}

	#created(data: CreatedEvent) {
		if (this.#known.has(data.run)) return;
		this.#known.add(data.run);
		this.#ws.newRuns += 1;
		// Runs moved in from another project are created finished.
		if (LIVE_STATES.has(data.state)) this.#ws.activeRuns += 1;
	}

	#deleted(data: DeletedEvent) {
		for (const id of data.runs) {
			if (this.#ws.removing.has(id) || this.#removed.has(id)) continue;
			this.#removed.add(id);
			this.#ws.removedRuns += 1;
			const run = this.#page!.runs[this.#row(id)];
			if (run && LIVE_STATES.has(run.state)) this.#ws.activeRuns -= 1;
		}
	}

	#schedule() {
		if (this.#frame) return;
		this.#frame = requestAnimationFrame(() => {
			this.#frame = 0;
			this.#flush();
		});
	}

	/** Publish this frame's changes: one version bump per touched metric, one for the table. */
	#flush() {
		const ws = this.#ws;
		for (const key of this.#dirtyMetrics)
			ws.metricVersion.set(key, (ws.metricVersion.get(key) ?? 0) + 1);
		this.#dirtyMetrics.clear();
		if (this.#dirtyTable) ws.tableVersion += 1;
		this.#dirtyTable = false;
	}
}
