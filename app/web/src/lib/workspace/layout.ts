/**
 * How a viewer arranged a project's charts, as kept in this browser. Pure helpers, so the
 * ordering rules can be tested without a workspace.
 */

/** How the viewer arranged a project's panels: section order, and per section its order and plot height. */
export interface PanelLayout {
	/** Section keys in the order they were arranged; sections logged since go after them. */
	sections: readonly string[];
	/** Metric keys in the order they were arranged; metrics logged since go after them. */
	order: Readonly<Record<string, readonly string[]>>;
	height: Readonly<Record<string, number>>;
}

export const EMPTY_LAYOUT: PanelLayout = { sections: [], order: {}, height: {} };

const isRecord = (v: unknown): v is Record<string, unknown> =>
	!!v && typeof v === 'object' && !Array.isArray(v);

const strings = (v: unknown): string[] =>
	Array.isArray(v) ? v.filter((s): s is string => typeof s === 'string') : [];

/**
 * A layout read back from storage. Anything malformed is dropped rather than trusted, and
 * layouts saved before sections could be moved (no `sections`) keep the default section order.
 */
export function parseLayout(raw: unknown): PanelLayout {
	if (!isRecord(raw)) return EMPTY_LAYOUT;
	const order: Record<string, string[]> = {};
	if (isRecord(raw.order))
		for (const [k, v] of Object.entries(raw.order)) if (Array.isArray(v)) order[k] = strings(v);
	const height: Record<string, number> = {};
	if (isRecord(raw.height))
		for (const [k, v] of Object.entries(raw.height))
			if (typeof v === 'number' && Number.isFinite(v)) height[k] = v;
	return { sections: strings(raw.sections), order, height };
}

/**
 * `items` in a saved order: those named in `order` first, as ordered there; the rest after
 * them in their own (default) order.
 */
export function arrange<T>(
	items: readonly T[],
	keyOf: (item: T) => string,
	order: readonly string[] | undefined
): T[] {
	if (!order?.length) return [...items];
	const rank = new Map(order.map((k, i) => [k, i]));
	const at = (item: T) => rank.get(keyOf(item)) ?? Infinity;
	// A stable sort: unranked items (all Infinity) keep their order.
	return items.toSorted((a, b) => at(a) - at(b));
}

/** `keys` with the one at `from` moved to `to` (clamped to the list). */
export function moveKey(keys: readonly string[], from: number, to: number): string[] {
	const out = [...keys];
	if (from < 0 || from >= out.length) return out;
	to = Math.max(0, Math.min(out.length - 1, to));
	out.splice(to, 0, ...out.splice(from, 1));
	return out;
}

/**
 * The order to save once the keys shown here were rearranged to `arranged`. Keys saved before
 * that aren't shown here (a run's page shows only its own sections) keep their places; the
 * shown ones fill their slots in the new order.
 */
export function mergeOrder(saved: readonly string[], arranged: readonly string[]): string[] {
	const shown = new Set(arranged);
	const full = [...saved.filter((k, i) => saved.indexOf(k) === i)];
	for (const k of arranged) if (!full.includes(k)) full.push(k);
	let next = 0;
	return full.map((k) => (shown.has(k) ? arranged[next++] : k));
}
