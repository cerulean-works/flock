# Python API

The entry points for running evaluations, and the model wrapper they score with. Task classes are in [Tasks](tasks), and result classes in [Results files](results).

## Running

[[autodoc]] eval.run.evaluate

[[autodoc]] eval.run.EvalConfig

[[autodoc]] eval.run.score_task

## Scoring

[[autodoc]] eval.model.ScoringModel

[[autodoc]] eval.model.ModelRef

[[autodoc]] eval.requests.Request

[[autodoc]] eval.requests.Scores

## Comparing

[[autodoc]] eval.compare.compare

[[autodoc]] eval.compare.Comparison

[[autodoc]] eval.compare.Breach
