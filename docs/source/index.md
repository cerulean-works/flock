# Flock

Flock is Cerulean's workspace for tracking experiments, evaluating language models and accessing shared storage. The CLI already points to Cerulean's setup.

[Install the workspace](installation), then sign in:

```bash
uv run flock login
```

Approve the code in your browser and accept the SDK setup to log experiments. [Sign in and manage access](cli/sign-in) explains what the login creates. Open [Cerulean's dashboard](https://flock.cerulean.works) to see your organizations, projects and runs.

## Choose a workflow

| Module | What you can do |
|---|---|
| CLI | [Manage access](cli/sign-in), [use S3 storage](cli/s3-access), [access storage from Python](cli/use-python-storage), or [authenticate a job](cli/authenticate-jobs). |
| Evals | [Evaluate a model and read its scores](evals/run-evaluation), [score a checkpoint during training](evals/evaluate-checkpoint), or [add a task](evals/add-task). |
| App | [Set up your account](app/accounts), [track an experiment](app/log-runs), [compare runs](app/explore-runs), or [browse storage and datasets](app/storage-and-datasets). |

The guides introduce the concepts you need as part of each workflow. **Reference** uses the same CLI, Evals and App groups for complete commands, APIs and technical behavior.

## Developing or hosting Flock

Cerulean users can use the existing service. To work on the app, [run it locally](app/run-locally). To host another installation, use [Deploy Flock](app/deploy) and [Operate Flock](app/operate).

`flock.training` is a scaffold on this branch. Evaluation during training uses `flock.eval` with a model your training code already holds.
