import { projectPath, flock } from '#lib/server/flock.ts';
import type { RequestHandler } from './$types';

/**
 * Flock's live event stream for the runs on a page, passed straight through. When the
 * browser disconnects, SvelteKit cancels the body, which closes the upstream connection and
 * unsubscribes on Flock.
 */
export const GET: RequestHandler = async ({ params, url, request }) => {
	const runs = url.searchParams.get('runs') ?? '';
	const res = await flock(
		`${projectPath(params.entity, params.project)}/stream?system=true&runs=${encodeURIComponent(runs)}`,
		{ signal: request.signal, headers: { accept: 'text/event-stream' } }
	);
	return new Response(res.body, {
		headers: {
			'content-type': 'text/event-stream',
			'cache-control': 'no-cache',
			'x-accel-buffering': 'no'
		}
	});
};
