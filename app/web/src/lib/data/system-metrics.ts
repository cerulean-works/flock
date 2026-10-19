/**
 * W&B-style charts for the system metrics wandb samples: one chart per measure, one line per
 * device (GPU, core, disk), titled as the W&B web app titles them.
 *
 * Keys are wandb's (`symon/src/*.rs`, `core/internal/leet/systemmetrics.go`, and the System
 * Metrics reference in wandb/docs); titles and legend labels follow the W&B workspace.
 */
import type { MetricFormat } from '#lib/types.ts';

export interface SystemSeries {
	/** The full Flock key, e.g. `system.gpu.0.nvlinkTxBytes`. */
	key: string;
	/** Legend label, as W&B shows it, e.g. `GPU 0 NVLink Tx (bytes)`. */
	label: string;
}

export interface SystemChart {
	/** Stable id for the chart, independent of how many GPUs/disks there are, e.g. `gpu.nvlinkTxBytes`. */
	id: string;
	/** Human-readable chart title, as W&B shows it, e.g. `GPU NVLink Tx Bytes`. */
	title: string;
	/** Coarse group for ordering/nesting: `GPU`, `CPU`, `Memory`, `Disk`, `Network`, `Process`, or `Other`. */
	group: string;
	format: MetricFormat;
	/** Lines in the chart, ordered by device index (numerically: GPU 2 before GPU 10). */
	series: SystemSeries[];
}

/** Chart groups, in display order. `Process` is the run's own process tree (`cpu`, `proc.*`). */
export const SYSTEM_GROUPS = [
	'GPU',
	'CPU',
	'Memory',
	'Disk',
	'Network',
	'Process',
	'Other'
] as const;
export type SystemGroup = (typeof SYSTEM_GROUPS)[number];

interface ChartDef {
	id: string;
	title: string;
	group: SystemGroup;
	format: MetricFormat;
}

interface Measure {
	chart: ChartDef;
	/** The legend label for a device (a GPU index, a disk path); `''` for single-series keys. */
	label: (device: string) => string;
	/** Orders the series of one device, as read before write. */
	sub: number;
}

/** Known charts, in W&B's display order within each group. */
const KNOWN: ChartDef[] = [];
/** Measures by key pattern: the key itself, `gpu.#.temp` for indexed keys, `disk.*.usageGB` for paths. */
const MEASURES = new Map<string, Measure>();

function define(
	id: string,
	title: string,
	group: SystemGroup,
	format: MetricFormat,
	series: [pattern: string, label: (device: string) => string][]
) {
	const chart = { id, title, group, format };
	KNOWN.push(chart);
	series.forEach(([pattern, label], sub) => MEASURES.set(pattern, { chart, label, sub }));
}

/** A single-series chart for one key. */
const single = (key: string, title: string, group: SystemGroup, format: MetricFormat) =>
	define(key, title, group, format, [[key, () => title]]);

