import { json } from '@sveltejs/kit';
import type { Handle } from '@sveltejs/kit/hooks';
import { immutable } from '$app/manifest';
import { loginHref, resolveSession } from '#lib/server/session.ts';

// SvelteKit's client start awaits the root error component (node 1) alongside the root layout,
// but only modulepreloads the nodes of the rendered route. Without this, that chunk is fetched
// only after `start.js` has run: one extra round trip before hydration.
const errorNode = immutable.find((f) => /(^|\/)nodes\/1\.[\w-]+\.js$/.test(f.path));
const errorPreload = errorNode ? `<link rel="modulepreload" href="/${errorNode.path}">` : '';

/** Pages and endpoints for people who aren't signed in yet. */
const PUBLIC = [
	'/login',
	'/setup',
	'/invite',
	'/reset',
	'/auth/google',
	'/api/v1/auth/google/',
	// Pictures on public pages (an invitation's organization); the digest is the permission.
	'/api/v1/avatars/'
];
const isPublic = (path: string) =>
	PUBLIC.some((p) => path === p || path.startsWith(p.endsWith('/') ? p : `${p}/`));

export const handle: Handle = async ({ event, resolve }) => {
	// Browsers probe for it whatever the page links (ours is an SVG); without this it would
	// match `/[entity]` and look up an organization called "favicon.ico".
	if (event.url.pathname === '/favicon.ico') return new Response(null, { status: 404 });

	const session = await resolveSession(event.cookies);
	event.locals.token = session.token;
	event.locals.user = session.user;

	// Signed out: pages go to sign in (which sends a fresh server's first visitor on to
	// setup), this server's API answers 401. Data requests pass: their server loads hit the
	// flock's 401, which redirects in a form the client router follows.
	const path = event.url.pathname;
	if (!event.locals.token && !isPublic(path) && !event.isDataRequest) {
		if (path.startsWith('/api/')) return json({ message: 'Sign in to continue' }, { status: 401 });
		const location = loginHref(event.url);
		// A form action sent with `use:enhance` expects an action result, not an HTML redirect.
		if (event.request.headers.get('x-sveltekit-action') === 'true')
			return json({ type: 'redirect', status: 303, location });
		return new Response(null, { status: 303, headers: { location } });
	}

	// Preload fonts alongside JS and CSS so they download in parallel with the document.
	return resolve(event, {
		preload: ({ type }) => type === 'js' || type === 'css' || type === 'font',
		transformPageChunk: errorPreload
			? ({ html }) => html.replace('</head>', `${errorPreload}</head>`)
			: undefined
	});
};
