import { browser } from '$app/env';
import { error } from '@sveltejs/kit';
import type { RunQuery, WorkspaceData } from '#lib/types.ts';
import { base64ToBytes } from '#lib/utils.ts';
import { runQueryString } from '#lib/workspace/url.ts';
import { decodeWorkspace } from './snapshot.ts';

/**
 * Client-side data cache.
 *
 * SvelteKit's own preloading keeps a single entry, which is not enough to keep neighbouring
 * pages and sort orders warm at the same time. Universal loads go through this cache instead:
 * on the server it is bypassed (every request renders fresh and SvelteKit inlines the response
 * into the HTML); in the browser the first call per URL hydrates from that inlined response,
 * and later calls — navigations, hover preloads, idle prefetches — share one in-flight promise.
 *
 * Pages are mutated in place by live updates while shown, and carry per-run history cursors,
 * so a cached page shown again catches up from where it left off (see #lib/data/live.ts).
 *
 * Encoding: the request made during SSR (and replayed once during hydration) asks for base64
 * text. SvelteKit inlines text verbatim and the browser decodes it natively in a couple of
 * milliseconds; inlining binary instead goes through a per-byte JS loop that costs hundreds of
 * milliseconds while the JIT is still cold. Every later request fetches raw binary.
 */

type Fetch = typeof fetch;

const MAX_PAGES = 6;
const pages = new Map<string, Promise<WorkspaceData>>();

async function checked(res: Response) {
	if (!res.ok) {
		const message = await res
			.json()
			.then((body: { message?: string }) => body.message)
			.catch(() => undefined);
		error(res.status as 400, message || res.statusText);
	}
	return res;
}

export const projectUrl = (entity: string, project: string) =>
	`/api/projects/${encodeURIComponent(entity)}/${encodeURIComponent(project)}`;

export const membersUrl = (org: string) => `/api/orgs/${encodeURIComponent(org)}/members`;

export function runPageUrl(entity: string, project: string, q: RunQuery) {
	return `${projectUrl(entity, project)}/runs?${runQueryString(q)}`;
}

/** The hydration replay must request exactly what SSR requested, so it is tracked explicitly. */
let hydrating = browser;

export function loadRunPage(
	fetchFn: Fetch,
	entity: string,
	project: string,
	q: RunQuery
): Promise<WorkspaceData> {
	const url = runPageUrl(entity, project, q);
	const inline = !browser || hydrating;
	hydrating = false;
	const run = () =>
		inline
			? fetchFn(`${url}&enc=b64`)
					.then(checked)
					.then((r) => r.text())
					.then((text) => decodeWorkspace(base64ToBytes(text).buffer, q))
			: fetchFn(url)
					.then(checked)
					.then((r) => r.arrayBuffer())
					.then((buf) => decodeWorkspace(buf, q));
	if (!browser) return run();

	let hit = pages.get(url);
	if (hit) {
		pages.delete(url); // refresh LRU position
	} else {
		hit = run();
		hit.catch(() => pages.delete(url));
	}
	pages.set(url, hit);
	while (pages.size > MAX_PAGES) pages.delete(pages.keys().next().value!);
	return hit;
}

export function isRunPageCached(entity: string, project: string, q: RunQuery) {
	return pages.has(runPageUrl(entity, project, q));
}

/** Warm the cache for a page without navigating. Failures are ignored; navigation will retry. */
export function prefetchRunPage(entity: string, project: string, q: RunQuery) {
	if (!browser || isRunPageCached(entity, project, q)) return;
	loadRunPage(fetch, entity, project, q).catch(() => {});
}

/** Forget every cached page, e.g. when new runs shift page boundaries. */
export function clearRunPages() {
	pages.clear();
}
