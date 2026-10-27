import type { RunAxes } from './chart/axes.ts';
import type { Series } from './chart/series.ts';
import type { RunFilter } from './workspace/filters.ts';

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
	/** Its ID: the `{org}` and `[entity]` of URLs and the wandb SDK's `entity`. Never changes. */
	name: string;
	/** What people see it called; admins change it freely. */
	display_name: string;
	created_at: number;
	updated_at: number;
	project_count: number;
	member_count: number;
	/** A path on this dashboard (`/api/v1/avatars/…`), or null without a picture. */
	avatar_url: string | null;
	/** Super admins are admins of every organization, member or not. */
	role: Role | null;
}

/** Where an organization's W&B import is: `interrupted` means Flock restarted during it. */
export type ImportState = 'running' | 'finished' | 'failed' | 'cancelled' | 'interrupted';

/** The progress of an organization's latest W&B import (`GET /orgs/{org}/import`). */
export interface OrgImport {
	entity: string;
	base_url: string;
	state: ImportState;
	projects_total: number;
	projects_done: number;
	runs_total: number;
	runs_done: number;
	/** Runs the organization already had, from an earlier attempt. */
	runs_skipped: number;
	runs_failed: number;
	current_project: string | null;
	/** Why it failed, or the last run that couldn't be copied. */
	error: string | null;
	started_by: number | null;
	started_at: number;
	finished_at: number | null;
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
	orgs: { name: string; display_name: string; role: Role }[];
	created_at: number;
	avatar_url: string | null;
	/** Where the picture came from (only `/me` says). */
	avatar_source?: 'gravatar' | 'upload' | null;
}

/** A command-line sign-in (`flock login`), one per machine. Times are seconds since the epoch. */
export interface CliSession {
	id: number;
	/** The machine's hostname. */
	name: string | null;
	created_at: number;
	/** Null until the machine first uses it. Flock records it within the hour. */
	last_used_at: number | null;
	/** Pushed back by each use. */
	expires_at: number;
}

/** A machine asking to sign in with `flock login`, as the approval page shows it. */
export interface DeviceRequest {
	/** As the terminal shows it: `WDJB-MJHT`. */
	user_code: string;
	hostname: string;
	platform: string;
	ip: string | null;
	created_at: number;
	expires_at: number;
}

/** An organization as the sidebar's switcher lists it. */
export interface OrgEntry {
	/** The org's ID. */
	name: string;
	display_name: string;
	role: Role;
	avatar_url: string | null;
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
	/** By cost, runs with GPU hours only; a null project collects deleted projects. */
	projects: { project: string | null; cost: number; gpu_hours: number; runs: number }[];
	/** Spend of deleted projects an admin left out of the budget: not in `spent`. */
	excluded: { projects: number; cost: number; gpu_hours: number };
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
	/** Admins only: GPU types with no price, which cost nothing until one is set. */
	unpriced?: { gpu_type: string; gpu_hours: number }[];
	/** Admins only: runs whose GPUs aren't known, and their wall-clock hours. */
	unknown_gpu?: { runs: number; hours: number };
}

interface WarningBase {
	/** `{kind}:{month}`. */
	id: string;
	/** `YYYY-MM`. */
	month: string;
	/** Changes with what the warning is about; dismissing sends the one that was shown. */
	fingerprint: string;
	/** Dismissed by an admin for everyone, until it is about something new. */
	dismissed: boolean;
	dismissed_by: Person | null;
	dismissed_at: number | null;
}

export type BudgetWarning =
	| (WarningBase & { kind: 'unpriced'; gpu_types: { gpu_type: string; gpu_hours: number }[] })
	| (WarningBase & { kind: 'unknown_gpu'; runs: number; hours: number });

/** An org's budget warnings (admins only): only what amounts to a tenth of an hour or more. */
export interface BudgetWarnings {
	month: string;
	/** The month's warnings nobody dismissed. */
	warnings: BudgetWarning[];
	/** Dismissed warnings of every month that are still about nothing new, newest first. */
	dismissed: BudgetWarning[];
}

/** A deleted project whose runs used GPU hours (admins only). */
export interface DeletedProject {
	id: number;
	/** Null for projects deleted before Flock kept their names. */
	name: string | null;
	deleted_at: number | null;
	cost: number;
	gpu_hours: number;
	runs: number;
	months: { month: string; cost: number; gpu_hours: number }[];
	/** Its spend is left out of the budget. */
	excluded: boolean;
	excluded_at: number | null;
	excluded_by: Person | null;
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
	/** Its name in legends and readouts: the key, or a system metric's ("GPU 0 Memory (%)"). */
	label: string;
}

/**
 * One chart: a title and the metrics it plots, all of one stream (so one x axis). A logged
 * metric is a panel of its own; system metrics are grouped (every GPU's power on one chart).
 */
export interface PanelDef {
	/**
	 * Stable across pages and runs, so layouts, zoom and customizations can be kept by it: a
	 * logged metric's key, or `system:<chart>` (`system:gpu.powerWatts`).
	 */
	id: string;
	title: string;
	section: string;
	stream: MetricStream;
	format: MetricFormat;
	/** Indices into `ProjectMeta.metrics`. */
	metrics: number[];
}

/**
 * A run's `wandb.define_metric` for one key or glob (`train/*`), as far as plotting goes: the
 * step metric it is plotted against by default, and whether it is left out of the charts.
 */
export interface MetricDefinition {
	key?: string;
	/** Ends in `*`: every key starting with what comes before it. */
	glob?: string;
	step?: string;
	hidden?: boolean;
}

export interface SectionDef {
	key: string;
	/** Indices into `ProjectMeta.panels`. */
	panels: number[];
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
	panels: PanelDef[];
	sections: SectionDef[];
}

export type SortOrder = 'asc' | 'desc';

export interface RunQuery {
	page: number;
	sort: string;
	order: SortOrder;
	/** Runs per page (one of `PAGE_SIZES`); every run on a page is also plotted. */
	size: number;
	/** Text a run's name or id contains; empty for every run. */
	search: string;
	/** Conditions every run shown meets. */
	filters: RunFilter[];
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
	/** Its metric definitions (step metrics, hidden metrics); updated live. */
	defs: MetricDefinition[];
}

/** One page of runs. Live updates mutate it in place (see #lib/data/live.ts). */
export interface RunPage {
	query: RunQuery;
	/** Runs matching the page's search and filters: all of the project's without them. */
	total: number;
	/** Runs in the project. */
	projectTotal: number;
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
	/** Per run, its x-axis mappings (see #lib/chart/axes.ts); grown by live updates. */
	axes: RunAxes[];
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
	/** In effect: set by hand, else detected. */
	gpu_type: string | null;
	gpu_count: number | null;
	/** What wandb's metadata says the run used. */
	gpu_detected: { type: string | null; count: number | null };
	gpu_hours: number;
	runtime: number;
	/** What its GPU hours cost at the org's prices, in its currency. */
	cost: number;
	config: Record<string, unknown>;
	summary: Record<string, unknown>;
	/** Flocks from before metric definitions were read send none. */
	metric_defs?: MetricDefinition[];
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
