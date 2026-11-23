import type { RunAxes } from './chart/axes.ts';
import type { Series } from './chart/series.ts';
import type { RunFilter } from './workspace/filters.ts';

/** Flock run states; `running` and `preempting` are live. */
export type RunState = 'running' | 'preempting' | 'finished' | 'failed' | 'crashed';

export const LIVE_STATES: ReadonlySet<string> = new Set(['running', 'preempting']);

/** A project, as Flock lists it. Times are seconds since the epoch. */
export interface ProjectInfo {
	/** Names its folder in flock-datasets and flock-models, `-projects/{id}/` (D160). */
	id?: number;
	entity: string;
	name: string;
	description: string | null;
	created_at: number;
	updated_at: number;
	run_count: number;
	active_run_count: number;
	column_count: number;
	/** Public projects can be read by anyone with the link (014, D110). */
	visibility: Visibility;
	/** The ID of the user who created it, who may change its visibility (D118); null if unknown. */
	created_by: number | null;
	/** What the caller may do with it; only the project's own GET says. */
	access?: Access;
}

export type Visibility = 'private' | 'public';

export type Role = 'admin' | 'member';

/**
 * What the viewer may do in an org's projects and datasets (D111): `public` is someone outside
 * the org, signed in or not, who can only read what was made public.
 */
export type Access = Role | 'public';

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
	/** Seconds S3 keys from `flock s3 credentials` and Python live. */
	s3_ephemeral_ttl: number;
	/** The longest any of its S3 keys may live, in seconds; null when keys may never expire. */
	s3_key_max_ttl: number | null;
	/** Keep the newest N untagged versions of each model (D109); null keeps every version. */
	model_retention?: number | null;
}

/**
 * One of the user's wandb keys, for the wandb SDK. Older keys (`flock-…`) are listed with the
 * rest. Flock keeps only a hash, so the key itself is never listed.
 */
export interface WandbKey {
	id: number;
	name: string;
	/** The key's first characters, to tell keys apart. Shown as given, never parsed. */
	prefix: string;
	created_at: number;
}

/** A wandb key just created: the only time its secret is shown. */
export interface WandbKeyCreated extends WandbKey {
	key: string;
}

/** Which of the account page's key tabs is shown (`?keys=`). */
export type KeysTab = 'wandb' | 's3';

/** The S3 proxy, from `auth/config`; null when the server has no S3. */
export interface S3Config {
	endpoint: string;
	region: string;
}

/** One of the user's S3 keys. Ephemeral ones were issued for a CLI sign-in or an API key. */
export interface S3Key {
	id: number;
	name: string;
	access_key_id: string;
	org: string | null;
	read_only: boolean;
	ephemeral: boolean;
	/** The CLI sign-in or API key an ephemeral key went to. */
	session_id: number | null;
	api_key_id: number | null;
	/** That sign-in's machine, or that API key's name. */
	source: string | null;
	created_at: number;
	/** When the proxy stops taking it (sooner if the org lowered its maximum); null: never. */
	expires_at: number | null;
	last_used_at: number | null;
}

/** A key just created: its secret is shown this once. */
export interface S3KeyCreated extends S3Key {
	secret_access_key: string;
	endpoint: string;
	region: string;
}

/** The buckets Flock creates and owns in each org (D85, D97). */
export type ManagedKind = 'datasets' | 'models';

/** An organization's S3 bucket. */
export interface Bucket {
	name: string;
	/** Its name on the S3 server: `{org pk, 5 digits}-{name}`. */
	upstream_name: string;
	created_at: number;
	created_by: Person | null;
	/** Set while a deleted bucket is still being emptied (listed with `deleting`). */
	deleted_at?: number;
	/**
	 * The kind of a bucket Flock creates and owns: `datasets` for the org's flock-datasets,
	 * `models` for its flock-models. Null otherwise.
	 */
	managed?: ManagedKind | null;
}

/** An object in a folder's listing (`GET …/buckets/{bucket}/objects`). */
export interface StorageObject {
	key: string;
	size: number;
	last_modified: number;
	etag: string;
}

/** One page of one level of a bucket: subfolders (ending in `/`), then objects. */
export interface Listing {
	prefix: string;
	folders: string[];
	objects: StorageObject[];
	/** The next page's, null on the last. */
	cursor: string | null;
}

