---
name: cerulean-docs
description: Write or edit documentation for a Cerulean project. Use when adding or changing pages in a docs/source folder (anything with a _toctree.yml), when writing docstrings that appear in an API reference, when a code change needs its docs updated, or when setting up docs for a new project. Covers where a page goes (Guide or Reference), doc-builder's Markdown syntax, the house writing style, and how to check the build before finishing.
---

# Writing Cerulean docs

Cerulean projects publish their docs with [doc-builder](https://github.com/cerulean-works/doc-builder) to `docs.cerulean.works/<project>/`. Each project's docs are a folder of Markdown pages, usually `<project>/docs/source/`, plus the docstrings of its Python package. doc-builder turns them into a site with two sections:
- **Guide**: pages people read through, such as tutorials, how-tos and explanations.
- **Reference**: pages people look things up in, such as the API, configuration keys and CLI flags.

Full documentation: https://docs.cerulean.works/doc-builder/latest/guide/ (its Markdown source is `docs/source/` in the doc-builder repository).

Work through the steps below in order. Don't skip the last one: a page that doesn't build blocks the project's release.

## 1. Orient yourself

Before writing anything:

1. **Find the docs folder.** It is the folder that holds `_toctree.yml`:
   ```bash
   find . -name _toctree.yml -not -path "*/node_modules/*"
   ```
   If the project has none yet, see [New projects](#new-projects) at the end.
2. **Read `_toctree.yml`** to learn the existing sections and groups, and where your page fits.
3. **Read `_config.py`** if it exists, for the project's title and repository.
4. **Find the project name.** It is the `project:` input in the workflow that calls `build_docs.yml`, under `.github/workflows/`. You need it for the build commands. It is often, but not always, the folder name.
5. **Read two or three neighbouring pages.** Match their structure, their running example and how they name things.
6. **Read the code you are documenting.** Every function name, flag, default value and behaviour you write down must come from the source or from running it, never from memory or from what seems likely. If you can't verify a claim, leave it out or ask.

## 2. Decide where the page goes

Pick the section by how the page will be read:

| The reader... | Section | Examples |
|---|---|---|
| follows it from start to finish | Guide | quickstart, tutorial, "Deploying to staging", "How caching works" |
| jumps to one entry and leaves | Reference | API classes, `_config.py` keys, CLI flags, error codes |

Then:
- **Prefer editing an existing page** to adding a new one. Add a page when the topic would make an existing page lose its focus.
- **One page, one topic.** One long page with clear `##` sections reads better than several tiny pages.
- **Each fact has one home.** If something is already explained, link to it rather than explaining it again.

## 3. Add the page

1. **Create the file** in the docs folder: `kebab-case.md`, or in a subfolder such as `tutorials/first-agent.md`.
2. **List it in `_toctree.yml`** under `guide:` or `reference:`, with `local` set to the path without extension:
   ```yaml
   guide:
     - title: Tutorials
       sections:
         - local: tutorials/first-agent
           title: Your first agent
   ```
   - Every page must appear exactly once, in one section. The build fails on an unlisted file, a missing file or a duplicate.
   - `local: index` (or `foo/index`) becomes the folder's URL. Don't also have a `foo` page next to `foo/index`.
   - Entries may only use the keys `title`, `local`, `sections` and `isExpanded`.
3. **The URL** becomes `/<project>/<version>/<guide|reference>/<local>/`. Moving a page between sections changes its URL but not its file name.

## 4. Write it

### Structure

- **Title:** exactly one `#` heading, at the top. It names the topic or task in plain words: "Publishing a project", not "Publishing 101" or "Everything about publishing".
- **Opening:** one or two sentences saying what the page covers or what the reader will be able to do. Don't announce it ("In this page we will…"); say it.
- **Body:** `##` sections in the order the reader needs them, with `###` below them only when a section is long. Headings use sentence case and describe their content.
- **Guide pages:** lead with the common path, and put options and edge cases after it. A how-to lists what's needed first, then numbered steps, then how to check it worked.
- **Reference pages:** complete and scannable. Use a table for every set of options, with a row per option and its default. Keep explanations short, and link to the Guide for the "why".

### Style

Write the way the existing docs are written:
- **Address the reader as "you"**, in the present tense and the active voice: "The build fails if…", not "The build will fail if…" or "An error is raised by…".
- **Use short sentences**, one idea each. If a sentence needs a semicolon or a second "and", split it.
- **Use lists for three or more parallel items**, and tables for facts the reader compares: options, flags, differences between modes.
- **Name things exactly** as they appear in code and in the interface. Put identifiers, file names, commands and literal values in backticks, and interface labels in bold: **Copy page**.
- **Use one running example** throughout a project's docs (in doc-builder's docs it is a project called `flock`). Never use `foo` or `bar` when a realistic name is available.
- **Explain why** when a rule or an order isn't obvious: "Delete the marker first, so the version disappears from the menus before its pages do."
- **Avoid:**
  - filler and sales words: "simply", "just", "easily", "powerful", "seamless", "robust", "leverage"
  - hedging, such as "should probably" or "might want to", when you can state the fact
  - em dashes; use a colon, a comma, parentheses or a new sentence instead
  - "e.g." and "i.e."; write "for example" and "that is"
  - describing future plans or features that don't exist yet

### Code examples

- Give every fence a language: `bash`, `python`, `yaml`, `json`, `ts`, `diff`. Use `text` for directory trees and plain output, so nothing gets highlighted by mistake.
- Commands must work when copied as they are. Put one command per line, with no `$` prompt, and show output only when the reader needs to recognise it.
- Keep examples minimal and realistic, and make sure they actually run. For Python, consider a [runnable example](https://docs.cerulean.works/doc-builder/latest/guide/testing-examples/) so CI tests it.

### Callouts

Use them sparingly, at most one or two per page, and never two in a row:

```md
> [!WARNING]
> Deletes in R2 are permanent.
```

| Type | Use it for |
|---|---|
| `NOTE` | context that is useful but not essential |
| `TIP` | a shortcut or a better way to do something |
| `IMPORTANT` | something the reader must do for the result to work |
| `WARNING`, `CAUTION` | data loss, security problems, or actions that can't be undone |

## 5. doc-builder syntax

Standard Markdown works, plus the following. The full list is in the [Markdown syntax reference](https://docs.cerulean.works/doc-builder/latest/reference/syntax/).

| Want | Write |
|---|---|
| Link to another page | `[Publishing](publishing)` or `[Publishing](publishing.md)`: the path relative to this file, which works across Guide and Reference |
| Link to a heading | `[Routes](worker#routes)`, or `[Routes](#routes)` on the same page |
| Choose a heading's anchor | `## Removing a version[[remove-version]]` |
| Link to an API object | `` [`Flock`] ``, `` [`Flock.run`] ``, or `` [`~utils.load_config`] `` to show only `load_config` |
| API docs for an object | `[[autodoc]] Flock` on its own line. Paths are relative to the package. |
| Image | `![Alt text](imgs/loop.png)`, with the file inside the docs folder |
| Tabs | `<tabs id="install">` containing `<tab id="pip">…</tab>`, with blank lines around Markdown inside a tab |
| Diagram | a ` ```mermaid ` code block |
| Math | `$$…$$` for display, `\\( … \\)` inline |
| Version notes | `<Added version="1.2.0">…</Added>`, and likewise `<Changed>` and `<Deprecated>` |
| Code from a file | `<literalinclude>{"path": "../../examples/x.py", "language": "python"}</literalinclude>` |

Links:
- **Link to pages in the same docs with relative links**, never with `https://docs.cerulean.works/...`. Relative links always stay within the version the reader is on.
- **Link to another project's docs with an absolute `latest` URL**, for example `https://docs.cerulean.works/flock/latest/guide/quickstart/`.
- **Never link to `.html` files** or to built output.

Syntax inside fenced code blocks is shown exactly as written, so examples of the syntax above are safe to put in fences.

## 6. Docstrings

API reference pages are generated from docstrings by `[[autodoc]]`. When you write or change a public function, class or method, write its docstring in Google style, using Markdown rather than reStructuredText:

````python
def run(self, task: str, timeout: float | None = None) -> RunResult:
    """
    Runs `task` on every agent in the flock and waits for the results.

    Args:
        task (`str`):
            What the agents should do, in plain language.
        timeout (`float`, *optional*):
            Seconds to wait before cancelling. Waits forever if not set.

    Returns:
        [`RunResult`]: One result per agent, in the order the agents were added.

    Raises:
        `TimeoutError`: if `timeout` passes before every agent has finished.

    Example:

    ```python
    >>> flock = Flock(size=2)
    >>> flock.run("count to three").ok
    True
    ```
    """
````

- **Types** go in backticks, followed by `*optional*` and `defaults to …` where they apply. Leave out "defaults to `None`".
- **Cross-reference** other objects with `` [`Name`] `` here too.
- **The first line** says what the object does, in the present tense.

The full format is in the [Docstring format reference](https://docs.cerulean.works/doc-builder/latest/reference/docstrings/).

## 7. Check before you finish

Run these from the repository that holds the docs. If doc-builder isn't installed, install it with `pip install git+https://github.com/cerulean-works/doc-builder`. `<project>` and `<docs>` are the project name and docs folder from step 1.

1. **Fast build.** This checks the toctree, autodoc and conversion, and takes seconds. For autodoc, the project's package must be installed in the same environment:
   ```bash
   doc-builder build <project> <docs> --build_dir /tmp/docs-check
   ```
   Add `--not_python_module` if the project's workflow sets `not_python_module: true`, and `--mock_deps a,b` if it sets `mock_deps`.
2. **Links.** This checks every relative link and anchor:
   ```bash
   doc-builder check-links <docs>
   ```
3. **Runnable examples**, if the page has any:
   ```bash
   pytest -q <docs>/<page>.md
   ```
4. **Look at the page** when you changed layout, tables, tabs, diagrams or images. This needs Node.js 20 or newer and pnpm (`npm install -g pnpm`):
   ```bash
   doc-builder preview <project> <docs>
   ```
   Open `http://localhost:5173/<project>/main/guide/` (or `…/reference/`) and check the page renders as intended. If you can't open a browser, run a full build instead (`doc-builder build … --html --build_dir /tmp/docs-site`) and confirm it succeeds.

Then go through this checklist:
- [ ] The page is listed in `_toctree.yml`, in the right section.
- [ ] Every name, flag, default and behaviour was checked against the code.
- [ ] Code blocks have languages, and the commands work as written.
- [ ] Links to the same docs are relative, and the link checker passes.
- [ ] Pages that mention what you changed (other guides, the README, the docstrings) are updated in the same change.
- [ ] Nothing in `build_dir`, `.svelte-kit` or other generated output was committed.

## New projects

If a project has no docs folder yet:

1. Create `docs/source/` with an `index.md`, a `_toctree.yml` with at least a `guide:` entry for it, and a `_config.py` that sets `project_title`, `project_description` and `repo`. The [Quickstart](https://docs.cerulean.works/doc-builder/latest/guide/quickstart/) has a minimal example.
2. Choose the project name. It is the first part of every URL and must be unique across Cerulean: letters, digits, `_` and `-`.
3. Adding the publishing workflows is a separate step, which the maintainers need to approve because it uses organization secrets. Follow [Publishing a project](https://docs.cerulean.works/doc-builder/latest/guide/publishing/), or tell the user it's still to do.
