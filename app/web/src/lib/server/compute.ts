import type { ComputeSnapshot } from '#lib/compute.ts';

type RawJob = {
	key: string;
	id: string;
	clusterId: string;
	name: string | null;
	user: string | null;
	partition: string | null;
	account: string | null;
	qos: string | null;
	state: string;
	reason: string | null;
	nodeList: string | null;
	elapsedSeconds: number | null;
	timeLimitSeconds: number | null;
	timeLimitUnlimited: boolean;
	gpusAllocated: number | null;
	gpusRequested: number | null;
	nodes: number | null;
	submitTime: string | null;
	startTime: string | null;
	startTimeIsEstimate: boolean;
};
type RawPartition = {
	key: string;
	clusterId: string;
	name: string;
	availability: string;
	timeLimitSeconds: number | null;
	timeLimitUnlimited: boolean;
	nodes: { allocated: number | null; idle: number | null; other: number | null };
};
export interface SlurmResponse {
	data: { jobs: RawJob[]; partitions: RawPartition[] };
	clusters: { id: string; name: string; enabled: boolean }[];
	summary: { state: string };
}

export function duration(seconds: number | null, unlimited = false): string {
	if (unlimited) return 'Unlimited';
	if (seconds === null) return '—';
	const days = Math.floor(seconds / 86400);
	const hours = Math.floor((seconds % 86400) / 3600);
	const minutes = Math.floor((seconds % 3600) / 60);
	const remaining = Math.floor(seconds % 60);
	return (
		[
			days && days + 'd',
			hours && hours + 'h',
			minutes && minutes + 'm',
			remaining && remaining + 's'
		]
			.filter(Boolean)
			.join(' ') || '0s'
	);
}
const timestamp = (value: string | null) =>
	value
		? value
				.replace('T', ' ')
				.replace(/\.\d+Z$/, ' UTC')
				.replace(/Z$/, ' UTC')
		: '—';

export function normalizeSnapshot(raw: SlurmResponse, planAccounts: string[]): ComputeSnapshot {
	const names = new Map(raw.clusters.map((cluster) => [cluster.id, cluster.name]));
	const clusterName = (id: string) => names.get(id) ?? id;
	return {
		clusters: raw.clusters
			.filter((cluster) => cluster.enabled)
			.map((cluster) => clusterName(cluster.id)),
		planAccounts,
		message: raw.summary.state === 'ok' ? null : 'Some cluster data is unavailable or stale.',
		jobs: raw.data.jobs.map((job) => {
			const running = job.state === 'RUNNING';
			return {
				key: job.key,
				id: job.id,
				clusterId: job.clusterId,
				cluster: clusterName(job.clusterId),
				name: job.name ?? '—',
				owner: job.user ?? '—',
				partition: job.partition ?? '—',
				account: job.account ?? '—',
				qos: job.qos ?? '—',
				state: job.state.charAt(0) + job.state.slice(1).toLowerCase(),
				detail: running ? duration(job.elapsedSeconds) : (job.reason ?? '—'),
				elapsedSeconds: job.elapsedSeconds,
				timeLimitSeconds: job.timeLimitUnlimited ? Number.MAX_SAFE_INTEGER : job.timeLimitSeconds,
				timeLimit: duration(job.timeLimitSeconds, job.timeLimitUnlimited),
				gpus: running ? job.gpusAllocated : job.gpusRequested,
				gpusAllocated: job.gpusAllocated,
				gpusRequested: job.gpusRequested,
				nodes: job.nodes,
				nodeList: job.nodeList,
				submitted: timestamp(job.submitTime),
				started: job.startTime
					? timestamp(job.startTime) + (job.startTimeIsEstimate ? ' (estimated)' : '')
					: null
			};
		}),
		partitions: raw.data.partitions.map((partition) => ({
			key: partition.key,
			clusterId: partition.clusterId,
			cluster: clusterName(partition.clusterId),
			name: partition.name,
			availability: partition.availability,
			limit: partition.timeLimitUnlimited ? Number.MAX_SAFE_INTEGER : partition.timeLimitSeconds,
			limitLabel: duration(partition.timeLimitSeconds, partition.timeLimitUnlimited),
			...partition.nodes
		}))
	};
}

export async function loadCompute(org: string, fetcher: typeof fetch): Promise<ComputeSnapshot> {
	const planAccounts = (process.env.FLOCK_SLURM_ACCOUNTS ?? '')
		.split(',')
		.map((value) => value.trim())
		.filter(Boolean);
	const empty: ComputeSnapshot = {
		jobs: [],
		partitions: [],
		clusters: [],
		planAccounts,
		message: null
	};
	if (!process.env.FLOCK_SLURM_URL || process.env.FLOCK_SLURM_ORG !== org) {
		return { ...empty, message: 'Compute is not configured for this workspace.' };
	}
	try {
		const headers = new Headers({ accept: 'application/json' });
		const clientId = process.env.FLOCK_SLURM_ACCESS_CLIENT_ID;
		const clientSecret = process.env.FLOCK_SLURM_ACCESS_CLIENT_SECRET;
		if (!!clientId !== !!clientSecret) throw new Error('Incomplete Access credentials');
		if (clientId && clientSecret) {
			headers.set('CF-Access-Client-Id', clientId);
			headers.set('CF-Access-Client-Secret', clientSecret);
		}
		const url = new URL(process.env.FLOCK_SLURM_URL);
		url.pathname = url.pathname.replace(/\/$/, '') + '/api/jobs';
		url.search = '?cluster=all';
		const response = await fetcher(url, {
			headers,
			redirect: 'error',
			signal: AbortSignal.timeout(10000)
		});
		if (!response.ok) throw new Error('Slurm API unavailable');
		const raw = (await response.json()) as SlurmResponse;
		return normalizeSnapshot(raw, planAccounts);
	} catch {
		return {
			...empty,
			message: 'Unable to load compute data. Check the Slurm connection and Access credentials.'
		};
	}
}