/** An object's details, from a HEAD (`GET …/buckets/{bucket}/object?key=`). */
export interface ObjectDetails {
	key: string;
	size: number;
	last_modified: number | null;
	etag: string;
	content_type: string | null;
	/** Its `x-amz-meta-*` headers, without the prefix. */
	metadata: Record<string, string>;
}

/** What the dashboard asks `POST …/presign` for (D69). */
export type PresignRequest =
	| {
			method: 'get';
			key: string;
			disposition?: 'inline' | 'attachment';
			link?: boolean;
			content_type?: string;
	  }
	| { method: 'put'; key: string; size: number }
	| { method: 'multipart'; key: string; size: number; content_type?: string }
	| { method: 'parts'; key: string; upload_id: string; parts: number[] }
	| { method: 'complete' | 'abort'; key: string; upload_id: string };

/** Its answer: a presigned `url`, an upload started, or URLs of parts by number. */
export interface Presigned {
	url?: string;
	expires_at?: number;
	upload_id?: string;
	part_size?: number;
	part_count?: number;
	parts?: Record<string, string>;
}

/** Something that happened to a bucket, and how it was asked for (`detail.via`). */
export interface BucketEvent {
	kind: 'created' | 'deleted' | 'purged';
	user: Person | null;
	at: number;
	detail: {
		via?: 'ui' | 'cli' | 'api' | 'purge' | 'org' | 'flock';
		machine?: string;
		key_name?: string;
		objects?: number;
	};
}

export interface BucketDetail extends Bucket {
	events: BucketEvent[];
}

// ----------------------------------------------------------------------------------------------
// Datasets (013): the Lance datasets in an org's flock-datasets bucket.

/**
 * A dataset in a folder's listing. Asked with `details`, Flock fills in `size` and, for manifests
 * in Lance's old naming, `version`. Both stay null when the dataset can't be read.
 */
export interface DatasetEntry {
	name: string;
	path: string;
	version: number | null;
	updated_at: number | null;
	size?: number | null;
	/** Made by Flock as a fork or a blend (023), for the listing's label. */
	origin?: 'fork' | 'blend' | null;
}

/** A file in a folder's listing of flock-datasets. */
export interface DatasetFile {
	name: string;
	key: string;
	size: number;
	last_modified: number;
}

/** One page of a folder of flock-datasets (`GET …/datasets/at`). */
export interface DatasetFolder {
	kind: 'folder';
	path: string;
	datasets: DatasetEntry[];
	folders: { name: string; path: string }[];
	files: DatasetFile[];
	cursor: string | null;
}

/** What `GET …/datasets/at` says of a path that is a dataset. */
export interface DatasetAt {
	kind: 'dataset';
	path: string;
	version: number | null;
	updated_at: number | null;
}

/** A field of the schema, with its children (struct fields, a list's items). */
export interface SchemaField {
	name: string;
	/** Arrow's name for its type, or `blob` for a Lance blob column. */
	type: string;
	nullable: boolean;
	metadata: Record<string, string>;
	children: SchemaField[];
}

/** The dataset at one version (`GET …/datasets/info`). */
export interface DatasetInfo {
	path: string;
	version: number;
	/** When that version was written. */
	timestamp: number;
	/** The latest version now; null when Lance's old naming hides it. */
	latest_version: number | null;
	rows: number;
	columns: number;
	fragments: number;
	data_size: number;
	schema: SchemaField[];
	metadata: Record<string, string>;
	/** Some column holds binary data, so the row viewer shows fewer rows a page. */
	binary: boolean;
	details: {
		data_storage_version: string;
		stable_row_ids: boolean;
		fragments: number;
		indices: { name: string; type: string; columns: string[] }[];
	};
	tags: { name: string; version: number }[];
	/** Anyone with the link can open it (D115). */
	public?: boolean;
	/** Where it came from, when Flock made it as a fork or a blend (023, D179). */
	origin?: DatasetOrigin | null;
	/** The datasets reading it (D177), for members; null from outside the org. */
	dependents?: { count: number; trashed: number; paths: string[] } | null;
}

/** A source of a blend, as its lineage records it. */
export interface BlendSourceRecord {
	path: string;
	version: number;
	rows: number;
	live: number;
	fraction?: number;
}

