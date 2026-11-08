import { json } from '@sveltejs/kit';
import type { Handle } from '@sveltejs/kit/hooks';
import { immutable } from '$app/manifest';
import { loginHref, resolveSession } from '#lib/server/session.ts';
import { parseTheme, THEME_COOKIE } from '#lib/theme.ts';

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

/** The first segment of the app's pages that are only for people signed in. */
const SIGNED_IN_PAGES = new Set([
	'account',
	'admin',
	'orgs',
	'storage',
	'models',
	'device',
	'logout'
]);
/** This server's endpoints that the readable pages call from the browser (reads only). */
const READABLE_API = ['/api/projects', '/api/datasets'];

/**
 * What a signed-out visitor may open, for what's public in it (015): an org's page
 * (`/{entity}`), its projects and runs (`/{entity}/{project}/…`) and its datasets
 * (`/datasets/{org}/…`), with the endpoints they call. Flock decides what each one shows; a
 * private one is a 404, which sends the visitor to sign in (see `flock()` and below).
 */
function isReadable(path: string, method: string) {
	// Writes signed out get this server's 401, not Flock's, which would redirect to sign in.
	if (path.startsWith('/api/'))
		return method === 'GET' && READABLE_API.some((p) => path === p || path.startsWith(`${p}/`));
	const first = path.split('/')[1];
	return !!first && !SIGNED_IN_PAGES.has(first);
}

export const handle: Handle = async ({ event, resolve }) => {
	// Browsers probe for it whatever the page links (ours is an SVG); without this it would
	// match `/[entity]` and look up an organization called "favicon.ico".
	if (event.url.pathname === '/favicon.ico') return new Response(null, { status: 404 });

	const session = await resolveSession(event.cookies);
	event.locals.token = session.token;
	event.locals.user = session.user;
	event.locals.theme = parseTheme(event.cookies.get(THEME_COOKIE));

	// Signed out: pages go to sign in (which sends a fresh server's first visitor on to
	// setup), this server's API answers 401. Data requests pass: their server loads hit the
	// flock's 401, which redirects in a form the client router follows. Pages that may be
	// public pass too.
	const path = event.url.pathname;
	const signedOut = !event.locals.token;
	if (
		signedOut &&
		!isPublic(path) &&
		!isReadable(path, event.request.method) &&
		!event.isDataRequest
	) {
		if (path.startsWith('/api/')) return json({ message: 'Sign in to continue' }, { status: 401 });
		const location = loginHref(event.url);
		// A form action sent with `use:enhance` expects an action result, not an HTML redirect.
		if (event.request.headers.get('x-sveltekit-action') === 'true')
			return json({ type: 'redirect', status: 303, location });
		return new Response(null, { status: 303, headers: { location } });
	}

	// Preload JS and CSS so they download in parallel with the document. The font comes from
	// Google Fonts, linked with a preconnect in app.html. The appearance goes on <html> here, so
	// a chosen light or dark page never flashes the other (see #lib/theme.ts).
	const theme = event.locals.theme;
	const response = await resolve(event, {
		preload: ({ type }) => type === 'js' || type === 'css',
		transformPageChunk: ({ html }) =>
			html
				.replace('%flock.theme%', theme)
				.replace('%flock.dark%', theme === 'dark' ? 'dark' : '')
				.replace('</head>', `${errorPreload}</head>`)
	});
	// A page that isn't there, opened signed out (one no route matches; Flock's 404s redirect
	// in `flock()`): it may be private, so sign in and come back (D116).
	if (
		signedOut &&
		response.status === 404 &&
		event.request.method === 'GET' &&
		!event.isDataRequest &&
		!path.startsWith('/api/') &&
		!isPublic(path)
	)
		return new Response(null, { status: 303, headers: { location: loginHref(event.url) } });
	return response;
};
