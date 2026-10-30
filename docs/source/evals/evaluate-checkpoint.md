# Evaluate a training checkpoint in memory

Score a model that your training code already holds, without saving and reloading its weights.

## Before you start

Your training process must hold a causal language model and tokenizer compatible with `ScoringModel`. The names `model`, `tokenizer`, `run` and `step` below refer to your existing training objects and step counter. Set aside enough memory for evaluation batches.

## Score and log a checkpoint

1. At an evaluation point in your loop, wrap the model:

   ```python
   from flock.eval.model import ScoringModel
   from flock.eval.run import score_task
   from flock.eval.tasks import TASKS

   scorer = ScoringModel(model, tokenizer, name=f"llm-pretrain@step-{step}")
   ```

   A Flock model, such as `Olmo2`, takes a batch rather than the arguments of a transformers model. Wrap it in `FlockCausalLM` first, with the tokenizer its config names:

   ```python
   from flock.eval.model import FlockCausalLM

   scorer = ScoringModel(
       FlockCausalLM(model), model.get_hf_tokenizer(), name=f"llm-pretrain@step-{step}"
   )
   ```

2. Score a registered task:

   ```python
   result = score_task(scorer, TASKS["arc_easy"])
   print(result.metrics["bpb"])
   ```

3. If the training run logs through wandb, attach metrics to that run's step:

   ```python
   run.log({f"arc_easy/{name}": value for name, value in result.metrics.items()}, step=step)
   ```

4. Check the printed metric and the training run's dashboard. Continue training after scoring returns.

Scoring disables gradients and restores the modules' previous training modes. It uses the model's existing device and dtype. Keep evaluation settings consistent between checkpoints. See [Scoring](scoring) and the [Python API](python-api) for batching and model-state behavior.

`score_task` returns metrics in memory. With `out`, it writes predictions only, not `results.json`; give each checkpoint its own output path. See [Results](results).
