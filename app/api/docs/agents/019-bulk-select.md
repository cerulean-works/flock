# 019: selecting many datasets or models, and a menu on each row

| | |
| --- | --- |
| Status | proposed |
| Date | 2026-11-14 |
| Builds on | [013](013-datasets-tab.md), [014](014-model-registry.md), [017](017-hf-compat.md) |
| Amends | D93 (folders go to the trash too), D95 (folders are renamed too), D99 (a model's name and its folder can differ), D108 (the models list gets the same selection) |
| Keeps | D85 to D92, D94, D96 to D98, D100 to D107, D109 |

## 1. Goal

Today a dataset or model is renamed or deleted one at a time, from its own page. This stage
adds the same actions to the lists, for one row or many:

1. **Selection.** Hovering a row shows a checkbox on its left. Checking rows shows a bar with
   what can be done to all of them (D130).
2. **Moving and deleting many datasets** at once, folders included (D131, D132).
3. **A ⋯ menu on each row** with Rename and Delete (D133).
4. **Renaming models,** which isn't possible yet (D134).
5. **A copy button in every type-to-confirm prompt,** and the name in it can be selected (D135).

**Out of scope:**
- selecting across pages ("Load more" loads more rows into the same selection, but there's no
  "select all 3,000");
- moving datasets to another org, or models between orgs;
- selecting loose files in the Datasets tab (the Storage tab handles files);
- drag and drop.

---

## 2. Design decisions

The numbering continues from 017 (018 is taken on another branch).

### D130. Selection

- **Where:** the Datasets tab's folder listing (at any folder) and the Models tab's list, for
  members and admins. Viewers of a public dataset (015) see no checkboxes.
- **What can be selected:** datasets and folders in the Datasets tab, and models in the Models
  tab. Loose files have no checkbox.
- **The checkbox** sits in a narrow first column, before the icon. It's hidden until the row is
  hovered or focused. Once anything is selected, every row's checkbox shows, so the rest can be
  checked without hunting. The header has a checkbox that selects every loaded row, and shows as
  partly checked when some are.
- **Clicking** the checkbox toggles the row. Shift-click selects the range from the last row
  clicked, as the runs table does. Clicking the row's name still opens it.
- **A selected row** gets the runs table's selection background.
- **The bar.** With anything selected, the header's buttons (Import from Hugging Face, New
  folder) are replaced by "3 selected", the actions and a Clear button. The actions are Move and
  Delete for datasets, and Delete for models (D134 says why there's no Move). Escape clears.
- **The selection is kept** while more rows load, and cleared when the folder or org changes.

### D131. Moving datasets and folders

- **The dialog** asks for a destination folder, defaulting to the folder shown, with a folder
  picker that browses `flock-datasets` (folders only). Each selected item keeps its last name:
  `eval/mmlu` moved to `archive` becomes `archive/mmlu`.
- **A folder moves with everything in it,** datasets and loose files, as one prefix. The rename
  rules of D95 apply to each item: nothing may exist at the new path, and an item can't move
  into itself.
- **One request, item by item.** `POST datasets/move {paths, to}` runs D95's copy-then-delete
  for each item in turn. A folder's datasets each go through D95's order, so none is ever
  broken. The limits (10,000 objects, 100 GiB) apply to the whole request, checked before
  anything moves.
- **When one item fails** (a name already taken, a dataset written to during the move), the rest
  still move. The answer lists each item as moved or not, with the reason, and the dialog shows
  the ones that didn't move. Moved rows leave the list and the selection.
- **One item** moved from the row menu is the same as D95's Rename dialog, below.

### D132. Deleting datasets and folders

- **The dialog** lists what will be deleted (up to 10 names, then "and 4 more") and asks to type
  the number of items, as the runs table's delete does for many runs. For one item it asks for
  its path, as D93's dialog does. It says "Org admins can restore them for 14 days."
- **A folder goes to the trash as one entry,** everything under its prefix, and is restored as a
  whole. `dataset_trash` gets a `kind` column (`dataset` or `folder`), and its "Deleted
  datasets" section in org settings shows folders with a folder icon. Restore and Delete now work
  as D93 says, for either kind.
- **The limits are D93's,** for the whole request. Above them, the API refuses before moving
  anything, with the `aws s3 rm` command.
- **Failures** are reported item by item, as moves are (D131).
- `POST datasets/delete` takes `paths` (a list) in place of `path`. One path still works.

### D133. The row menu

- **Every dataset, folder and model row** gets a ⋯ button at its right end, shown on hover and
  always on touch screens. Loose files get none.
- **Datasets and folders:** Rename and Delete. Rename opens D95's dialog with the full path to
  edit, so it moves as well as renames. For a folder it renames the prefix, under D131's rules.
- **Models:** Rename (D134) and Delete (D109's dialog).
- **The dataset and model pages keep** their own Rename and Delete buttons.

### D134. Renaming models

- **A model's folder stops following its name.** `models` gets a `prefix` column, set to the
  name for every existing model and for new ones. Uploads and downloads use the prefix, not the
  name. Renaming changes only the name, in the registry, so it's instant whatever the model's
  size. D99 tied the two so the folder's name never went stale. Copying hundreds of gigabytes
  on every rename is the worse trade.
- **The Storage tab** still shows the folder by its old name. The model page's "Browse files"
  links to the right folder, since it reads the prefix.
- **The new name** follows D98's rules and must be free in the org, including names in the
  trash. A rename is refused while an upload to the model is open, since the client holds the
  old name.
- **Old refs stop resolving.** `acme/old-name@3` gets a 404 after the rename, with no redirect.
  Runs that link to the model keep linking, since links use the model's id.
- **No Move for models.** Model names have one segment (014, section 6), so there's no folder
  to move into. Renaming covers the rest.

### D135. Copying the name in delete prompts

Added at the user's request while this doc was written.

- **Every type-to-confirm prompt** gets a copy button next to the text to type, so it can be
  pasted instead of typed. That's the delete dialogs for orgs, projects, buckets, runs,
  datasets (delete and delete now), models (delete and delete now) and versions, and the new
  ones in D131 and D132.
- **The text to type can be selected.** It sits inside a `Label`, whose `select-none` stopped it
  being selected or copied. The prompts share one new `ConfirmField` component (the line saying
  what to type, the copy button, and the input), which keeps the text out of `select-none`.
  `Label` itself keeps it elsewhere.
- **The copy button** is a small icon button with the label "Copy". It shows a check for a
  moment after copying, as `CopyBlock` does, and puts focus in the input so the next step is
  pasting.
- **The prompt still has to be filled in.** Pasting counts, and the delete button stays disabled
  until the input matches.

---

## 3. Plan

One branch, `feat/bulk-select`, cut from `feat/hf-compat` because the list pages it changes
are the ones 017 changed. One PR, based on `feat/hf-compat`, retargeted to `main` once
cerulean-works/flock#105 merges.

### Part 1. API

- Migration `0019`: `dataset_trash.kind`, `models.prefix`.
- `POST datasets/move`, `POST datasets/delete` with `paths`, and folder renames and deletes in
  `core/datasets.py`, reusing D95's copy and D93's trash code.
- `PATCH models/{name} {name}` for renames. The upload, download, Storage-link and trash code
  read `prefix`.

### Part 2. Web

- A selection helper shared by both lists, the selection bar, the row menu, and the Move and
  Delete dialogs, following `RunsTable`, `MoveRunsDialog` and `DeleteRunsDialog`.
- The model Rename dialog.
- `ConfirmField`, used by every type-to-confirm dialog (D135).
- Folder entries in org settings' "Deleted datasets".
- UI copy in plain sentences.

### Tests

- `tests/app/test_datasets.py`: moving several items, one failing and the rest moving; a folder
  moved with its loose files; moving into itself refused; the limits counted over the request;
  deleting a folder and restoring it; deleting several items.
- `tests/app/test_models.py`: a rename, then upload and download through the new name; old ref
  gives 404; a taken name refused; a rename during an open upload refused; `prefix` used by the
  trash and restore.
- Web specs for the selection (hover, shift-click, select all, Escape), the bar, the menu, and
  each dialog. A spec for `ConfirmField`: the copy button copies the exact text, the text can be
  selected, and the delete button enables once the pasted text matches.
- **Manual,** on the local stack: select and move two GLUE configs into a folder, delete a
  folder and restore it from org settings, rename a model and load it by its new name.

### Docs

- The Datasets and Models guide pages, and the API reference for the new routes.
- Mark this file `applied`, with a "How it was actually built" section.

---

## 4. Open for review

1. **Model renames keep the old folder** (D134), or copy the files so the folder matches?
2. **No Move for models** (D134), or should Move send models to another org?
3. **Loose files can't be selected** (D130), or should they move and delete with the rest?
4. **A deleted folder is one trash entry** (D132), or one entry per dataset in it?
5. **Stacked on `feat/hf-compat`** (section 3), or wait for cerulean-works/flock#105 and branch
   from `main`?
