# Task definitions

The task definition controls the requests an evaluation scores. For the contribution workflow, see [Add a task](add-task).

## The prompt format

Each [`~eval.tasks.Choice`] of a doc is one request: its `continuation` is scored after the task's `description`, the few-shot examples and its `context`. Each example is its gold choice's context and continuation (or the doc's `example`, below), followed by a blank line. An example from another split that shows the same text as the scored doc would as an example is left out for that doc, so a dataset that repeats a question and its answer in its few-shot split, in any choice order, never shows a doc its own answer. Written-out `Fewshot.rows` are shown to every doc, and so are examples from the scored split, its own included, unless `Fewshot.num_shots` is set, as OLMES shows its curated examples. With [`~eval.tasks.cloze`], every choice shares the context `Question: <question>\nAnswer:` and its continuation is `" <choice>"`:

```text
Question: <few-shot question 1>
Answer: <its gold choice>

...

Question: <question>
Answer:
```

For pointwise mutual information (PMI, `acc_uncond`), each choice is scored a second time after the task's `unconditional_context` alone (`Answer:` by default).

A task with another format writes its own choices in `to_doc`:
- **Other prefixes:** `cloze(goal, answers, question_prefix="Goal: ")`.
- **Traditional Chinese:** `cloze(question, answers, question_prefix=CHINESE_QUESTION_PREFIX, answer_prefix=CHINESE_ANSWER_PREFIX, separator="")` renders `問題：<question>\n答案：` with each choice right after it, with no space, as TMMLU+ does. Set the task's `unconditional_context` to `CHINESE_ANSWER_PREFIX` too, unless it is a [`~eval.tasks.GoldOnlyTask`], which has none (as DRCD's `drcd_bpb`). Chinese text has no spaces between words, so a leading space would split the first character into unusual tokens and count in `acc_per_char`.
- **A context per choice:** give each `Choice` its own `context` and the same `continuation`, as WinoGrande does.
- **A gold text only**, such as a math solution: one `Choice` with `gold=0`, and the task built as a [`~eval.tasks.GoldOnlyTask`], which reports `bpb` alone and skips PMI, since a multiple-choice accuracy over one choice is always 1. Pass `primary_metric="acc_greedy"` and `metrics=("acc_greedy", "bpb")` to also report whether greedy decoding would produce the gold text, as LAMBADA does.
- **Examples that read differently from the scored text:** set the doc's `example`. HumanEval's examples put a space before the solution that the scored doc doesn't have, as OLMES does.
- **Text before the examples**, such as a subject header: the task's `description`.
- **Many tasks of one kind**, such as MMLU's 57 subjects: build them in a loop over a list of names, and group them in a suite, as `flock/eval/tasks/mmlu.py` does.
- **Only some rows scored**, such as single-answer items of a set that also has multi-answer ones: set the task's `keep` to a module-level function of a row that returns whether to score it. It runs before `limit` and `to_doc`, and doesn't filter few-shot examples.
- **Examples from the scored split:** set `Fewshot.num_shots` and list one id more than it. Each doc is shown the first `num_shots` examples that aren't the doc itself. The ids must come from the split the task scores, since an id identifies a doc within one split; the task rejects any other.

A changed prompt is a different task, and its numbers don't compare with the old ones, so give it a new name.


## The task hash

Each result records the task's [hash](results#the-task-hash), so results from before and after a change to the task never share one.
