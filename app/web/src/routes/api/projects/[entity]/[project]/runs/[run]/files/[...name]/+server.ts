import { projectPath, flock } from '#lib/server/flock.ts';
import type { RequestHandler } from './$types';

/** Types shown in the browser; anything else (HTML a run uploaded, say) is downloaded. */
const INLINE = /^(text\/plain|application\/json|image\/(png|jpeg|gif|webp))\b/;

/**
 * One of a run's files. They're whatever the run uploaded, served from the dashboard's own
 * origin, so nothing in them may run: no sniffing, a sandboxing CSP, and a download for any
 * type a browser would render as a page.
 */
export const GET: RequestHandler = async ({ params }) => {
	const name = params.name.split('/').map(encodeURIComponent).join('/');
	const res = await flock(
		`${projectPath(params.entity, params.project)}/runs/${encodeURIComponent(params.run)}/files/${name}`
	);
	const type = res.headers.get('content-type') ?? 'application/octet-stream';
	const file = params.name.split('/').pop()!;
	return new Response(res.body, {
		headers: {
			'content-type': INLINE.test(type) ? type : 'application/octet-stream',
			'content-disposition': `${INLINE.test(type) ? 'inline' : 'attachment'}; filename*=UTF-8''${encodeURIComponent(file)}`,
			'x-content-type-options': 'nosniff',
			'content-security-policy': "default-src 'none'; sandbox",
			'cache-control': 'private, no-cache'
		}
	});
};