/** Where a fork or blend came from (`info.origin`). */
export type DatasetOrigin = (
	| { kind: 'fork'; source: { path: string; version: number } }
	| {
			kind: 'blend';
			seed: number;
			spec: { mode: 'rows' } | { mode: 'fraction'; total: number };
			replace: boolean;
			columns: BlendColumn[];
			sources: BlendSourceRecord[];
			version?: number;
	  }
) & {
	/** Rewritten into its own files (D178): it reads no other dataset any more. */
	independent?: boolean;
	created_by: Person | null;
	created_at: number;
};

/** Where an output column of a blend comes from in one source (D175). */
export type ColumnFrom =
	{ column: string; cast?: boolean } | { null: true } | { value: string | number | boolean };

/** An output column of a blend and how each source gives it, keyed by the source's path. */
export interface BlendColumn {
	name: string;
	type: string;
	nullable: boolean;
	from: Record<string, ColumnFrom>;
}

/** A source of a blend as the dialog asks for it. */
export interface BlendSourceRequest {
	path: string;
	version?: string | number | null;
	rows?: number;
	fraction?: number;
}

/** What `POST …/datasets/blend/preview` answers. */
export interface BlendPreview {
	sources: {
		path: string;
		version: number;
		live: number;
		rows: number;
		share: number | null;
		schema: { name: string; type: string; nullable: boolean }[];
	}[];
	total: number;
	columns: BlendColumn[];
	automatic: boolean;
	unused: Record<string, { name: string; type: string }[]>;
	writes: { column: string; source: string; kind: 'cast' | 'copy' | 'value' }[];
	/** Every problem refusing the blend. */
	problems: string[];
	/** Those of `problems` that are the column map's, for the Columns step. */
	column_problems: string[];
}

/** A blend or a dataset made independent, run by the API (D176). */
export interface DatasetJob {
	id: number;
	kind: 'blend' | 'independent';
	dest: string;
	options: Record<string, unknown>;
	state: 'running' | 'finished' | 'failed' | 'cancelled' | 'interrupted';
	phase: string | null;
	rows_total: number;
	rows_done: number;
	progress: number;
	result: { path: string; version: number; rows: number } | null;
	error: string | null;
	started_by: Person | null;
	started_at: number;
	finished_at: number | null;
}

/** Where a dataset is, or would be, in S3 (`GET …/datasets/location`, D163). */
export interface DatasetLocation {
	bucket: string;
	/** The real key: `-projects/{pk}/{path}` for a project's dataset (D160). */
	key: string;
	uri: string;
	path: string;
	project: string | null;
}

/** What a page of a scope's datasets shows: a folder, or a dataset (D169). */
export type DatasetsPath =
	| { kind: 'folder'; path: string; folder: DatasetFolder; now: number }
	| {
			kind: 'dataset';
			path: string;
			info: DatasetInfo | null;
			failed: string | null;
			requested: string | null;
			s3: S3Config;
			/** Where it is in flock-datasets: its path when global, under `-projects/` otherwise. */
			key: string;
			now: number;
	  };

/** A cell, rendered by the API for display (D90). */
export type Cell =
	| { k: 'null' }
	| { k: 'num'; v: number | string; label?: string }
	| { k: 'bool'; v: boolean }
	| { k: 'str'; v: string; len?: number }
	| { k: 'time'; v: string }
	| { k: 'image'; mime: string; size: number; data: string }
	| { k: 'bin'; size: number }
	| { k: 'blob'; size: number | null }
	| { k: 'vec'; dtype: string; len: number; head: (number | string | null)[] }
	| { k: 'json'; v: string; len?: number };

/** A page of rows (`GET …/datasets/rows`). */
export interface RowsPage {
	version: number;
	offset: number;
	limit: number;
	total: number;
	columns: { name: string; type: string; feature?: string }[];
	rows: Cell[][];
}

/** One cell in full, for an opened row (`GET …/datasets/cell`). */
export interface FullCell {
	row: number;
	column: string;
	type: string;
	cell: Cell;
}

/** A version in the history (`GET …/datasets/versions`). */
export interface DatasetVersion {
	version: number;
	timestamp: number;
	tags: string[];
	/** Lance's operation (Append, Overwrite, Delete…), null without a transaction file. */
	operation: string | null;
	rows: number;
	/** Rows added (or removed, below zero) since the version before, when that one exists. */
	rows_delta: number | null;
	/** `transaction_properties` the writer set, such as a message. */
	properties: Record<string, string>;
	/** How many forks and blends read this version, which keeps it from cleanup (D173). */
	kept_for?: number;
}

