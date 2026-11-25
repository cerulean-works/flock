#!/bin/bash
#SBATCH --partition=gb200_72gpu
#SBATCH --account=ACD115198
#SBATCH --gpus-per-node=4
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=136
#SBATCH --mem=900000M
#SBATCH --time=0:05:00
#SBATCH --output=/work/u4982363/sweeps/logs/%x-%j.out
source /home/u4982363/experiments/.tools/env.sh
cd /home/u4982363/experiments/scaling-law-pilot
# Print the launcher's real srun options for a 1-GPU slot, then run four such steps at once.
python - <<'PY' > /tmp/srun_opts
from pathlib import Path
from flock.training.experiments.scaling_law.sweep import Allocation, RunDirectory, Slot, SlotLayout, train_command
c = train_command(RunDirectory(Path("/x/runs/p")), SlotLayout.plan(Allocation(1, 4), 1), Slot(0), mode="srun", master_port=1)
print(" ".join(c[c.index("--exact"):c.index("bash")]))
PY
opts=$(cat /tmp/srun_opts); echo "srun opts: $opts"
for i in 0 1 2 3; do
  srun $opts bash -c 'echo "step $SLURM_STEP_ID start $(date +%T) CVD=$CUDA_VISIBLE_DEVICES cpus=$(nproc) $(nvidia-smi --query-gpu=pci.bus_id --format=csv,noheader)"; sleep 20' &
done
wait
