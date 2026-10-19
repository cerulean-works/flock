import { fail, type RequestEvent } from '@sveltejs/kit';
import { change, projectPath, refused } from '#lib/server/flock.ts';
import { formText, parseTags, projectNameError } from '#lib/utils.ts';

/** What Flock reports after changing a selection of runs. */
interface Changed {
	ids: string[];
	count: number;
}

/** Selected runs travel as repeated `run` fields. */
const selected = (form: FormData) => form.getAll('run').map(String).filter(Boolean);

type Event = RequestEvent<{ entity: string; project: string }>;

/**
 * The GPUs set by hand, when the form edits them: a blank field goes back to what wandb
 * detected. Nothing for a form without GPU fields, which leaves them as they are; null when the
 * count isn't a whole number.
 */
function gpuFields(form: FormData) {
	if (!form.has('gpu_type')) return {};
	const type = formText(form, 'gpu_type');
	const count = formText(form, 'gpu_count');
	const n = Number(count);
	if (count && !(Number.isInteger(n) && n >= 0)) return null;
	return { gpu_type: type || null, gpu_count: count ? n : null };
}

/**
 * Run changes, for the project's workspace and a run's own page (both under
 * `/{entity}/{project}`). The pages' data comes from their loads; these only write.
 */
export const runActions = {
	/** Edit one run: every field is sent, and an empty one clears that attribute. */
	edit: async ({ params, request }: Event) => {
		const form = await request.formData();
		const run = formText(form, 'run');
		const gpu = gpuFields(form);
		if (!gpu) return fail(400, { error: 'Enter the GPU count as a whole number' });
		const res = await change(
			'PATCH',
			`${projectPath(params.entity, params.project)}/runs/${encodeURIComponent(run)}`,
			{
				display_name: formText(form, 'display_name') || null,
				group: formText(form, 'group') || null,
				job_type: formText(form, 'job_type') || null,
				notes: formText(form, 'notes') || null,
				tags: parseTags(formText(form, 'tags')),
				...gpu
			}
		);
		if (refused(res)) return res;
		return { count: 1 };
	},

	/** Add and remove tags on many runs. */
	tag: async ({ params, request }: Event) => {
		const form = await request.formData();
		const add = parseTags(formText(form, 'add'));
		const remove = form.getAll('remove').map(String);
		if (!add.length && !remove.length) return fail(400, { error: 'Add or remove a tag' });
		const res = await change<Changed>(
			'POST',
			`${projectPath(params.entity, params.project)}/runs/update`,
			{ runs: selected(form), add_tags: add, remove_tags: remove }
		);
		if (refused(res)) return res;
		return { count: res.data.count };
	},

	/** Move runs to another project of the entity, creating it if asked. */
	move: async ({ params, request }: Event) => {
		const form = await request.formData();
		const to = formText(form, 'to');
		const invalid = projectNameError(to);
		if (invalid) return fail(400, { error: invalid });
		const res = await change<Changed>(
			'POST',
			`${projectPath(params.entity, params.project)}/runs/move`,
			{ runs: selected(form), to, create: form.get('create') === 'on' }
		);
		if (refused(res)) return res;
		return { count: res.data.count, to };
	},

	/** Delete runs; active ones only with `force`. */
	delete: async ({ params, request }: Event) => {
		const form = await request.formData();
		const res = await change<Changed>(
			'POST',
			`${projectPath(params.entity, params.project)}/runs/delete`,
			{ runs: selected(form), force: form.get('force') === 'on' }
		);
		if (refused(res)) return res;
		return { count: res.data.count };
	}
};
