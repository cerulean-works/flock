import type { ResolvedPathname } from '$app/types';
import type { Crumb } from './storage.ts';

/** Datasets, Models and Storage, as the sidebar names them; an org's are at `/<tab>/<org>`. */
export const TABS: Record<string, string> = {
	datasets: 'Datasets',
	models: 'Models',
	storage: 'Storage'
};
/** A project's pages, as its sidebar names them. */
const PAGES: Record<string, string> = { runs: 'Runs', groups: 'Groups', ...TABS };

/**
 * Where a missing address sits, for its 404's header: the steps up to the last part before the
 * missing one (`/datasets/acme/-/a/gone` → Datasets › a). The org, and a project, lead the
 * header by themselves, so they're left out. A `-` splits a path off and is no step. Null when
 * nothing above the address is known.
 */
export function missingTrail(pathname: string): Crumb[] | null {
	const parts = pathname.split('/').filter(Boolean);
	parts.pop();
	const out: Crumb[] = [];
	const at = (n: number) => `/${parts.slice(0, n + 1).join('/')}` as ResolvedPathname;
	// Settings: /orgs/<org>/<section>, /account/<section>, /admin/<section>.
	const SETTINGS: Record<string, string> = { account: 'Account', admin: 'Super admin' };
	if (parts[0] in SETTINGS) return [{ label: SETTINGS[parts[0]], href: at(0) }];
	if (parts[0] === 'orgs') return parts.length < 2 ? null : [{ label: 'Settings', href: at(1) }];
	// The tabs: /<tab>/<org>/…; the workspace: /<org>/<project>/<page>/….
	const tab = parts[0] in TABS;
	if (tab) {
		if (parts.length < 2) return null;
		out.push({ label: TABS[parts[0]], href: at(1) });
	}
	for (let i = 2; i < parts.length; i++) {
		if (parts[i] === '-') continue;
		const label = decodeURIComponent(parts[i]);
		out.push({ label: !tab && i === 2 ? (PAGES[label] ?? label) : label, href: at(i) });
	}
	return out.length ? out : null;
}

/**
 * What a 404 says: "No <thing> at this address", the thing named from the error's message
 * ("folder -/a/b not found", "settings page nope not found"), or "page" when it names none.
 */
export function missingHeading(message: string | undefined): string {
	const named = message && /^(.+?) \S+ not found$/i.exec(message);
	return `No ${named ? named[1].toLowerCase() : 'page'} at this address`;
}