/** Per-GPU measures: metric, chart title, legend label after `GPU {i}`, format. */
const GPU: [string, string, string, MetricFormat][] = [
	['gpu', 'GPU Utilization (%)', 'Utilization (%)', 'pct'],
	['temp', 'GPU Temperature (℃)', 'Temperature (℃)', 'num'],
	['powerWatts', 'GPU Power Usage (W)', 'Power Usage (W)', 'num'],
	['powerPercent', 'GPU Power Usage (%)', 'Power Usage (%)', 'pct'],
	['enforcedPowerLimitWatts', 'GPU Enforced Power Limit (W)', 'Enforced Power Limit (W)', 'num'],
	['smClock', 'GPU Streaming Multiprocessor (SM) Clock Speed (MHz)', 'SM Clock Speed (MHz)', 'int'],
	['memoryClock', 'GPU Memory Clock Speed (MHz)', 'Memory Clock Speed (MHz)', 'int'],
	['graphicsClock', 'GPU Graphics Clock Speed (MHz)', 'Graphics Clock Speed (MHz)', 'int'],
	['freq', 'GPU Frequency (MHz)', 'Frequency (MHz)', 'int'],
	['memoryAllocatedBytes', 'GPU Memory Allocated (Bytes)', 'Memory Allocated (bytes)', 'si'],
	['memoryAllocated', 'GPU Memory Allocated (%)', 'Memory Allocated (%)', 'pct'],
	['memoryUsed', 'GPU Memory Used (Bytes)', 'Memory Used (bytes)', 'si'],
	['memoryUsedBytes', 'GPU Memory Used (Bytes)', 'Memory Used (bytes)', 'si'],
	['memory', 'GPU Time Spent Accessing Memory (%)', 'Time Spent Accessing Memory (%)', 'pct'],
	[
		'memoryReadWriteActivity',
		'GPU Memory Read/Write Activity (%)',
		'Memory Read/Write Activity (%)',
		'pct'
	],
	['memoryOverDrive', 'GPU Memory OverDrive (%)', 'Memory OverDrive (%)', 'pct'],
	['correctedMemoryErrors', 'GPU Corrected Memory Errors', 'Corrected Memory Errors', 'int'],
	['uncorrectedMemoryErrors', 'GPU Uncorrected Memory Errors', 'Uncorrected Memory Errors', 'int'],
	['encoderUtilization', 'GPU Encoder Utilization (%)', 'Encoder Utilization (%)', 'pct'],
	['fanSpeed', 'GPU Fan Speed (%)', 'Fan Speed (%)', 'pct'],
	['energyJoules', 'GPU Energy (J)', 'Energy (J)', 'si'],
	['recoveryCount', 'GPU Recovery Count', 'Recovery Count', 'int'],
	['pcieLinkGen', 'GPU PCIe Link Gen', 'PCIe Link Gen', 'int'],
	['maxPcieLinkGen', 'GPU Max PCIe Link Gen', 'Max PCIe Link Gen', 'int'],
	['pcieLinkSpeed', 'GPU PCIe Link Speed (bits/s)', 'PCIe Link Speed (bits/s)', 'si'],
	['pcieLinkWidth', 'GPU PCIe Link Width', 'PCIe Link Width', 'int'],
	['maxPcieLinkWidth', 'GPU Max PCIe Link Width', 'Max PCIe Link Width', 'int'],
	// NVML GPM / DCGM profiling metrics: titles from the key, as W&B derives them.
	['pcieTxBytes', 'GPU PCIe Tx Bytes', 'PCIe Tx (bytes)', 'si'],
	['pcieRxBytes', 'GPU PCIe Rx Bytes', 'PCIe Rx (bytes)', 'si'],
	['nvlinkTxBytes', 'GPU NVLink Tx Bytes', 'NVLink Tx (bytes)', 'si'],
	['nvlinkRxBytes', 'GPU NVLink Rx Bytes', 'NVLink Rx (bytes)', 'si'],
	['smActive', 'GPU SM Active', 'SM Active (%)', 'pct'],
	['smOccupancy', 'GPU SM Occupancy', 'SM Occupancy (%)', 'pct'],
	['dramActive', 'GPU DRAM Active', 'DRAM Active (%)', 'pct'],
	['pipeTensorActive', 'GPU Pipe Tensor Active', 'Pipe Tensor Active (%)', 'pct'],
	['pipeTensorHmmaActive', 'GPU Pipe Tensor HMMA Active', 'Pipe Tensor HMMA Active (%)', 'pct'],
	['pipeFp64Active', 'GPU Pipe FP64 Active', 'Pipe FP64 Active (%)', 'pct'],
	['pipeFp32Active', 'GPU Pipe FP32 Active', 'Pipe FP32 Active (%)', 'pct'],
	['pipeFp16Active', 'GPU Pipe FP16 Active', 'Pipe FP16 Active (%)', 'pct']
];

/** Other spellings of GPU metrics (the reference docs spell this one so). */
const GPU_ALIASES: Record<string, string> = { unCorrectedMemoryErrors: 'uncorrectedMemoryErrors' };

/** Indexed key families (`{family}.{i}.{metric}`): legend name and group. */
const FAMILIES: Record<string, { name: string; group: SystemGroup }> = {
	gpu: { name: 'GPU', group: 'GPU' },
	'gpu.process': { name: 'Process GPU', group: 'GPU' },
	cpu: { name: 'CPU', group: 'CPU' },
	tpu: { name: 'TPU', group: 'Other' },
	trn: { name: 'NeuronCore', group: 'Other' }
};