export interface VersionsPage {
	versions: DatasetVersion[];
	latest: number;
	cursor: number | null;
}

/** A deleted dataset in the trash (`GET …/datasets/trash`), for org admins. */
export interface TrashEntry {
	id: number;
	/** A dataset, or a folder deleted with everything in it (D132). */
	kind: 'dataset' | 'folder';
	/** Relative to its scope. */
	path: string;
	/** The project it was deleted from, null for global (D168). */
	project?: string | null;
	deleted_by: Person | null;
	deleted_at: number;
	/** Null while the move to the trash is unfinished. */
	moved_at: number | null;
	/** When the sweep removes it for good. */
	expires_at: number;
	latest_version: number | null;
	objects: number;
	bytes: number;
}

/** One item of `POST …/datasets/move` (D131). */
export interface MoveResult {
	path: string;
	to: string;
	moved: boolean;
	error: string | null;
	/** Something already exists at `to`. */
	taken: boolean;
}

/** One item of `POST …/datasets/delete` with `paths` (D132). */
export interface DeleteResult {
	path: string;
	deleted: boolean;
	error: string | null;
	entry: TrashEntry | null;
}

// ----------------------------------------------------------------------------------------------
// Models (014): the model registry, in an org's flock-models bucket.

/** A model in a folder's listing (`GET …/models?folder=`). */
export interface ModelEntry {
	/** Its path, `eval/olmo2-1b` (D158). */
	name: string;
	/** How refs name it: `acme/eval/olmo2-1b`, or `pretrain:eval/olmo2-1b` in a project (D162). */
	model: string;
	org?: string;
	/** Its project, null for global (D159). */
	project?: string | null;
	created_at: number;
	created_by: Person | null;
	/** Live versions. */
	versions: number;
	/** The newest version's number; null before the first one is committed. */
	latest: number | null;
	primary: number | null;
	/** The primary version's size in bytes. */
	size: number | null;
	updated_at: number;
}

/** How one model's move went (`PATCH …/models/{path}/-`, D166), as the Move dialog asked. */
export interface ModelMove {
	/** Its path before. */
	name: string;
	/** Its path after, in the scope it moved to. */
	to: string;
	moved: boolean;
	error: string | null;
}

/** A tag of a model, with the version it's on. */
export interface ModelTag {
	name: string;
	version: number;
	moved_by: Person | null;
	moved_at: number;
}

/** One level of a scope's models (`GET …/models?folder=&project=`, D158): models, then folders. */
export interface ModelFolder {
	kind: 'folder';
	folder: string;
	project: string | null;
	models: ModelEntry[];
	/** Each with the number of models under it. */
	folders: { name: string; path: string; models: number }[];
	cursor: string | null;
}

/** What `GET …/models?folder=` says of a path that is a model. */
export interface ModelAt {
	kind: 'model';
	name: string;
	project: string | null;
}

/** What a page of a scope's models shows: a folder, or a model (D169). */
export type ModelsPath =
	| { kind: 'folder'; path: string; folder: ModelFolder; now: number }
	| {
			kind: 'model';
			path: string;
			info: ModelInfo;
			version: ModelVersionDetail | null;
			requested: string | null;
			now: number;
	  };

/** A model (`GET …/models/{path}/-`). */
export interface ModelInfo {
	name: string;
	model: string;
	org?: string;
	project?: string | null;
	created_at: number;
	created_by: Person | null;
	versions: number;
	latest: number | null;
	primary: number | null;
	/** Who made the primary version primary, and when (D107). */
	primary_set_by: Person | null;
	primary_set_at: number | null;
	tags: ModelTag[];
	/** Its folder in flock-models, ending in `/`, which keeps its old name after a rename (D134). */
	prefix: string;
}

