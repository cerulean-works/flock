/**
 * The Python shown where there's nothing yet (an empty project, Datasets or Models): short,
 * complete examples that run as written once the SDK is signed in.
 */

import { type Scope, asScope } from './scope.ts';

/** Log a run with wandb: a config, a few metrics each step, then finish. */
export const wandbSnippet = (entity: string, project: string) =>
	[
		'import math',
		'import wandb',
		'',
		'run = wandb.init(',
		`    entity="${entity}",`,
		`    project="${project}",`,
		'    config={"lr": 3e-4, "batch_size": 64, "steps": 200},',
		')',
		'',
		'for step in range(run.config.steps):',
		'    loss = 2.5 * math.exp(-step / 60) + 0.1',
		'    lr = run.config.lr * (1 - step / run.config.steps)',
		'    run.log({"train/loss": loss, "train/lr": lr}, step=step)',
		'',
		'run.finish()'
	].join('\n');

/**
 * Write a small Lance dataset into a scope, where `datasets.location` says: the org's global
 * datasets with `org=`, a project's with `project="org/project"` (D160).
 */
export function datasetSnippet(scope: Scope | string, path = 'eval/mmlu') {
	const { org, project } = asScope(scope);
	const where =
		project === null
			? `org=${JSON.stringify(org)}`
			: `project=${JSON.stringify(`${org}/${project}`)}`;
	return [
		'import lance',
		'import pyarrow as pa',
		'from flock.hub import datasets',
		'',
		'table = pa.table({"question": ["2 + 2 = ?"], "answer": ["4"]})',
		'',
		`where = datasets.location(${JSON.stringify(path)}, ${where})`,
		'lance.write_dataset(',
		'    table, where.uri, storage_options=where.storage_options',
		')'
	].join('\n');
}

/**
 * Save a model into a scope, then load it back: global models are named `org/path`, a project's
 * by a path with `project=` (D162).
 */
export function modelSnippet(scope: Scope | string, name = 'my-model') {
	const { org, project } = asScope(scope);
	const where =
		project === null ? `"${org}/${name}"` : `"${name}", project=${JSON.stringify(project)}`;
	return [
		'from flock.hub import models',
		'',
		`models.save_to_hub(`,
		`    model, ${where}, tags=["baseline"]`,
		`)`,
		'',
		'# Load the primary version back on any machine',
		`model = models.load_from_hub(${where})`
	].join('\n');
}