// Charts sharing a title (`memoryUsed`, `memoryUsedBytes`) share a chart.
for (const family of ['gpu', 'gpu.process']) {
	const byTitle = new Map<string, ChartDef>();
	const { name } = FAMILIES[family];
	for (const [metric, title, label, format] of GPU) {
		const full = family === 'gpu' ? title : `Process ${title}`;
		const pattern = `${family}.#.${metric}`;
		const shared = byTitle.get(full);
		if (shared) {
			MEASURES.set(pattern, { chart: shared, label: (d) => `${name} ${d} ${label}`, sub: 0 });
			continue;
		}
		define(`${family}.${metric}`, full, 'GPU', format, [[pattern, (d) => `${name} ${d} ${label}`]]);
		byTitle.set(full, KNOWN[KNOWN.length - 1]);
	}
}

define('cpu.cpu_percent', 'CPU Core Utilization (%)', 'CPU', 'pct', [
	['cpu.#.cpu_percent', (d) => `CPU ${d} Utilization (%)`]
]);
single('cpu.pcpu_percent', 'Apple P-core Utilization (%)', 'CPU', 'pct');
single('cpu.ecpu_percent', 'Apple E-core Utilization (%)', 'CPU', 'pct');
single('cpu.pcpu_freq', 'Apple P-core Frequency (MHz)', 'CPU', 'int');
single('cpu.ecpu_freq', 'Apple E-core Frequency (MHz)', 'CPU', 'int');
single('cpu.avg_temp', 'CPU Temperature (℃)', 'CPU', 'num');
single('cpu.powerWatts', 'CPU Power Usage (W)', 'CPU', 'num');

// `memory` is what wandb logged before `memory_percent`.
define('memory_percent', 'System Memory Utilization (%)', 'Memory', 'pct', [
	['memory_percent', () => 'System Memory Utilization (%)'],
	['memory', () => 'System Memory Utilization (%)']
]);
single('memory.used', 'System Memory Used (Bytes)', 'Memory', 'si');
single('memory.used_percent', 'System Memory Used (%)', 'Memory', 'pct');
single('swap.used', 'Swap Used (Bytes)', 'Memory', 'si');
single('swap.used_percent', 'Swap Used (%)', 'Memory', 'pct');

// `disk` is what wandb logged before per-path usage.
define('disk.usagePercent', 'Disk Utilization (%)', 'Disk', 'pct', [
	['disk', () => 'Disk Utilization (%)'],
	['disk.*.usagePercent', (d) => `Disk ${d} Utilization (%)`]
]);
define('disk.usageGB', 'Disk Utilization (GB)', 'Disk', 'num', [
	['disk.*.usageGB', (d) => `Disk ${d} Utilization (GB)`]
]);
define('disk.io', 'Disk I/O Utilization (MB)', 'Disk', 'num', [
	['disk.in', () => 'Disk Read (MB)'],
	['disk.out', () => 'Disk Write (MB)'],
	['disk.*.in', (d) => `Disk ${d} Read (MB)`],
	['disk.*.out', (d) => `Disk ${d} Write (MB)`]
]);
define('disk.ioRate', 'Disk I/O Rate (bytes/s)', 'Disk', 'si', [
	['disk.*.readBps', (d) => `Disk ${d} Read (bytes/s)`],
	['disk.*.writeBps', (d) => `Disk ${d} Write (bytes/s)`]
]);

define('network.traffic', 'Network Traffic (Bytes)', 'Network', 'si', [
	['network.recv', () => 'Network Traffic Received (bytes)'],
	['network.sent', () => 'Network Traffic Sent (bytes)']
]);
define('network.rate', 'Network Traffic Rate (bytes/s)', 'Network', 'si', [
	['network.recvBps', () => 'Network Traffic Received (bytes/s)'],
	['network.sentBps', () => 'Network Traffic Sent (bytes/s)']
]);
single('network.tcpRetransmits', 'Network TCP Retransmits', 'Network', 'int');

