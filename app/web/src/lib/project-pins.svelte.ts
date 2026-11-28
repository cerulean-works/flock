import { SvelteMap } from 'svelte/reactivity';
import { toast } from 'svelte-sonner';
import { invalidateAll } from '$app/navigation';
import type { ProjectInfo } from '#lib/types.ts';

/**
 * Projects pinned to the top of the viewer's own sidebar, oldest pin first. Kept on the server
 * per user; a pin or unpin shows at once, before the sidebar's data comes back with it.
 */
const pending = new SvelteMap<string, number | null>();

const keyOf = (entity: string, name: string) => `${entity}/${name}`;

/** When the viewer pinned the project (seconds), or null. */
export function pinnedAt(p: Pick<ProjectInfo, 'entity' | 'name' | 'pinned_at'>): number | null {
	const key = keyOf(p.entity, p.name);
	return pending.has(key) ? pending.get(key)! : (p.pinned_at ?? null);
}

/** Pinned projects first, in pin order, then the rest in the order given. */
export function pinnedFirst<T extends Pick<ProjectInfo, 'entity' | 'name' | 'pinned_at'>>(
	projects: T[]
): T[] {
	const pinned = projects
		.filter((p) => pinnedAt(p) != null)
		.sort((a, b) => pinnedAt(a)! - pinnedAt(b)!);
	return [...pinned, ...projects.filter((p) => pinnedAt(p) == null)];
}

export async function setPinned(entity: string, name: string, on: boolean) {
	const key = keyOf(entity, name);
	pending.set(key, on ? Date.now() / 1000 : null);
	try {
		const res = await fetch(
			`/api/projects/${encodeURIComponent(entity)}/${encodeURIComponent(name)}/pin`,
			{ method: on ? 'PUT' : 'DELETE' }
		);
		if (!res.ok) throw new Error();
		await invalidateAll();
	} catch {
		toast.error(on ? `Couldn’t pin ${name}` : `Couldn’t unpin ${name}`);
	} finally {
		pending.delete(key);
	}
}
