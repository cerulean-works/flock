import { flockAnswer, projectPath } from '#lib/server/flock.ts';
import type { RequestHandler } from './$types';

/** Pin the project to the top of the viewer's own sidebar (PUT), or unpin it (DELETE). */
const pin: RequestHandler = async ({ params, request }) => {
	const res = await flockAnswer(`${projectPath(params.entity, params.project)}/pin`, {
		method: request.method
	});
	return new Response(res.status === 204 ? null : res.body, {
		status: res.status,
		headers: res.status === 204 ? {} : { 'content-type': 'application/json' }
	});
};

export const PUT = pin;
export const DELETE = pin;