single('cpu', 'Process CPU Utilization (%)', 'Process', 'pct');
single('proc.cpu.threads', 'Process CPU Threads In Use', 'Process', 'int');
single('proc.cpu.throttledPercent', 'Process CPU Throttled (%)', 'Process', 'pct');
single('proc.memory.rssMB', 'Process Memory In Use (MB)', 'Process', 'num');
single('proc.memory.percent', 'Process Memory In Use (%)', 'Process', 'pct');
single('proc.memory.availableMB', 'Process Memory Available (MB)', 'Process', 'num');
single('proc.memory.oomKills', 'Process OOM Kills', 'Process', 'int');
single('wandb.cpu', 'W&B CPU Utilization (%)', 'Process', 'pct');
single('wandb.memory.rssMB', 'W&B Memory In Use (MB)', 'Process', 'num');

single('system.powerWatts', 'System Power Usage (W)', 'Other', 'num');
single('ane.power', 'Neural Engine Power Usage (W)', 'Other', 'num');
for (const [metric, title, label, format] of [
	['tensorcoreUtilization', 'TPU TensorCore Utilization (%)', 'TensorCore Utilization (%)', 'pct'],
	['dutyCycle', 'TPU Duty Cycle (%)', 'Duty Cycle (%)', 'pct'],
	['hbmCapacityUsage', 'TPU HBM Usage (Bytes)', 'HBM Usage (bytes)', 'si'],
	['hbmCapacityTotal', 'TPU HBM Capacity (Bytes)', 'HBM Capacity (bytes)', 'si']
] as const)
	define(`tpu.${metric}`, title, 'Other', format, [
		[`tpu.#.${metric}`, (d) => `TPU ${d} ${label}`]
	]);
define('trn.neuroncore_utilization', 'NeuronCore Utilization (%)', 'Other', 'pct', [
	['trn.#.neuroncore_utilization', (d) => `NeuronCore ${d} Utilization (%)`]
]);

const RANK = new Map(KNOWN.map((c, i) => [c.id, i]));

const WORDS: Record<string, string> = {
	ane: 'ANE',
	cpu: 'CPU',
	dcn: 'DCN',
	dram: 'DRAM',
	e2e: 'E2E',
	ecc: 'ECC',
	gb: 'GB',
	gib: 'GiB',
	gpu: 'GPU',
	grpc: 'gRPC',
	hbm: 'HBM',
	hlo: 'HLO',
	hmma: 'HMMA',
	ici: 'ICI',
	io: 'I/O',
	ipu: 'IPU',
	mb: 'MB',
	mhz: 'MHz',
	mib: 'MiB',
	nvlink: 'NVLink',
	oom: 'OOM',
	pcie: 'PCIe',
	rss: 'RSS',
	rtt: 'RTT',
	sm: 'SM',
	tcp: 'TCP',
	tpu: 'TPU',
	trn: 'TRN',
	wandb: 'W&B'
};

/** A key, or part of one, as words: `pipeFp16Active` → `Pipe FP16 Active`, `cpu_percent` → `CPU Percent`. */
export function humanize(s: string): string {
	return s
		.replace(/([a-z0-9])([A-Z])/g, '$1 $2')
		.replace(/([A-Z])([A-Z][a-z])/g, '$1 $2')
		.split(/[\s._/-]+/)
		.filter(Boolean)
		.map((w) => {
			const known = WORDS[w.toLowerCase()];
			if (known) return known;
			if (/^fp\d+$/i.test(w)) return w.toUpperCase();
			return w[0].toUpperCase() + w.slice(1);
		})
		.join(' ');
}

/** A format for a key nothing describes. */
function guessFormat(metric: string): MetricFormat {
	if (/percent|utili[sz]ation/i.test(metric)) return 'pct';
	if (/bytes$/i.test(metric)) return 'si';
	return 'num';
}

const GROUP_OF: Record<string, SystemGroup> = {
	gpu: 'GPU',
	cpu: 'CPU',
	memory: 'Memory',
	swap: 'Memory',
	disk: 'Disk',
	network: 'Network',
	proc: 'Process'
};

/** A measure for a key nothing describes, still one chart per measure across devices. */
function unknown(
	id: string,
	title: string,
	group: SystemGroup,
	metric: string,
	label: (device: string) => string
): Measure {
	// Never merge into a known chart that happens to have this id.
	if (RANK.has(id)) id = `key:${id}`;
	return { chart: { id, title, group, format: guessFormat(metric) }, label, sub: 0 };
}

