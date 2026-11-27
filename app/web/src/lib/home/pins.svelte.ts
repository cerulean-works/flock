/**
 * Plots pinned to Home: one metric of one run each, kept in this browser (H8). Pinned from a
 * run's chart menu, the command menu, or Home's own Add plot tile.
 */
export interface Pin {
	org: string;
	project: string;
	run: string;
	/** The run's name when it was pinned, shown until its curve loads. */
	label: string;
	metric: string;
}

const KEY = 'flock:home-pins';

const same = (a: Pin, b: Omit<Pin, 'label'>) =>
	a.org === b.org && a.project === b.project && a.run === b.run && a.metric === b.metric;

function read(): Pin[] {
	try {
		const raw = JSON.parse(localStorage.getItem(KEY) ?? '[]');
		return Array.isArray(raw) ? raw : [];
	} catch {
		return [];
	}
}

class Pins {
	// Read once, as the module loads in the browser: never from inside a derived. The server has
	// none (Home shows them only once mounted).
	#all = $state<Pin[]>(typeof localStorage === 'undefined' ? [] : read());
	#save() {
		try {
			localStorage.setItem(KEY, JSON.stringify(this.#all));
		} catch {
			// Private windows: pins last for the visit.
		}
	}

	/** The org's pins, oldest first. */
	of(org: string): Pin[] {
		return this.#all.filter((p) => p.org === org);
	}
	has(pin: Omit<Pin, 'label'>) {
		return this.#all.some((p) => same(p, pin));
	}
	add(pin: Pin) {
		if (this.has(pin)) return;
		this.#all = [...this.#all, pin];
		this.#save();
	}
	remove(pin: Omit<Pin, 'label'>) {
		this.#all = this.#all.filter((p) => !same(p, pin));
		this.#save();
	}
	toggle(pin: Pin) {
		if (this.has(pin)) this.remove(pin);
		else this.add(pin);
	}
}

export const pins = new Pins();

/** The metric a run is best read by: its loss, else its first curve. */
export function mainMetric(keys: string[]): string | null {
	const shown = keys.filter((k) => !k.startsWith('_') && !k.startsWith('system'));
	const rank = (k: string) =>
		/^(train\/)?loss$/.test(k) ? 0 : /train.*loss|loss.*train/.test(k) ? 1 : /loss/.test(k) ? 2 : 3;
	return [...shown].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))[0] ?? null;
}

/**
 * The curves a pinned tile steps through while idle (Harry: "idle animation to switch between
 * metrics"): the one pinned, then the run's other telling ones (losses, learning rate, gradient
 * norm, accuracy), at most `max`.
 */
export function cycleMetrics(keys: string[], pinned: string, max = 4): string[] {
	const shown = keys.filter((k) => !k.startsWith('_') && !k.startsWith('system'));
	const rank = (k: string) =>
		/^(train\/)?loss$/.test(k)
			? 0
			: /^(eval|val)[^/]*\/loss$|^val_loss$/.test(k)
				? 1
				: /(^|\/)(lr|learning_rate)$/.test(k)
					? 2
					: /grad.?norm/.test(k)
						? 3
						: /acc/.test(k)
							? 4
							: /loss/.test(k)
								? 5
								: 9;
	const rest = shown
		.filter((k) => k !== pinned && rank(k) < 9)
		.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
	return [pinned, ...rest].slice(0, max);
}
