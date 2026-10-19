import { describe, expect, it } from 'vitest';
import { humanize, systemCharts, SYSTEM_GROUPS, type SystemChart } from './system-metrics.ts';

/** The per-GPU keys an 8-GPU H100 run logs with wandb-core (NVML + GPM). */
const GPU_METRICS = [
	'gpu',
	'memory',
	'memoryAllocated',
	'memoryAllocatedBytes',
	'temp',
	'powerWatts',
	'enforcedPowerLimitWatts',
	'powerPercent',
	'smClock',
	'memoryClock',
	'graphicsClock',
	'correctedMemoryErrors',
	'uncorrectedMemoryErrors',
	'pcieLinkGen',
	'pcieLinkSpeed',
	'pcieLinkWidth',
	'smActive',
	'smOccupancy',
	'pipeTensorActive',
	'dramActive',
	'pipeFp64Active',
	'pipeFp32Active',
	'pipeFp16Active',
	'pipeTensorHmmaActive',
	'pcieTxBytes',
	'pcieRxBytes',
	'nvlinkTxBytes',
	'nvlinkRxBytes'
];

const HOST = [
	'system.cpu',
	'system.proc.cpu.threads',
	'system.memory_percent',
	'system.proc.memory.rssMB',
	'system.proc.memory.percent',
	'system.proc.memory.availableMB',
	'system.disk./.usagePercent',
	'system.disk./.usageGB',
	'system.disk.in',
	'system.disk.out',
	'system.network.sent',
	'system.network.recv'
];

const EIGHT_GPUS = [
	...HOST,
	...Array.from({ length: 8 }, (_, i) => GPU_METRICS.map((m) => `system.gpu.${i}.${m}`)).flat()
];

const byId = (charts: SystemChart[], id: string) => {
	const c = charts.find((c) => c.id === id);
	if (!c) throw new Error(`no chart ${id}`);
	return c;
};

/** A deterministic shuffle. */
function shuffle<T>(xs: readonly T[]): T[] {
	const out = [...xs];
	let seed = 7;
	for (let i = out.length - 1; i > 0; i--) {
		seed = (seed * 16807) % 2147483647;
		const j = seed % (i + 1);
		[out[i], out[j]] = [out[j], out[i]];
	}
	return out;
}

