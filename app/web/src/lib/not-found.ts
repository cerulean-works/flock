import type { ResolvedPathname } from '$app/types';
import type { Crumb } from './storage.ts';

/** Datasets, Models and Storage, as the sidebar names them; an org's are at `/<tab>/<org>`. */
const TABS: Record<string, string> = { datasets: 'Datasets', models: 'Models', storage: 'Storage' };
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