interface Parsed {
	measure: Measure;
	device: string;
	/** The node of a shared-mode run (`gpu.0.gpu/l:node-1`), or `''`. */
	node: string;
}

/** What chart a key belongs to; keys may carry Flock's `system.` prefix or not. */
function parse(key: string): Parsed {
	let k = key.replace(/^system[./]/, '');
	let node = '';
	const l = k.indexOf('/l:');
	if (l > 0) {
		node = k.slice(l + 3);
		k = k.slice(0, l);
	}
	const known = MEASURES.get(k);
	if (known) return { measure: known, device: '', node };

	let m = /^gpu\.process\.(\d+)\.(.+)$/.exec(k);
	if (m) return indexed('gpu.process', m[1], m[2], node);

	m = /^disk\.(.+)\.([A-Za-z]+)$/.exec(k);
	if (m) {
		const [, path, metric] = m;
		const measure =
			MEASURES.get(`disk.*.${metric}`) ??
			unknown(
				`disk.${metric}`,
				`Disk ${humanize(metric)}`,
				'Disk',
				metric,
				(d) => `Disk ${d} ${humanize(metric)}`
			);
		return { measure, device: path, node };
	}

	m = /^([A-Za-z]+)\.(\d+)\.(.+)$/.exec(k);
	if (m) return indexed(m[1], m[2], m[3], node);

	// TPU distributions: `tpu.hloExecTiming.{label}.{stat}Us`, one chart per measure.
	m = /^tpu\.([^.]+)\.(.+)$/.exec(k);
	if (m) {
		const [, metric, rest] = m;
		const title = `TPU ${humanize(metric)}`;
		const label = rest.replaceAll('.', ' ');
		return {
			measure: unknown(`tpu.${metric}`, title, 'Other', metric, () => label),
			device: rest,
			node
		};
	}

	const title = humanize(k);
	const group = GROUP_OF[k.split('.')[0]] ?? 'Other';
	return { measure: unknown(k, title, group, k, () => title), device: '', node };
}

function indexed(family: string, device: string, metric: string, node: string): Parsed {
	if (family.startsWith('gpu')) metric = GPU_ALIASES[metric] ?? metric;
	const known = MEASURES.get(`${family}.#.${metric}`);
	if (known) return { measure: known, device, node };
	const name = FAMILIES[family]?.name ?? family.toUpperCase();
	const group = FAMILIES[family]?.group ?? 'Other';
	const words = humanize(metric);
	const measure = unknown(
		`${family}.${metric}`,
		`${name} ${words}`,
		group,
		metric,
		(d) => `${name} ${d} ${words}`
	);
	return { measure, device, node };
}

const collator = new Intl.Collator('en', { numeric: true });

/** Group a run/project's system metric keys into W&B-style charts, in W&B's display order. */
export function systemCharts(keys: readonly string[]): SystemChart[] {
	const charts = new Map<string, { def: ChartDef; series: (SystemSeries & Parsed)[] }>();
	for (const key of new Set(keys)) {
		const p = parse(key);
		const { chart } = p.measure;
		let entry = charts.get(chart.id);
		if (!entry) charts.set(chart.id, (entry = { def: chart, series: [] }));
		const label = p.measure.label(p.device);
		entry.series.push({ ...p, key, label: p.node ? `${label} (${p.node})` : label });
	}
	const group = (c: ChartDef) => SYSTEM_GROUPS.indexOf(c.group);
	const rank = (c: ChartDef) => RANK.get(c.id) ?? Infinity;
	return [...charts.values()]
		.sort(
			(a, b) =>
				group(a.def) - group(b.def) ||
				rank(a.def) - rank(b.def) ||
				collator.compare(a.def.title, b.def.title) ||
				(a.def.id < b.def.id ? -1 : a.def.id > b.def.id ? 1 : 0)
		)
		.map(({ def, series }) => ({
			...def,
			series: series
				.sort(
					(a, b) =>
						collator.compare(a.node, b.node) ||
						collator.compare(a.device, b.device) ||
						a.measure.sub - b.measure.sub ||
						(a.key < b.key ? -1 : a.key > b.key ? 1 : 0)
				)
				.map(({ key, label }) => ({ key, label }))
		}));
}
