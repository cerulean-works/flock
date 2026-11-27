/** What Home shows of the Slurm clusters: GPUs held by running jobs, and the queue behind them. */
export interface ClusterGlance {
	/** Where the dashboard opens. */
	href: string;
	/** GPUs allocated now, across the tracked GPU classes. */
	allocated: number;
	/** Their total, when the dashboard knows it. */
	capacity: number | null;
	/** Allocated GPUs over the dashboard's window (2 hours), oldest first. */
	points: { x: number; y: number | null }[];
	running: number;
	pending: number;
}
