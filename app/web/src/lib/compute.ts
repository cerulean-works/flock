export interface ComputeJob {
	key: string;
	id: string;
	name: string;
	owner: string;
	cluster: string;
	clusterId: string;
	partition: string;
	account: string;
	qos: string;
	state: string;
	detail: string;
	elapsedSeconds: number | null;
	timeLimitSeconds: number | null;
	gpus: number | null;
	gpusAllocated: number | null;
	gpusRequested: number | null;
	nodes: number | null;
	nodeList: string | null;
	timeLimit: string;
	submitted: string;
	started: string | null;
}
export interface ComputePartition {
	key: string;
	cluster: string;
	clusterId: string;
	name: string;
	availability: string;
	limit: number | null;
	limitLabel: string;
	allocated: number | null;
	idle: number | null;
	other: number | null;
}
export interface ComputeSnapshot {
	jobs: ComputeJob[];
	partitions: ComputePartition[];
	clusters: string[];
	planAccounts: string[];
	message: string | null;
}
