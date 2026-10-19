/**
 * Frame-budgeted draw queue.
 *
 * Redrawing every panel at once (e.g. after toggling a run with 100 runs × 18 panels mounted)
 * would block a frame for tens of milliseconds. Instead, panels enqueue their base-layer draw:
 *   - `urgent` tasks (panels on screen) run in animation frames, ~8ms of work per frame;
 *   - `idle` tasks (panels off screen or behind the table) run in idle time, so the charts are
 *     already painted by the time the user switches to them.
 * A key holds at most one pending task; re-scheduling replaces it, so stale draws never run.
 */

type Task = () => void;

/** Performance mark set whenever every on-screen chart is current. */
export const PAINTED = 'charts:painted';

const urgent = new Map<object, Task>();
const idle = new Map<object, Task>();
let frameQueued = false;
let idleQueued = false;

const FRAME_BUDGET = 8;

type Deadline = { timeRemaining(): number; didTimeout?: boolean };

const ric: (cb: (d: Deadline) => void) => void =
	typeof requestIdleCallback === 'function'
		? (cb) => requestIdleCallback(cb, { timeout: 500 })
		: (cb) => setTimeout(() => cb({ timeRemaining: () => 8 }), 16);

function runFrame() {
	frameQueued = false;
	const start = performance.now();
	for (const [key, task] of urgent) {
		urgent.delete(key);
		task();
		if (performance.now() - start > FRAME_BUDGET) break;
	}
	if (urgent.size) queueFrame();
	else {
		// Every on-screen chart is current; lets performance tooling time redraws end to end.
		performance.clearMarks(PAINTED);
		performance.mark(PAINTED);
	}
}

function runIdle(deadline: Deadline) {
	idleQueued = false;
	// A timed-out callback has no idle time left; still make progress, one task at a time.
	let budget = deadline.didTimeout ? 1 : Infinity;
	for (const [key, task] of idle) {
		if (budget-- <= 0 || (!deadline.didTimeout && deadline.timeRemaining() < 4)) break;
		idle.delete(key);
		task();
	}
	if (idle.size) queueIdle();
}

function queueFrame() {
	if (frameQueued) return;
	frameQueued = true;
	requestAnimationFrame(runFrame);
}

function queueIdle() {
	if (idleQueued) return;
	idleQueued = true;
	ric(runIdle);
}

export function schedule(key: object, task: Task, priority: 'urgent' | 'idle') {
	if (priority === 'urgent') {
		idle.delete(key);
		urgent.set(key, task);
		queueFrame();
	} else if (urgent.has(key)) {
		urgent.set(key, task);
	} else {
		idle.set(key, task);
		queueIdle();
	}
}

/** Promote a pending idle task (e.g. a panel just scrolled into view). */
export function promote(key: object) {
	const task = idle.get(key);
	if (task) schedule(key, task, 'urgent');
}

export function cancel(key: object) {
	urgent.delete(key);
	idle.delete(key);
}
