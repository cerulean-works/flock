/**
 * A runs view's three views: the table, the charts beside the names, or both side by side
 * (`visualize`: the table on the left, the charts on the right).
 */
export type ProjectView = 'table' | 'visualize' | 'charts';

export const isProjectView = (v: unknown): v is ProjectView =>
	v === 'table' || v === 'visualize' || v === 'charts';

/** Holds the view last picked, so the server renders the next project in it too. */
export const VIEW_COOKIE = 'flock_project_view';

/** The view a `VIEW_COOKIE` holds: table unless it says another. */
export const cookieView = (value: string | undefined): ProjectView =>
	isProjectView(value) ? value : 'table';

/** The view last picked in this tab; until then the layout's, read from the cookie. */
let picked = $state<ProjectView | null>(null);

/** The view a project opens in when its URL doesn't say: the one picked last, on any project. */
export function lastView(fromCookie: ProjectView): ProjectView {
	return picked ?? fromCookie;
}

export function rememberView(view: ProjectView) {
	picked = view;
	document.cookie = `${VIEW_COOKIE}=${view}; path=/; max-age=31536000; samesite=lax`;
}