describe('systemCharts', () => {
	it('groups an 8-GPU run into one chart per measure', () => {
		const charts = systemCharts(EIGHT_GPUS);
		expect(charts).toHaveLength(GPU_METRICS.length + HOST.length - 2); // disk I/O, network merge
		expect(charts.flatMap((c) => c.series.map((s) => s.key)).sort()).toEqual(
			[...EIGHT_GPUS].sort()
		);

		const nvlink = byId(charts, 'gpu.nvlinkTxBytes');
		expect(nvlink).toEqual({
			id: 'gpu.nvlinkTxBytes',
			title: 'GPU NVLink Tx Bytes',
			group: 'GPU',
			format: 'si',
			series: Array.from({ length: 8 }, (_, i) => ({
				key: `system.gpu.${i}.nvlinkTxBytes`,
				label: `GPU ${i} NVLink Tx (bytes)`
			}))
		});
		for (const c of charts.filter((c) => c.group === 'GPU')) expect(c.series).toHaveLength(8);
	});

	it('uses W&B titles, labels and formats', () => {
		const charts = systemCharts(EIGHT_GPUS);
		const summary = (id: string) => {
			const c = byId(charts, id);
			return [c.title, c.series[0].label, c.format, c.group];
		};
		expect(summary('gpu.gpu')).toEqual([
			'GPU Utilization (%)',
			'GPU 0 Utilization (%)',
			'pct',
			'GPU'
		]);
		expect(summary('gpu.memory')).toEqual([
			'GPU Time Spent Accessing Memory (%)',
			'GPU 0 Time Spent Accessing Memory (%)',
			'pct',
			'GPU'
		]);
		expect(summary('gpu.memoryAllocatedBytes')).toEqual([
			'GPU Memory Allocated (Bytes)',
			'GPU 0 Memory Allocated (bytes)',
			'si',
			'GPU'
		]);
		expect(summary('gpu.temp')).toEqual([
			'GPU Temperature (℃)',
			'GPU 0 Temperature (℃)',
			'num',
			'GPU'
		]);
		expect(summary('gpu.powerWatts')).toEqual([
			'GPU Power Usage (W)',
			'GPU 0 Power Usage (W)',
			'num',
			'GPU'
		]);
		expect(summary('gpu.smClock')).toEqual([
			'GPU Streaming Multiprocessor (SM) Clock Speed (MHz)',
			'GPU 0 SM Clock Speed (MHz)',
			'int',
			'GPU'
		]);
		expect(summary('gpu.uncorrectedMemoryErrors')).toEqual([
			'GPU Uncorrected Memory Errors',
			'GPU 0 Uncorrected Memory Errors',
			'int',
			'GPU'
		]);
		expect(summary('gpu.pipeFp16Active')).toEqual([
			'GPU Pipe FP16 Active',
			'GPU 0 Pipe FP16 Active (%)',
			'pct',
			'GPU'
		]);
		expect(summary('cpu')).toEqual([
			'Process CPU Utilization (%)',
			'Process CPU Utilization (%)',
			'pct',
			'Process'
		]);
		expect(summary('proc.cpu.threads')[0]).toBe('Process CPU Threads In Use');
		expect(summary('proc.memory.rssMB')[0]).toBe('Process Memory In Use (MB)');
		expect(summary('proc.memory.percent')[0]).toBe('Process Memory In Use (%)');
		expect(summary('proc.memory.availableMB')[0]).toBe('Process Memory Available (MB)');
		expect(summary('memory_percent')).toEqual([
			'System Memory Utilization (%)',
			'System Memory Utilization (%)',
			'pct',
			'Memory'
		]);
		expect(summary('disk.usagePercent')).toEqual([
			'Disk Utilization (%)',
			'Disk / Utilization (%)',
			'pct',
			'Disk'
		]);
		expect(summary('disk.usageGB')[0]).toBe('Disk Utilization (GB)');
	});

	it('puts related keys in one chart', () => {
		const charts = systemCharts(EIGHT_GPUS);
		expect(byId(charts, 'network.traffic')).toEqual({
			id: 'network.traffic',
			title: 'Network Traffic (Bytes)',
			group: 'Network',
			format: 'si',
			series: [
				{ key: 'system.network.recv', label: 'Network Traffic Received (bytes)' },
				{ key: 'system.network.sent', label: 'Network Traffic Sent (bytes)' }
			]
		});
		expect(byId(charts, 'disk.io').series).toEqual([
			{ key: 'system.disk.in', label: 'Disk Read (MB)' },
			{ key: 'system.disk.out', label: 'Disk Write (MB)' }
		]);

		const cores = systemCharts([
			'system.cpu.1.cpu_percent',
			'system.cpu.0.cpu_percent',
			'system.cpu'
		]);
		expect(cores.map((c) => c.title)).toEqual([
			'CPU Core Utilization (%)',
			'Process CPU Utilization (%)'
		]);
		expect(cores[0].series.map((s) => s.label)).toEqual([
			'CPU 0 Utilization (%)',
			'CPU 1 Utilization (%)'
		]);
	});

	it('orders series by device index numerically', () => {
		const keys = [10, 2, 0, 1, 11, 3].map((i) => `system.gpu.${i}.temp`);
		const [chart] = systemCharts(keys);
		expect(chart.series.map((s) => s.label)).toEqual(
			[0, 1, 2, 3, 10, 11].map((i) => `GPU ${i} Temperature (℃)`)
		);
	});

	it('handles disk paths with dots and slashes', () => {
		const charts = systemCharts([
			'system.disk./mnt/data.usageGB',
			'system.disk./.usageGB',
			'system.disk./mnt/v1.2.usagePercent',
			'system.disk./.usagePercent',
			'system.disk.nvme1n1.out',
			'system.disk.nvme10n1.in',
			'system.disk.nvme1n1.in'
		]);
		expect(byId(charts, 'disk.usageGB').series).toEqual([
			{ key: 'system.disk./.usageGB', label: 'Disk / Utilization (GB)' },
			{ key: 'system.disk./mnt/data.usageGB', label: 'Disk /mnt/data Utilization (GB)' }
		]);
		expect(byId(charts, 'disk.usagePercent').series.map((s) => s.label)).toEqual([
			'Disk / Utilization (%)',
			'Disk /mnt/v1.2 Utilization (%)'
		]);
		expect(byId(charts, 'disk.io').series.map((s) => s.label)).toEqual([
			'Disk nvme1n1 Read (MB)',
			'Disk nvme1n1 Write (MB)',
			'Disk nvme10n1 Read (MB)'
		]);
		expect(charts.map((c) => c.id)).toEqual(['disk.usagePercent', 'disk.usageGB', 'disk.io']);
	});

	it('accepts keys without the system prefix', () => {
		const bare = EIGHT_GPUS.map((k) => k.slice('system.'.length));
		const strip = (charts: SystemChart[]) =>
			charts.map((c) => ({ ...c, series: c.series.map((s) => s.label) }));
		const charts = systemCharts(bare);
		expect(strip(charts)).toEqual(strip(systemCharts(EIGHT_GPUS)));
		expect(byId(charts, 'gpu.gpu').series[0].key).toBe('gpu.0.gpu');
	});

	it('keeps unknown keys, grouped across GPUs, with readable titles', () => {
		const charts = systemCharts([
			'system.gpu.1.fooBarBaz',
			'system.gpu.0.fooBarBaz',
			'system.gpu.process.0.someNewThing',
			'system.weird_thing.someValue',
			'system.load15',
			'system.tpu.0.newCounter',
			'system.disk./.inodesUsedPercent'
		]);
		expect(byId(charts, 'gpu.fooBarBaz')).toEqual({
			id: 'gpu.fooBarBaz',
			title: 'GPU Foo Bar Baz',
			group: 'GPU',
			format: 'num',
			series: [
				{ key: 'system.gpu.0.fooBarBaz', label: 'GPU 0 Foo Bar Baz' },
				{ key: 'system.gpu.1.fooBarBaz', label: 'GPU 1 Foo Bar Baz' }
			]
		});
		expect(byId(charts, 'gpu.process.someNewThing').series[0].label).toBe(
			'Process GPU 0 Some New Thing'
		);
		expect(byId(charts, 'weird_thing.someValue')).toMatchObject({
			title: 'Weird Thing Some Value',
			group: 'Other',
			series: [{ key: 'system.weird_thing.someValue', label: 'Weird Thing Some Value' }]
		});
		expect(byId(charts, 'tpu.newCounter')).toMatchObject({
			title: 'TPU New Counter',
			group: 'Other'
		});
		expect(byId(charts, 'disk.inodesUsedPercent')).toMatchObject({
			title: 'Disk Inodes Used Percent',
			group: 'Disk',
			format: 'pct',
			series: [{ label: 'Disk / Inodes Used Percent' }]
		});
		expect(charts.flatMap((c) => c.series)).toHaveLength(7);
	});

	it('orders by group, then W&B order, then unknowns alphabetically', () => {
		const charts = systemCharts(
			shuffle([
				'system.zeta',
				'system.alpha',
				'system.network.sent',
				'system.proc.cpu.threads',
				'system.cpu',
				'system.disk./.usagePercent',
				'system.memory_percent',
				'system.cpu.0.cpu_percent',
				'system.gpu.0.zzzUnknown',
				'system.gpu.0.aaaUnknown',
				'system.gpu.0.memoryAllocated',
				'system.gpu.0.gpu',
				'system.gpu.process.0.gpu',
				'system.gpu.0.temp'
			])
		);
		expect(charts.map((c) => c.id)).toEqual([
			'gpu.gpu',
			'gpu.temp',
			'gpu.memoryAllocated',
			'gpu.process.gpu',
			'gpu.aaaUnknown',
			'gpu.zzzUnknown',
			'cpu.cpu_percent',
			'memory_percent',
			'disk.usagePercent',
			'network.traffic',
			'cpu',
			'proc.cpu.threads',
			'alpha',
			'zeta'
		]);
		const groups = charts.map((c) => SYSTEM_GROUPS.indexOf(c.group as never));
		expect(groups).toEqual([...groups].sort((a, b) => a - b));
	});

	it('is deterministic whatever the input order', () => {
		expect(systemCharts(shuffle(EIGHT_GPUS))).toEqual(systemCharts(EIGHT_GPUS));
		expect(systemCharts([...EIGHT_GPUS].reverse())).toEqual(systemCharts(EIGHT_GPUS));
	});

	it('drops duplicate keys', () => {
		const [chart] = systemCharts(['system.gpu.0.gpu', 'system.gpu.0.gpu']);
		expect(chart.series).toHaveLength(1);
	});

	it('maps legacy keys and aliases into the current charts', () => {
		const charts = systemCharts([
			'system.memory',
			'system.disk',
			'system.gpu.0.unCorrectedMemoryErrors',
			'system.gpu.1.uncorrectedMemoryErrors'
		]);
		expect(charts.map((c) => c.id)).toEqual([
			'gpu.uncorrectedMemoryErrors',
			'memory_percent',
			'disk.usagePercent'
		]);
		expect(charts[0].series.map((s) => s.label)).toEqual([
			'GPU 0 Uncorrected Memory Errors',
			'GPU 1 Uncorrected Memory Errors'
		]);
		expect(byId(charts, 'disk.usagePercent').series[0].label).toBe('Disk Utilization (%)');
	});

	it('names per-process GPU metrics', () => {
		const [chart] = systemCharts([
			'system.gpu.process.1.memoryAllocated',
			'system.gpu.process.0.memoryAllocated'
		]);
		expect(chart).toMatchObject({
			id: 'gpu.process.memoryAllocated',
			title: 'Process GPU Memory Allocated (%)',
			group: 'GPU',
			format: 'pct'
		});
		expect(chart.series.map((s) => s.label)).toEqual([
			'Process GPU 0 Memory Allocated (%)',
			'Process GPU 1 Memory Allocated (%)'
		]);
	});

	it('labels the nodes of shared-mode runs', () => {
		const [chart] = systemCharts([
			'system.gpu.1.gpu/l:node-b',
			'system.gpu.0.gpu/l:node-b',
			'system.gpu.0.gpu/l:node-a'
		]);
		expect(chart.series.map((s) => s.label)).toEqual([
			'GPU 0 Utilization (%) (node-a)',
			'GPU 0 Utilization (%) (node-b)',
			'GPU 1 Utilization (%) (node-b)'
		]);
	});

	it('never merges an unknown key into a known chart', () => {
		const charts = systemCharts(['system.disk.usagePercent', 'system.disk./.usagePercent']);
		expect(charts.map((c) => c.id)).toEqual(['disk.usagePercent', 'key:disk.usagePercent']);
	});

	it('returns nothing for no keys', () => {
		expect(systemCharts([])).toEqual([]);
	});
});

describe('humanize', () => {
	it.each([
		['pipeFp16Active', 'Pipe FP16 Active'],
		['nvlinkTxBytes', 'NVLink Tx Bytes'],
		['pcieRxBytes', 'PCIe Rx Bytes'],
		['smOccupancy', 'SM Occupancy'],
		['cpu_percent', 'CPU Percent'],
		['proc.memory.rssMB', 'Proc Memory RSS MB'],
		['hbmCapacityUsage', 'HBM Capacity Usage'],
		['HBMUsage', 'HBM Usage'],
		['usageGB', 'Usage GB'],
		['average board temp (C)', 'Average Board Temp (C)']
	])('%s → %s', (key, words) => {
		expect(humanize(key)).toBe(words);
	});
});
