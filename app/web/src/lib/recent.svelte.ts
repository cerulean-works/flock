/**
 * Pages opened lately (runs, projects, groups, datasets, models, buckets), newest first, kept
 * in this browser: the command field lists them first (Harry: recent ranked higher).
 */
export interface Visit {
	href: string;
	label: string;
	/** What it is, for its icon. */
	kind: 'run' | 'project' | 'group' | 'dataset' | 'model' | 'storage';
	/** Where it lives, shown muted beside it. */
	detail?: string;
}

const KEY = 'flock:recent';
const MAX = 8;

function read(): Visit[] {
	try {
		const raw = JSON.parse(localStorage.getItem(KEY) ?? '[]');
		return Array.isArray(raw) ? raw : [];
	} catch {
		return [];
	}
}

class Recent {
	all = $state<Visit[]>(typeof localStorage === 'undefined' ? [] : read());

	record(visit: Visit) {
		this.all = [visit, ...this.all.filter((v) => v.href !== visit.href)].slice(0, MAX);
		try {
			localStorage.setItem(KEY, JSON.stringify(this.all));
		} catch {
			// Kept for the visit.
		}
	}
}

export const recent = new Recent();

/** The kind of page a route is, if it's one worth going back to. */
export function visitKind(route: string | null): Visit['kind'] | null {
	if (!route) return null;
	if (route.includes('/runs/[run]')) return 'run';
	if (route.includes('/groups/[group]')) return 'group';
	if (route === '/(app)/[entity]/[project]') return 'project';
	if (route.startsWith('/(app)/datasets/')) return 'dataset';
	if (route.startsWith('/(app)/models/')) return 'model';
	if (route.startsWith('/(app)/storage/')) return 'storage';
	return null;
}
