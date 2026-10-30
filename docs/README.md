# Working on Flock documentation

All public documentation lives in `docs/source/`. The Guide groups content by CLI, Training, Evals and App; Reference covers CLI, Evals and App. The single-page trainer guide lives in `docs/source/training/`. The files live under matching module folders; `_toctree.yml` assigns each page to a tab. Internal implementation notes under component `docs/agents/` folders are not published.

Write complete workflows rather than a page for every command or small action. Explain the concepts needed for the task before giving commands: for example, S3 keys, expiry and renewal belong before key generation. Keep related steps together and link directly to sections when a reader has a narrower question.

Make the common path Cerulean-specific. Use `uv run flock login`, the configured service and the account's default organization. Keep host overrides and other-server setup in reference or the development/hosting guides. Preserve precise descriptions of what the defaults do.

Keep full option tables, protocol details and nuanced edge cases in the module reference. Verify every claim against the code on the branch being documented.

Install Python 3.14 and [doc-builder](https://github.com/cerulean-works/doc-builder) in an environment where the Flock root package is installed. The same dependency mocks are used by production and PR builds so API reference generation does not load models or download data. Run from the repository root:

```bash
doc-builder build flock docs/source --version main --mock_deps torch,transformers,huggingface_hub,datasets,tokenizers,wandb,httpx --build_dir /tmp/flock-docs-check
doc-builder check-links docs/source
doc-builder build flock docs/source --version main --mock_deps torch,transformers,huggingface_hub,datasets,tokenizers,wandb,httpx --html --build_dir /tmp/flock-docs-site
doc-builder preview flock docs/source --mock_deps torch,transformers,huggingface_hub,datasets,tokenizers,wandb,httpx
```

Open `http://localhost:5173/flock/main/guide/` and inspect both tabs, cross-module links and API rendering. Every page must appear once in `_toctree.yml`. Keep generated output outside the repository.

The existing Flock docs workflows build this tree, publish the `flock` project, and create PR previews. CLI and evaluation docs are included in that build. Published paths follow the new module folders; old unversioned page paths have no migration pages.

## Generated source links

The current shared doc-builder workflow assumes Python source lives under `src/` when generating the API reference's **source** links. Flock's packages live at the repository root, so those generated links currently include an extra `src/` segment. The API contents, page links and **Edit on GitHub** links are unaffected. This is a shared-builder limitation; this rewrite does not patch the builder.

For a manual build with correct API source links, add `--version_tag_suffix ""` to the `doc-builder build` command. The reusable workflow needs an input for that option before production builds can use the same override.
