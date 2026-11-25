#!/bin/bash
# 400M production WSD runs on GB200 (aarch64): wsd_launch.sbatch, but from ~/experiments/scaling-law-prod
# (branch experiment/scaling-law-prod) and its venv slp-prod-gb200.
#   sbatch -p gb200_72gpu -N <nodes> --gpus-per-node=4 --cpus-per-task=136 --mem=900000M -J <name> -t <limit> \
#     wsd_prod_launch.sbatch a.yaml:<max_slots> b.yaml:<max_slots> ...
# Sweeps given as one argument separated by "+" run one after another (a.yaml:1+b.yaml:1), others concurrently.
# Give each sweep file in one job its own master_port. Resubmit the same command to continue.
#SBATCH --account=ACD115198
#SBATCH --ntasks-per-node=1
#SBATCH --output=/work/u4982363/sweeps/logs/%x-%j.out
#SBATCH --signal=B:TERM@120
set -uo pipefail
[ $# -gt 0 ] || { echo "usage: wsd_prod_launch.sbatch sweep.yaml:max_slots[+next.yaml:max_slots] ..."; exit 2; }

case $(uname -m) in
  aarch64) source /home/u4982363/experiments/.tools/env-gb200-prod.sh  # GB200: 36 CPUs per GPU
           export RAYON_NUM_THREADS=4 OMP_NUM_THREADS=4 TORCHINDUCTOR_COMPILE_THREADS=8 ;;
  *)       echo "wsd_prod_launch.sbatch is GB200 (aarch64) only"; exit 2 ;;
esac
export HF_HOME=/work/u4982363/hf HF_TOKEN_PATH=$HOME/.cache/huggingface/token HF_HUB_OFFLINE=1
unset WANDB_MODE                      # env file disables W&B; each run's config sets its own mode.
export WANDB_DIR=/work/u4982363/wandb
export LANCE_CPU_THREADS=4 LANCE_IO_THREADS=8
ulimit -u "$(ulimit -Hu)"             # Library thread pools exhaust the default per-user limit.
ulimit -n "$(ulimit -Hn)"             # A systemd-submitted job inherits soft nofile 1024; DataLoader fd passing needs more.

nvidia-smi --query-gpu=timestamp,index,utilization.gpu,memory.used --format=csv,noheader -l 10 \
  > "/work/u4982363/sweeps/logs/$SLURM_JOB_NAME-$SLURM_JOB_ID-gpu-$(hostname).csv" &
monitor=$!

cd /home/u4982363/experiments/scaling-law-prod
stopping=0
chain() {  # Launch "a.yaml:n+b.yaml:m" one sweep after another; stop at the first failure.
  local spec code=0
  IFS=+ read -ra specs <<< "$1"
  for spec in "${specs[@]}"; do
    [ "$stopping" -eq 0 ] || return 1
    local sweep=${spec%:*} slots=${spec##*:}
    python -m flock.training.experiments.scaling_law.sweep launch "$sweep" --max-slots "$slots" \
      > >(sed -u "s|^|[$(basename "$sweep" .yaml)] |") 2>&1 &
    child=$!
    trap 'stopping=1; kill -TERM "$child" 2>/dev/null' TERM
    while :; do wait "$child"; code=$?; kill -0 "$child" 2>/dev/null || break; done
    [ "$code" -eq 0 ] || return "$code"
  done
}
pids=()
for spec in "$@"; do
  chain "$spec" &
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