/** A version as the listings show it (`GET …/models/{name}/versions`). */
export interface ModelVersion {
	/** As refs name the model: `acme/eval/olmo2-1b`, or `pretrain:sweeps/lr-3e-4` (D162). */
	model: string;
	name: string;
	org?: string;
	/** The model's project, null for global. */
	project?: string | null;
	number: number;
	digest: string;
	total_bytes: number;
	metadata: Record<string, unknown>;
	tags: string[];
	primary: boolean;
	step: number | null;
	/** The run that made it, `entity/project/id`. */
	run: string | null;
	created_by: Person | null;
	created_at: number;
	/** Eval runs that scored it, deleted runs left out. */
	eval_runs: number;
	bucket: string;
	/** Its folder in flock-models, ending in `/`. */
	prefix: string;
}

export interface ModelFile {
	path: string;
	size: number;
	sha256: string;
}

/** A version with its files and eval runs (`GET …/versions/{number}`, `…/resolve`). */
export interface ModelVersionDetail extends ModelVersion {
	files: ModelFile[];
	/** The eval runs that scored it, as `entity/project/id`. */
	evals: string[];
}

export interface ModelVersionsPage {
	versions: ModelVersion[];
	cursor: number | null;
}

/** An eval run of a version, with its scores (the dashboard's `…/evals`, 025 D183). */
export interface ModelEvalRun {
	/** `entity/project/id`. */
	path: string;
	entity: string;
	project: string;
	id: string;
	display_name: string | null;
	state: string;
	created_at: number;
	/** Its scores by row (`evalScores`), or null when it has no results file to read them from. */
	scores: EvalScores | null;
}

/** One task of an eval run's `results.json` (eval's results file, schemas 8 to 10). */
export interface EvalTask {
	task: string;
	task_hash: string;
	dataset: {
		repo: string;
		revision: string;
		subset: string | null;
		split: string;
		/** Schema 9 only. */
		private?: boolean;
	};
	primary_metric: string;
	/** A non-finite score is written as null. */
	metrics: Record<string, number | null>;
	num_docs: number;
	limit: number | null;
	truncation: { requests: number; docs: number; max_tokens_cut: number };
}

/** One suite of an eval run's `results.json`. */
export interface EvalSuite {
	suite: string;
	suite_hash: string;
	members: string[];
	/** The metric read from each member, or null for each task's primary metric. */
	metric: string | null;
	/** Null for a suite that only groups others. */
	score: number | null;
}

/** What produced an eval run (`results.json`'s `provenance`). */
export interface EvalProvenance {
	model: string;
	model_digest?: string | null;
	/** Schema 10 on: a Flock model's org, project (null when global) and path. */
	model_org?: string | null;
	model_project?: string | null;
	model_path?: string | null;
	flock_commit: string;
	command: string | null;
	device: string;
	dtype: string;
	batch_tokens: number;
	max_length: number;
	add_bos?: boolean;
	attention: string;
	sdpa_backend?: string | null;
	torch: string;
	transformers: string;
	tokenizers: string;
	datasets: string;
	python: string;
	created_at: string;
}

/** An eval run's `results.json`, as the Results tab reads it (025 D184). */
export interface EvalResults {
	schema_version: number;
	provenance: EvalProvenance;
	tasks: EvalTask[];
	suites: EvalSuite[];
}

/** A row of the Evals tab: a suite's score, or a task's primary metric. */
export interface EvalRow {
	/** `suites/{suite}@{suite_hash}` or `{task}/{metric}@{task_hash}`. */
	key: string;
	kind: 'suite' | 'task';
	name: string;
	metric: string | null;
	/** The suite or task hash its scores were made under. */
	hash: string;
}

/** An eval run's scores, rows in its results file's order. */
export interface EvalScores {
	rows: EvalRow[];
	values: Record<string, number | null>;
}

/** One task two eval runs both scored the same way (eval's `TaskDiff`). */
export interface TaskDiff {
	task: string;
	task_hash: string;
	metric: string;
	num_docs: number;
	both_right: number;
	both_wrong: number;
	/** Ids of the docs only run A got right, in A's order. */
	only_a: string[];
	only_b: string[];
}

/** Two eval runs compared doc by doc (`GET …/runs/{run}/diff`, eval's `ItemDiff` schema 1). */
export interface ItemDiff {
	/** Each run's model. */
	a: string;
	b: string;
	tasks: TaskDiff[];
	/** Every other task, with why: `only_a`, `only_b`, `task_hash`, `not_accuracy`, `no_predictions` or `no_outcome`. */
	not_compared: [string, string][];
	schema_version: number;
}

