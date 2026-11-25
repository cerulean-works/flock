#!/bin/bash
# Launch one sweep file inside this allocation (GB200, aarch64 environment).
#   sbatch -N <nodes> -J <name> -t <limit> launch.sbatch <sweep.yaml>
# Resubmit the same command to continue: finished runs are skipped and
# interrupted runs resume from their latest checkpoint.
#SBATCH --partition=gb200_72gpu
#SBATCH --account=ACD115198
#SBATCH --gpus-per-node=4
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=136
#SBATCH --mem=900000M
#SBATCH --output=/work/u4982363/sweeps/logs/%x-%j.out
#SBATCH --signal=B:TERM@120
set -euo pipefail
SWEEP=${1:?usage: sbatch launch.sbatch path/to/sweep.yaml}

source /home/u4982363/experiments/.tools/env.sh
# The tokenizer is cached on /work; stay offline so dozens of runs never hit the Hub at once.
export HF_HOME=/work/u4982363/hf HF_TOKEN_PATH=$HOME/.cache/huggingface/token HF_HUB_OFFLINE=1
# env.sh disables W&B for ad hoc testing; each run's config sets its own mode.
unset WANDB_MODE
export WANDB_DIR=/work/u4982363/wandb

# Each run's DataLoader workers tokenize text; four runs share a node. The default soft limit of
# 2048 threads per user is exhausted by library thread pools sized to the node's 144 cores, so
# raise it (srun passes resource limits on to every run) and size the pools to a run's share.
ulimit -u "$(ulimit -Hu)"
export RAYON_NUM_THREADS=4            # Tokenizer threads per DataLoader worker.
export LANCE_CPU_THREADS=4 LANCE_IO_THREADS=8
export TORCHINDUCTOR_COMPILE_THREADS=8 OMP_NUM_THREADS=4

# GPU memory and utilization on the batch node every 10 s, for stall and OOM diagnosis.
# A monitoring job step would take CPUs that the runs' --exact steps are sized to use.
nvidia-smi --query-gpu=timestamp,index,utilization.gpu,memory.used --format=csv,noheader -l 10 \
  > "/work/u4982363/sweeps/logs/$SLURM_JOB_NAME-$SLURM_JOB_ID-gpu-$(hostname).csv" &

cd /home/u4982363/experiments/scaling-law-pilot
python -m flock.training.experiments.scaling_law.sweep launch "$SWEEP"
