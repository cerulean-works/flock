#!/bin/bash
# Train Llama 3.2 1B for 200 tokens per parameter (train_llama32.py) on 16 GB200 GPUs.
#
# Submit from the repository root (logs need their folder first):
#   mkdir -p /work/u4982363/canary/11w4/logs
#   sbatch flock/training/experiments/canary/train_llama32.sbatch
#
# The run takes about 35 h. Training resumes from the newest checkpoint by itself. `--requeue`
# restarts the job if Slurm loses a node, and a failed launch is retried in the allocation.
# Once training finishes it writes DONE in the output directory, and later starts exit at once.
#SBATCH --job-name=canary-11w4
#SBATCH --account=ACD115198
#SBATCH --partition=gb200_72gpu
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=4
#SBATCH --cpus-per-task=136
#SBATCH --mem=0
#SBATCH --time=3-00:00:00
#SBATCH --requeue
#SBATCH --open-mode=append
#SBATCH --output=/work/u4982363/canary/11w4/logs/%x-%j.out
set -euo pipefail

OUTPUT_DIR=/work/u4982363/canary/11w4
REPO=/home/u4982363/experiments/canary/11w4-code
ENV_SH=$REPO/flock/training/experiments/canary/train_llama32.env.sh
GPUS_PER_NODE=4
MAX_ATTEMPTS=3

echo "$(date '+%F %T %Z') job $SLURM_JOB_ID restart ${SLURM_RESTART_COUNT:-0} on $SLURM_JOB_NODELIST"
if [[ -e $OUTPUT_DIR/DONE ]]; then
    echo "Training already finished ($OUTPUT_DIR/DONE)"
    exit 0
fi

source "$ENV_SH"
mkdir -p "$OUTPUT_DIR/logs" "$WANDB_DIR" "$FLASH_ATTENTION_CUTE_DSL_CACHE_DIR"
# Build or check the aarch64 venv once, on this node, before every rank imports it.
(cd "$REPO" && uv sync --all-packages --locked)

FIRST=$(scontrol show hostnames "$SLURM_JOB_NODELIST" | head -n 1)
PORT=29511

for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
    echo "$(date '+%F %T %Z') launch attempt $attempt of $MAX_ATTEMPTS"
    status=0
    # --propagate=NONE: do not copy the submitting shell's soft 2048-process limit.
    # --cpus-per-task: srun does not inherit it from sbatch. --cpu-bind=none: let each node's ranks and loader workers use all of its CPUs.
    srun --propagate=NONE --cpu-bind=none --cpus-per-task="$SLURM_CPUS_PER_TASK" --kill-on-bad-exit=1 \
        --nodes="$SLURM_JOB_NUM_NODES" --ntasks="$SLURM_JOB_NUM_NODES" --ntasks-per-node=1 \
        bash -c "
source '$ENV_SH'
cd '$REPO'
exec python -m torch.distributed.run --nnodes=$SLURM_JOB_NUM_NODES --nproc-per-node=$GPUS_PER_NODE \
    --node-rank=\$SLURM_PROCID --master-addr=$FIRST --master-port=$PORT \
    -m flock.training.experiments.canary.train_llama32" || status=$?
    if [[ -e $OUTPUT_DIR/DONE ]]; then
        echo "$(date '+%F %T %Z') training finished"
        exit 0
    fi
    echo "$(date '+%F %T %Z') launch exited with status $status before DONE"
    sleep 60
done
exit 1