/** One run's answer to a doc (eval's `Answer`, in `GET …/runs/{run}/items`). */
export interface ItemAnswer {
	/** The run's `provenance.model`. */
	model: string;
	/** The choice the primary metric picks; null for `acc_greedy`, which ranks none. */
	pick: number | null;
	correct: boolean;
	/** Each choice's summed log-probability after its context. */
	logprob: number[];
	/** Each choice's probability renormalized over the choices. */
	prob: number[];
	/**
	 * The softmax of the scores the primary metric ranks by: a share, not a probability, under a
	 * length-normalized metric; null for `acc_greedy`.
	 */
	share: number[] | null;
}

/** A doc with its text and each run's answer (eval's `Item`, `flock eval items --json`). */
export interface EvalItem {
	task: string;
	/** The primary metric, which `pick`, `correct` and `share` follow. */
	metric: string;
	id: string;
	/** The context the choices share, or null when each has its own (`contexts`). */
	question: string | null;
	contexts: string[] | null;
	/** Each choice's continuation as scored, leading space included. */
	choices: string[];
	gold: number;
	/** One answer per run, in the order the runs were asked for. */
	runs: ItemAnswer[];
}

/** One run's answer in a row of the items list (`GET …/runs/{run}/items`). */
export interface ItemRunSummary {
	correct: boolean;
	/** The choice the primary metric picks; null for `acc_greedy`, which ranks none. */
	pick: number | null;
	/** The picked choice as a reader names it, clipped. */
	pick_text: string | null;
	/**
	 * The gold choice's score minus the best wrong choice's, in the primary metric's units; null
	 * for `acc_greedy` or a doc with one choice.
	 */
	margin: number | null;
	/** The gold choice's probability renormalized over the choices. */
	gold_prob: number;
}

/** A doc as a row of the items list: enough to skim, the whole `EvalItem` comes when it opens. */
export interface ItemSummary {
	id: string;
	/** The part of the question that varies (the lines every question shares left out), clipped. */
	text: string;
	gold: number;
	/** The gold choice as a reader names it, clipped. */
	gold_text: string;
	/** One answer per run, in the order the runs were asked for. */
	runs: ItemRunSummary[];
}

/** A page of a task's items (`GET …/runs/{run}/items`). */
export interface ItemsPage {
	/** How many items the filter keeps in all. */
	total: number;
	offset: number;
	items: ItemSummary[];
}

/** The compare page's row of suite squares for two runs (`GET …/runs/{run}/items/suites`). */
export interface ItemSuites {
	suites: {
		suite: string;
		/** Every accuracy task under it both runs scored under one hash. */
		tasks: string[];
		/** How many of its docs are in each cell of the runs' diff. */
		counts: Partial<Record<ItemKeep, number>>;
		/** Up to 800 docs, taken evenly through each cell: A's margin, B's, the cell's index in eval's `CELLS`. */
		dots: [number, number, number][];
	}[];
}

/** Where a link leaves the items list (028): each part only when it isn't the default. */
export interface ItemsView {
	task?: string;
	keep?: ItemKeep;
	sort?: 'doc';
	/** From 1. */
	page?: number;
	/** The open item's id. */
	item?: string;
}

/** What an items page keeps: one run's docs by outcome, or a cell of two runs' diff. */
export type ItemKeep =
	'wrong' | 'right' | 'all' | 'only_a' | 'only_b' | 'both_wrong' | 'both_right';

/** An eval run the compare page's picker offers (`GET /api/projects/…/eval-runs`). */
export interface EvalRunChoice {
	id: string;
	display_name: string | null;
	created_at: number;
	state: string;
	/** The model its config names, `flock:org/path@N`, if any. */
	model: string | null;
}

/** A deleted model or version in the trash (`GET …/models/-/trash`), for org admins. */
interface ModelTrashBase {
	id: number;
	name: string;
	model: string;
	/** The project it was deleted from, null for global (D168). */
	project?: string | null;
	deleted_by: Person | null;
	deleted_at: number;
	expires_at: number;
	/** False while its files are still moving to the trash. */
	moved: boolean;
	bytes: number;
}

export interface ModelTrashModel extends ModelTrashBase {
	kind: 'model';
	/** The versions deleted with it. */
	versions: number;
}

