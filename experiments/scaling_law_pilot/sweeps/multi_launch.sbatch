#!/bin/bash
# Train several sweep files inside one allocation, each launcher capped to its share of slots.
#   sbatch -p <partition> -N <nodes> --gpus-per-node=<g> --cpus-per-task=<c> --mem=<m> -J <name> -t <limit> \
#     multi_launch.sbatch a.yaml:<max_slots> b.yaml:<max_slots> ...
# Give each sweep file in one job its own master_port. Resubmit the same command to continue.
#SBATCH --account=ACD115198
#SBATCH --ntasks-per-node=1
#SBATCH --output=/work/u4982363/sweeps/logs/%x-%j.out
#SBATCH --signal=B:TERM@120
set -uo pipefail
[ $# -gt 0 ] || { echo "usage: multi_launch.sbatch sweep.yaml:max_slots ..."; exit 2; }

case $(uname -m) in
  aarch64) source /home/u4982363/experiments/.tools/env.sh       # GB200: 36 CPUs per GPU
           export RAYON_NUM_THREADS=4 OMP_NUM_THREADS=4 TORCHINDUCTOR_COMPILE_THREADS=8 ;;
  x86_64)  source /home/u4982363/experiments/.tools/env-h200.sh  # H200: 14 CPUs per GPU
           export RAYON_NUM_THREADS=2 OMP_NUM_THREADS=2 TORCHINDUCTOR_COMPILE_THREADS=4 ;;
esac
export HF_HOME=/work/u4982363/hf HF_TOKEN_PATH=$HOME/.cache/huggingface/token HF_HUB_OFFLINE=1
unset WANDB_MODE                      # env.sh disables W&B; each run's config sets its own mode.
export WANDB_DIR=/work/u4982363/wandb
export LANCE_CPU_THREADS=4 LANCE_IO_THREADS=8
ulimit -u "$(ulimit -Hu)"             # Library thread pools exhaust the default per-user limit.
ulimit -n "$(ulimit -Hn)"             # Watcher-submitted jobs inherit systemd's 1024 soft fd limit; DataLoader shm fd passing needs thousands.

nvidia-smi --query-gpu=timestamp,index,utilization.gpu,memory.used --format=csv,noheader -l 10 \
  > "/work/u4982363/sweeps/logs/$SLURM_JOB_NAME-$SLURM_JOB_ID-gpu-$(hostname).csv" &
monitor=$!

cd /home/u4982363/experiments/scaling-law-pilot
pids=()
for spec in "$@"; do
  sweep=${spec%:*}; slots=${spec##*:}
  python -m flock.training.experiments.scaling_law.sweep launch "$sweep" --max-slots "$slots" \
    > >(sed -u "s|^|[$(basename "$sweep" .yaml)] |") 2>&1 &
  pids+=($!)
done
# Slurm signals only this shell; pass the time-limit SIGTERM on so every launcher stops its runs.
trap 'echo "SIGTERM: stopping launchers"; kill -TERM "${pids[@]}" 2>/dev/null' TERM
status=0
for pid in "${pids[@]}"; do
  while :; do wait "$pid"; code=$?; kill -0 "$pid" 2>/dev/null || break; done
  [ "$code" -eq 0 ] || status=1
done
kill "$monitor" 2>/dev/null
exit $status
