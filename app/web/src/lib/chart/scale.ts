export interface Ticks {
	values: number[];
	step: number;
}

/** "Nice" linear ticks covering [lo, hi], roughly `count` of them. */
export function niceTicks(lo: number, hi: number, count = 4): Ticks {
	if (!(hi > lo)) return { values: [lo], step: 1 };
	const raw = (hi - lo) / count;
	const mag = 10 ** Math.floor(Math.log10(raw));
	const norm = raw / mag;
	const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
	return ticksEvery(lo, hi, step);
}

function ticksEvery(lo: number, hi: number, step: number): Ticks {
	const values: number[] = [];
	const start = Math.ceil(lo / step - 1e-9);
	for (let i = start; i * step <= hi + step * 1e-9; i++) values.push(+(i * step).toPrecision(12));
	return { values, step };
}

/** Tick spacings for a time axis in seconds: round seconds, minutes and hours. */
const TIME_STEPS = [
	1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10_800, 21_600, 43_200, 86_400
];

/** Ticks for an axis in seconds, on clock-friendly boundaries; days step like `niceTicks`. */
export function timeTicks(lo: number, hi: number, count = 4): Ticks {
	const raw = (hi - lo) / count;
	if (!(raw >= 1)) return niceTicks(lo, hi, count);
	const step =
		TIME_STEPS.find((s) => s >= raw / 1.5) ?? niceTicks(0, (hi - lo) / 86_400, count).step * 86_400;
	return ticksEvery(lo, hi, step);
}

/**
 * Ticks for an axis of epoch seconds, on round times of the viewer's clock: `tz` is its offset
 * from UTC in minutes, as `Date.getTimezoneOffset` gives it (positive west of UTC).
 */
export function wallTicks(
	lo: number,
	hi: number,
	count = 4,
	tz = new Date(lo * 1000).getTimezoneOffset()
): Ticks {
	const shift = -tz * 60;
	const { values, step } = timeTicks(lo + shift, hi + shift, count);
	return { values: values.map((v) => v - shift), step };
}

/** Pad a data extent so lines never touch the plot frame. */
export function niceDomain(lo: number, hi: number): [number, number] {
	if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [0, 1];
	if (hi - lo < Math.abs(hi) * 1e-9 || hi === lo) {
		const d = Math.abs(hi) * 0.1 || 1;
		return [lo - d, hi + d];
	}
	const pad = (hi - lo) * 0.04;
	return [lo - pad, hi + pad];
}

/** Ticks for a log10 axis whose domain is given in log10 units. */
export function logTicks(lo: number, hi: number): Ticks {
	const values: number[] = [];
	for (let e = Math.ceil(lo); e <= Math.floor(hi); e++) values.push(e);
	if (values.length < 2) return niceTicks(lo, hi, 3);
	while (values.length > 6) {
		for (let i = values.length - 2; i > 0; i -= 2) values.splice(i, 1);
	}
	return { values, step: 1 };
}
