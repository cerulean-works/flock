import type { Series } from './chart/series.ts';

/** Flock run states; `running` and `preempting` are live. */
export type RunState = 'running' | 'preempting' | 'finished' | 'failed' | 'crashed';

export const LIVE_STATES: ReadonlySet<string> = new Set(['running', 'preempting']);

/** A project, as Flock lists it. Times are seconds since the epoch. */
export interface ProjectInfo {
	entity: string;
	name: string;
	description: string | null;
	created_at: number;
	updated_at: number;
	run_count: number;
	active_run_count: number;
	column_count: number;
}

export type Role = 'admin' | 'member';

/** An organization (a wandb entity), with the caller's role in it. */
export interface OrgInfo {
	name: string;
	created_at: number;
	updated_at: number;
	project_count: number;
	member_count: number;
	/** A path on this dashboard (`/api/v1/avatars/…`), or null without a picture. */
	avatar_url: string | null;
	/** Super admins are admins of every organization, member or not. */
	role: Role | null;
}

/** An account, as Flock describes it to its owner and to super admins. */
export interface UserInfo {
	id: number;
	email: string;
	name: string;
	is_superadmin: boolean;
	has_password: boolean;
	/** Signs in with Google. */
	google: boolean;
	default_org: string | null;
	orgs: { name: string; role: Role }[];
	created_at: number;
	avatar_url: string | null;
	/** Where the picture came from (only `/me` says). */
	avatar_source?: 'gravatar' | 'upload' | null;
}

export interface Member {
	id: number;
	email: string;
	name: string;
	role: Role;
	avatar_url: string | null;
}

/** An invitation not yet accepted. Its link is shown only when it is created. */
export interface Invite {
	id: number;
	email: string;
	role: Role;
	/** The inviting user's id. */
	created_by: number;
	created_at: number;
	expires_at: number;
}

/** A person as feeds and budgets name them. */
export interface Person {
	id: number;
	name: string;
	email: string;
	avatar_url: string | null;
}

export type FeedKind =
	'run_started' | 'run_finished' | 'run_failed' | 'run_crashed' | 'project_created';

/** A run as a feed item carries it. */
export interface FeedRun {
	id: string;
	display_name: string | null;
	state: string;
	gpu_type: string | null;
	gpu_count: number | null;
	/** Seconds the run was alive. */
	runtime: number;
	tags: string[];
	group: string | null;
	job_type: string | null;
}

/** One entry of the home feed, newest first. `at` is in seconds since the epoch. */
export interface FeedItem {
	id: string;
	kind: FeedKind;
	at: number;
	org: string;
	project: string;
	/** The run's owner; null for runs from before accounts, and for projects. */
	actor: Person | null;
	run?: FeedRun;
	description?: string | null;
}

export interface FeedPage {
	items: FeedItem[];
	/** The cursor of the next page; null on the last. */
	next: string | null;
}

/** An org's spend for one UTC month (`YYYY-MM`), priced at its current GPU rates. */
export interface Budget {
	org: string;
	month: string;
	currency: string;
	/** Null when no budget is set. */
	budget: number | null;
	spent: number;
	/** Null without a budget; negative once it is overspent. */
	remaining: number | null;
	gpu_hours: number;
	/** By cost; a null project collects deleted projects. */
	projects: { project: string | null; cost: number; gpu_hours: number; runs: number }[];
	top_runs: {
		entity: string;
		project: string;
		id: string;
		display_name: string | null;
		state: string;
		owner: Person | null;
		gpu_type: string | null;
		gpu_count: number | null;
		gpu_hours: number;
		cost: number;
	}[];
	/** GPU types with no price: they cost nothing until one is set. */
	unpriced: { gpu_type: string; gpu_hours: number }[];
	/** Runs whose GPUs aren't known, and their wall-clock hours. */
	unknown_gpu: { runs: number; hours: number };
}

/** An org's prices and budget settings. */
export interface Billing {
	currency: string;
	monthly_budget: number | null;
	budgets: { month: string; amount: number | null }[];
	rates: { gpu_type: string; hourly_cost: number }[];
	/** Every GPU type seen in the org's runs, priced or not. */
	gpu_types: { gpu_type: string; gpu_hours: number; runs: number; hourly_cost: number | null }[];
}

export type ColumnGroup = 'info' | 'config' | 'summary';

