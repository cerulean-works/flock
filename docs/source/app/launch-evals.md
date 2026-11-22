# Launch evals from the dashboard

Score a model on `flock eval` suites and tasks from a project's page. The server runs the eval on an H100 on [Modal](https://modal.com) and logs it as a run of the project, with its `results.json` and per-doc predictions, so it shows on the run's **Results** tab like any eval run. Launches run in the background, so you can close the page while they run.

## Before you start

- You need to be a member or an admin of the organization.
- The server must launch evals: its admin sets the Modal token and the GPU price (see [Set up launches](#set-up)). Without them, the project page shows no **Launch eval** button.
- The model must be a public Hugging Face repository. Gated and private models, and models in the Flock registry, can't be launched yet.
- Only suites and tasks whose datasets are all public can be launched. The dialog lists those. A suite that holds one task on a private dataset, such as `zh_tw` (it holds the mock exams), isn't listed.

## Launch an eval

1. Open the project the run should go to, and click **Launch eval**.
2. Enter the model's repository, such as `allenai/OLMo-2-0425-1B`, and its **Revision** (`main` by default). The server resolves the revision to a commit when you launch, so the run scores that commit even if the branch moves later.
3. Pick the suites and tasks to score.
4. Check **Add BOS** for Gemma models, which are scored with a BOS token as OLMES does.
5. Click **Launch**.

Every launch is scored the same way: BF16 weights and 16,384 batch tokens, the settings of the reference eval bank. You can't change them, so two launches of the same tasks are always comparable.

## Follow a launch

Running launches show as rows on the project page. A row says what is scored, who launched it and its state:

| State | Meaning |
|---|---|
| `running` | The eval is waiting for a GPU or scoring. |
| `finished` | The run is logged. **Open run** goes to it. |
| `failed` | The row shows why. A failed launch logs nothing, or a run cut short. |
| `cancelled` | Someone cancelled it. What it logged so far stays. |

Click **Cancel** to stop a running launch. Only whoever launched it, or an admin, can cancel it. Its GPU stops at once.

## Costs and caps

Each launch has a cap (US$25 by default). The server turns it into a time limit at the GPU price, so a launch stops before it costs more. The month's launches share a second cap (US$300 by default), counted over the whole server. A running launch counts at its full cap until it ends, and an ended one at its time from launch to end. When a new launch wouldn't fit under the monthly cap, the server refuses it.

## Comparing launched runs

A launched run records the device it ran on (`NVIDIA H100`) in its `results.json`. The reference eval bank was scored on H200 GPUs. Scores on another GPU can differ slightly, so compare a launched run with the bank only once a parity check on the same models and tasks has shown the two agree within the bank's tolerance.

## Set up launches[[set-up]]

An admin of the server does this once per Flock release:

1. Create a Modal token for the server, not one shared with CI, in the Modal workspace that pays for launches.
2. Deploy the worker from a clean checkout of the release the server runs, with that token:
   ```bash
   MODAL_TOKEN_ID=... MODAL_TOKEN_SECRET=... uv run modal deploy app/api/eval_worker.py
   ```
   The worker scores with that commit's `flock eval`, and each run records the commit.
3. Set [`FLOCK_MODAL_TOKEN_ID`, `FLOCK_MODAL_TOKEN_SECRET` and `FLOCK_EVAL_LAUNCH_USD_PER_HOUR`](configuration) on the API, with `FLOCK_API_PUBLIC_URL`, which the worker logs runs to.

The worker gets no Hugging Face token and no Flock storage key, so it can't read a private dataset or model. It logs to the tracker with a wandb key that the server makes for each launch and deletes when the launch ends. A wandb key reaches the tracker only, not the rest of the API.
