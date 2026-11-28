import { deserialize } from '$app/forms';
import { invalidate, invalidateAll } from '$app/navigation';
import { toast } from 'svelte-sonner';
import { clearRunPages } from '#lib/data/cache.ts';
import { forgetRunValues } from '#lib/data/run-values.svelte.ts';
import { GROUPS, groupsApi, groupsCall, GroupsError } from '#lib/groups.ts';

/**
 * Adds a tag to runs or takes one off: the runs page's `tag` action, then the page's data read
 * again. Posted by hand rather than through `enhance`, whose `applyAction` moves focus to the
 * page and so would close a menu or palette it's in. Shared by the Tags submenu and the palette.
 */
export async function changeTags(
	ids: readonly string[],
	change: { add?: string; remove?: string }
) {
	const body = new FormData();
	for (const id of ids) body.append('run', id);
	body.append('add', change.add ?? '');
	if (change.remove) body.append('remove', change.remove);
	forgetRunValues();
	try {
		const res = await fetch(`${location.pathname}?/tag`, {
			method: 'POST',
			body,
			headers: { 'x-sveltekit-action': 'true' }
		});
		const result = deserialize(await res.text());
		if (result.type === 'failure' || result.type === 'error') {
			const message =
				result.type === 'failure'
					? String(result.data?.error ?? 'Couldn’t change the tags')
					: 'Couldn’t change the tags';
			toast.error(message.charAt(0).toUpperCase() + message.slice(1));
		}
	} catch {
		toast.error('Couldn’t change the tags');
	}
	clearRunPages();
	await invalidateAll();
}

/**
 * Deletes a tag: takes it off every run of the project that has it (a tag is only its runs'),
 * through the same `tag` action, for the runs found by asking Flock which have it.
 */
export async function deleteTag(entity: string, project: string, tag: string) {
	try {
		const base = `/api/projects/${encodeURIComponent(entity)}/${encodeURIComponent(project)}`;
		const res = await fetch(`${base}/runs/query`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ filter: { tags: tag }, limit: 10000, columns: [] })
		});
		if (!res.ok) throw new Error(String(res.status));
		const { ids } = (await res.json()) as { ids: string[] };
		if (ids.length) await changeTags(ids, { remove: tag });
		toast.success(`Deleted ${tag} from ${ids.length === 1 ? '1 run' : `${ids.length} runs`}`);
	} catch {
		toast.error('Couldn’t delete the tag');
	}
}

/** Adds runs to a saved group, saying so (`what`: the run's name, or "3 runs"). */
export async function addToGroup(
	entity: string,
	project: string,
	ids: readonly string[],
	group: { id: number; name: string },
	what: string
) {
	try {
		await groupsCall(groupsApi(entity, project, String(group.id)), {
			method: 'PATCH',
			body: { add: ids }
		});
		await invalidate(GROUPS);
		toast.success(`Added ${what} to ${group.name}`);
	} catch (e) {
		toast.error(e instanceof GroupsError ? e.message : 'Couldn’t add to the group');
	}
}
