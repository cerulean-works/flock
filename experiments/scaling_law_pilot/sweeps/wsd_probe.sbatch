#!/bin/bash
# WSD pre-launch probe on one H200 node: 100M and 200M throughput, memory and save-time evals,
# then a decay branch from the 100M probe. Sweep files run one after another.
#   sbatch -A ACD115198 -p h200_256gpu -N 1 --gpus-per-node=8 --cpus-per-task=104 --mem=1800000M \
#     -J wsd-probe -t 75 wsd_probe.sbatch
#SBATCH --account=ACD115198
#SBATCH --ntasks-per-node=1
#SBATCH --output=/work/u4982363/sweeps/logs/%x-%j.out
#SBATCH --signal=B:TERM@120
set -uo pipefail
source /home/u4982363/experiments/.tools/env-h200-wsd.sh
export RAYON_NUM_THREADS=2 OMP_NUM_THREADS=2 TORCHINDUCTOR_COMPILE_THREADS=4
export HF_HOME=/work/u4982363/hf HF_TOKEN_PATH=$HOME/.cache/huggingface/token HF_HUB_OFFLINE=1
unset WANDB_MODE
export WANDB_DIR=/work/u4982363/wandb
export LANCE_CPU_THREADS=4 LANCE_IO_THREADS=8
ulimit -u "$(ulimit -Hu)"
ulimit -n "$(ulimit -Hn)"             # Watcher-submitted jobs inherit systemd's 1024 soft fd limit; DataLoader shm fd passing needs thousands.
nvidia-smi --query-gpu=timestamp,index,utilization.gpu,memory.used --format=csv,noheader -l 5 \
  > "/work/u4982363/sweeps/logs/$SLURM_JOB_NAME-$SLURM_JOB_ID-gpu-$(hostname).csv" &
monitor=$!
cd /home/u4982363/experiments/scaling-law-wsd
echo "worktree $(git rev-parse HEAD) $(git status --porcelain | wc -l) uncommitted"
status=0
for sweep in wsd-probe-100m wsd-probe-200m wsd-probe-branch; do
  echo "=== $(date +%T) $sweep"
  python -m flock.training.experiments.scaling_law.sweep launch /work/u4982363/sweeps/configs/$sweep.yaml \
    2>&1 | sed -u "s|^|[$sweep] |" || status=1
done
echo "=== $(date +%T) done"
kill $monitor 2>/dev/null
exit $status
