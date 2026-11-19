#!/bin/bash
# Train a sweep inside one allocation, several runs at a time.
# Submit from the repository root:
#   sbatch flock/training/experiments/scaling_law/launch_sweep.sbatch path/to/sweep.yaml
# Resubmit the same command to continue: finished runs are skipped and
# interrupted runs resume from their latest checkpoint.
#SBATCH --job-name=flock-sweep
#SBATCH --nodes=4
#SBATCH --gpus-per-node=4
#SBATCH --ntasks-per-node=1
#SBATCH --exclusive
#SBATCH --time=48:00:00
#SBATCH --output=%x-%j.out
#SBATCH --signal=B:TERM@120
# Set these for your cluster:
##SBATCH --partition=gpu
##SBATCH --qos=normal
##SBATCH --account=cerulean

set -euo pipefail
SWEEP=${1:?usage: sbatch launch_sweep.sbatch path/to/sweep.yaml}

# Each run's processes start from this environment, so it must exist on every node.
source .venv/bin/activate

# Ask Slurm for SIGTERM two minutes before the time limit; the launcher then
# stops its runs and leaves them claimed for the next submission to resume.
exec python -m flock.training.experiments.scaling_law.sweep launch "$SWEEP"
