# Environment for train_llama32 on GB200 (aarch64) nodes, sourced on every node by
# train_llama32.sbatch. Adapted from the trainer e2e validation's env-gb200.sh.

# aarch64 uv and a venv of this worktree's lockfile, separate from the x86 .venv.
export PATH=/home/u4982363/experiments/.tools/uv-aarch64:$PATH
export UV_PROJECT_ENVIRONMENT=/home/u4982363/experiments/.venvs/canary-11w4-gb200
export UV_CACHE_DIR=/work/u4982363/cache/uv-aarch64
export VIRTUAL_ENV=$UV_PROJECT_ENVIRONMENT
export PATH=$VIRTUAL_ENV/bin:$PATH

# The Llama 3.2 tokenizer and the evaluation datasets are cached here; nodes read offline.
export HF_HOME=/work/u4982363/hf HF_TOKEN_PATH=$HOME/.cache/huggingface/token HF_HUB_OFFLINE=1
# W&B logs to Flock's API, as `flock login` configured it.
export WANDB_BASE_URL=https://flock-api.cerulean.works
export WANDB_DIR=/work/u4982363/canary/11w4/wandb

ulimit -n 65535
# The per-user thread cap (soft 2048) is exhausted by tokenizer, Lance and OpenMP pools sized
# to a 144-CPU node: raise it and cap the pools.
ulimit -u "$(ulimit -Hu)" 2>/dev/null || true
export RAYON_NUM_THREADS=${RAYON_NUM_THREADS:-4} LANCE_CPU_THREADS=${LANCE_CPU_THREADS:-4}
export LANCE_IO_THREADS=${LANCE_IO_THREADS:-8} OMP_NUM_THREADS=${OMP_NUM_THREADS:-4}
export TORCHINDUCTOR_COMPILE_THREADS=${TORCHINDUCTOR_COMPILE_THREADS:-8}
export TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1

# FA4 JIT-compiles kernels per rank on first use; share a persistent cache across restarts.
export FLASH_ATTENTION_CUTE_DSL_CACHE_ENABLED=1
export FLASH_ATTENTION_CUTE_DSL_CACHE_DIR=/work/u4982363/canary/11w4/fa-cache
# Traffic beyond one NVL72 domain uses IB; NCCL's default HCA list includes the RoCE ports
# (mlx5_2,3,6,7) and fails with "async fatal event on QP". Pin the IB HCAs (exact match).
export NCCL_IB_HCA==mlx5_0,mlx5_1,mlx5_4,mlx5_5
# torch 2.14 Inductor CPU kernels (compiled evaluation) target -march=armv9-a+sve2, which the
# system g++ 11.5 rejects. Use the generic CPU target.
export ATEN_CPU_CAPABILITY=default
export TORCH_NCCL_ASYNC_ERROR_HANDLING=1