export interface ModelTrashVersion extends ModelTrashBase {
	kind: 'version';
	number: number;
	/** The checkpoint tags that went with it. */
	tags: string[];
	eval_runs: number;
}

export type ModelTrashEntry = ModelTrashModel | ModelTrashVersion;

/** The versions a run made and those it scored (`GET …/runs/{run}/models`). */
export interface RunModels {
	made: (ModelVersion & { evals: string[] })[];
	scored: (ModelVersion & { evals: string[] })[];
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

/**
 * Someone who logged runs, as a project's owners and a run's details name them: without their
 * email for viewers outside the org (D113).
 */
export interface Owner {
	id: number;
	name: string;
	email?: string;
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
 * How a column's values are stored and rendered. `num`, `time`, `duration` and `user` travel
 * as Float64 (times and durations in seconds, users as their ids); the others as JSON values.
 */
export type ColumnKind = 'num' | 'time' | 'duration' | 'user' | 'str' | 'state' | 'bool' | 'json';

export interface ColumnDef {
	/** Flock column name, e.g. `config.trainer.learning_rate`. Also the sort key. */
	key: string;
	/** Header label (key without the group prefix). */
	label: string;
	group: ColumnGroup;
	kind: ColumnKind;
	width: number;
}

/**
 * How a metric's values read. The byte formats show a unit sized to the values (`512MB`,
 * `40GB`); `mb` and `gb` are for values logged in those units.
 */
export type MetricFormat = 'num' | 'exp' | 'pct' | 'int' | 'si' | ByteFormat;
export type ByteFormat = 'bytes' | 'bytes/s' | 'mb' | 'gb';

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
	/** That account, named; null for runs from before accounts. */
	owner_user?: Owner | null;
	/** In effect: set by hand, else detected. */
	gpu_type: string | null;
	gpu_count: number | null;
	/** What wandb's metadata says the run used. */
	gpu_detected: { type: string | null; count: number | null };
	gpu_hours: number;
	runtime: number;
	/** What its GPU hours cost at the org's prices, in its currency; null outside the org. */
	cost: number | null;
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

/** A Hugging Face import the API runs (D124), as `GET /orgs/{org}/imports/hf` lists it. */
export interface HFImport {
	id: number;
	kind: 'dataset' | 'model';
	repo: string;
	revision: string;
	/** The commit, once the Hub was asked. */
	sha: string | null;
	/** The dataset folder, or the model's name. */
	dest: string;
	/** The project it imports into, null for global. */
	project?: string | null;
	/** A dataset's configs, each with the splits imported. */
	configs?: Record<string, string[]>;
	/** A model version's tags, and whether it becomes primary. */
	tags?: string[];
	primary?: boolean;
	state: 'running' | 'finished' | 'failed' | 'cancelled' | 'interrupted';
	phase: 'downloading' | 'converting' | 'uploading' | 'committing' | null;
	bytes_total: number;
	bytes_done: number;
	files_total: number;
	files_done: number;
	rows_done: number;
	/** From 0 to 1. */
	progress: number;
	/** Each config's and split's dataset, or the model's version. */
	result: {
		datasets?: { config: string; split: string; path: string; version: number; rows: number }[];
		model?: string;
		version?: number;
		existing?: boolean;
	} | null;
	error: string | null;
	/** The `flock` command that does the same import. */
	command: string;
	started_by: Person | null;
	started_at: number;
	finished_at: number | null;
}

/** A split of a Hugging Face dataset, with its size when the Hub knows it. */
export interface HFSplit {
	name: string;
	size: number | null;
	rows: number | null;
}

/** What the dataset import dialog shows of a repository. */
export interface HFDatasetPreview {
	kind: 'dataset';
	repo: string;
	revision: string;
	/** Each config with its splits (null when only an import would find them). */
	configs: { name: string; size: number | null; splits: HFSplit[] | null }[];
	default_config: string | null;
	/** The largest import the server runs. */
	max_bytes: number;
}

/** What deleting a project deletes with it (`GET projects/{entity}/{project}/storage`, D167). */
export interface ProjectStorage {
	datasets: number;
	models: number;
	bytes: number;
	/** Each dataset and model by path with its bytes, datasets first. */
	items: { kind: 'dataset' | 'model'; path: string; bytes: number }[];
}