/**
 * How a column's values are stored and rendered. `num`, `time` and `duration` travel as
 * Float64 (times and durations in seconds); the others as JSON values.
 */
export type ColumnKind = 'num' | 'time' | 'duration' | 'str' | 'state' | 'bool' | 'json';

export interface ColumnDef {
	/** Flock column name, e.g. `config.trainer.learning_rate`. Also the sort key. */
	key: string;
	/** Header label (key without the group prefix). */
	label: string;
	group: ColumnGroup;
	kind: ColumnKind;
	width: number;
}

export type MetricFormat = 'num' | 'exp' | 'pct' | 'int' | 'si';

/**
 * Where a metric comes from: what the run logged (plotted against `_step`), or the system
 * metrics wandb samples on a timer (plotted against `_runtime`, in seconds).
 */
export type MetricStream = 'history' | 'system';

export interface MetricDef {
	key: string;
	section: string;
	format: MetricFormat;
	stream: MetricStream;
}

export interface SectionDef {
	key: string;
	/** Indices into `ProjectMeta.metrics`. */
	metrics: number[];
}

export interface ColumnGroupSpan {
	key: ColumnGroup;
	label: string;
	/** Index range [start, end) into the column list. */
	start: number;
	end: number;
}

export interface ProjectMeta {
	entity: string;
	project: string;
	pageSize: number;
	/** Flock clock (seconds) when the page was read; relative times use it so SSR and hydration agree. */
	now: number;
	columns: ColumnDef[];
	/** Contiguous column groups, in schema order. */
	groups: ColumnGroupSpan[];
	metrics: MetricDef[];
	sections: SectionDef[];
}

export type SortOrder = 'asc' | 'desc';

export interface RunQuery {
	page: number;
	sort: string;
	order: SortOrder;
	/** Runs per page (one of `PAGE_SIZES`); every run on a page is also plotted. */
	size: number;
}

export interface RunInfo {
	/** Flock run id (wandb run name). */
	id: string;
	/** Display name. */
	name: string;
	/** Updated live. */
	state: string;
	/** Position of the run in its project; drives its color so it never changes with sort or page. */
	ordinal: number;
	/** History lines already reflected in this page; the cursor live updates resume from. */
	lines: number;
	/** The same for system metric samples. */
	systemLines: number;
}

/** One page of runs. Live updates mutate it in place (see #lib/data/live.ts). */
export interface RunPage {
	query: RunQuery;
	/** Runs in the project. */
	total: number;
	/** Live runs in the project. */
	active: number;
	runs: RunInfo[];
	metricCount: number;
	/** Per column (same order as `ProjectMeta.columns`): numeric cells or null. */
	num: (Float64Array | null)[];
	/** Per column: JSON cells (strings, booleans, numbers, lists...) or null. */
	text: (unknown[] | null)[];
	/** Series for run `r` and metric `m` at `r * metricCount + m`; null when not logged. */
	series: (Series | null)[];
}

export interface WorkspaceData {
	meta: ProjectMeta;
	page: RunPage;
}

/** One run, as its own page shows it (`GET .../runs/{run}`). */
export interface RunDetail {
	id: string;
	entity: string;
	project: string;
	display_name: string | null;
	state: string;
	created_at: number;
	updated_at: number;
	heartbeat_at: number | null;
	tags: string[];
	group: string | null;
	job_type: string | null;
	notes: string | null;
	description: string | null;
	host: string | null;
	program: string | null;
	commit: string | null;
	sweep: string | null;
	exitcode: number | null;
	/** The account that logged it. */
	owner: number | null;
	gpu_type: string | null;
	gpu_count: number | null;
	gpu_hours: number;
	runtime: number;
	/** What its GPU hours cost at the org's prices, in its currency. */
	cost: number;
	config: Record<string, unknown>;
	summary: Record<string, unknown>;
	history_keys: string[];
	system_keys: string[];
	history_line_count: number;
	system_line_count: number;
	log_line_count: number;
}

/** A run's curves for one stream (`GET .../runs/{run}/history`); null y values weren't finite. */
export interface RunHistory {
	x: string;
	series: Record<string, { x: number[]; y: (number | null)[] }>;
}

/** A page of a run's console output. */
export interface RunLogs {
	total: number;
	lines: { line: number; text: string }[];
}

export interface RunFile {
	name: string;
	size: number;
	updated_at: number;
}
