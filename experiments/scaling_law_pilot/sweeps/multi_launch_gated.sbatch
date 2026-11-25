#!/bin/bash
# multi_launch.sbatch plus branch gating, for the 750M sweep.
#   sbatch ... multi_launch_gated.sbatch a.yaml:<max_slots> b-branch.yaml:<max_slots>:gated ...
# A ":gated" sweep starts its launcher only once every run's branch_from checkpoint is published
# (checkpoints appear by rename, so an existing directory is complete). While a gated launcher
# waits, and while its srun steps wait for GPUs held by other launchers in this allocation, it
# uses nothing. Once every ungated launcher has exited, gated launchers still waiting on their
# gate are stopped so the allocation ends (the watcher resubmits when the parents are ready).
#SBATCH --account=ACD115198
#SBATCH --ntasks-per-node=1
#SBATCH --output=/work/u4982363/sweeps/logs/%x-%j.out
#SBATCH --signal=B:TERM@120
set -uo pipefail
[ $# -gt 0 ] || { echo "usage: multi_launch_gated.sbatch sweep.yaml:max_slots[:gated] ..."; exit 2; }

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
ulimit -n "$(ulimit -Hn)"             # systemd-submitted jobs inherit soft nofile=1024; DataLoader fd passing needs more.

nvidia-smi --query-gpu=timestamp,index,utilization.gpu,memory.used --format=csv,noheader -l 10 \
  > "/work/u4982363/sweeps/logs/$SLURM_JOB_NAME-$SLURM_JOB_ID-gpu-$(hostname).csv" &
monitor=$!

gates=/tmp/gates-$SLURM_JOB_ID; mkdir -p "$gates"
cd /home/u4982363/experiments/scaling-law-pilot
pids=(); gated_pids=(); gated_names=()
for spec in "$@"; do
  IFS=: read -r sweep slots mode <<< "$spec"
  name=$(basename "$sweep" .yaml)
  if [ "${mode:-}" = gated ]; then
    (
      while :; do
        missing=$(python - "$sweep" <<'PY'
import sys, yaml, pathlib
runs = yaml.safe_load(open(sys.argv[1]))["sweep"]["runs"]
print(sum(not pathlib.Path(r["branch_from"]).is_dir() for r in runs))
PY
)
        [ "$missing" = 0 ] && break
        sleep 120
      done
      echo "gate open: all branch_from checkpoints exist"
      touch "$gates/$name"
      exec python -m flock.training.experiments.scaling_law.sweep launch "$sweep" --max-slots "$slots"
    ) > >(sed -u "s|^|[$name] |") 2>&1 &
    gated_pids+=($!); gated_names+=("$name")
  else
    python -m flock.training.experiments.scaling_law.sweep launch "$sweep" --max-slots "$slots" \
      > >(sed -u "s|^|[$name] |") 2>&1 &
    pids+=($!)
  fi
done
all=("${pids[@]}" "${gated_pids[@]}")
# Slurm signals only this shell; pass the time-limit SIGTERM on so every launcher stops its runs.
trap 'echo "SIGTERM: stopping launchers"; kill -TERM "${all[@]}" 2>/dev/null' TERM
status=0
waitfor() {
  local code
  while :; do wait "$1"; code=$?; kill -0 "$1" 2>/dev/null || break; done
  [ "$code" -eq 0 ] || status=1
}
for pid in "${pids[@]}"; do waitfor "$pid"; done
for i in "${!gated_pids[@]}"; do
  if [ ! -e "$gates/${gated_names[$i]}" ]; then
    echo "[${gated_names[$i]}] ungated launchers finished and gate still closed: stopping it"
    kill -TERM "${gated_pids[$i]}" 2>/dev/null
  fi
done
for pid in "${gated_pids[@]}"; do waitfor "$pid"; done
kill "$monitor" 2>/dev/null
rm -rf "$gates"
exit $status
