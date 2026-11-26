# 031: the compare page paints before the diff

| | |
| --- | --- |
| Status | implemented, pending review |
| Date | 2026-11-23 |
| Builds on | [027](027-eval-compare-items.md) (D195 the compare page), [028](028-eval-item-links.md) (D200 links to a task's items) |
| Amends | D195 (the page's load no longer waits for the diff) |

## 1. Goal

On two runs of a whole bank (642 tasks, 278 of them compared), the compare page showed nothing for 6 to 7 s: its server load waited for the diff before it rendered, and a linked task's items were asked for only after that. A link to one doc felt broken.

1. **First paint at once.** The page shows the two runs as soon as Flock answers for them.
2. **A linked task's items start at once,** beside the diff, not after it.

**Out of scope:** making the diff itself faster, and caching it between requests.

---

## 2. Design decisions

### D209. The diff streams in after the page

Measured on the bank pair (OLMo 2 1B against Llama 3.2 1B, schema 11 runs, 302 MB of predictions read): Flock's diff route takes 5.7 to 6.1 s and answers 587 KB. Inside eval's diff, about 3.8 of 4.0 s is parsing the prediction rows (their item text included) to read each doc's `id` and `correct`; reading the files is 0.7 s and encoding the answer 0.01 s. So a counts-only answer (85 KB without the id lists) would save no time, and a faster parser in eval would halve the parse at best. What can change is when the page waits.

- The page's load awaits the two runs only and returns the diff as an unawaited promise, which SvelteKit streams into the same response. The page shows a status line, "Comparing every task of the two runs…", until it lands, then the counts table and the tasks not compared.
- **A task named in the link** (`task=`, D200) is offered to the items alone, without counts, until the diff lands, so its first page is asked for at once. Its counts fill in when the diff arrives; the reader's task, group and page stay. With no task in the link, the items wait for the diff, which picks the first task with docs as before.
- A linked task the diff then leaves out (not compared) restarts the items on a task it compared, as a link to an unknown task did before.
- A refusal (409, or a run without results, 404) still shows its reason in place of the items. Any other failure shows "The comparison failed" with Flock's message; the two runs stay on the page.
- An answer for a pair the reader has since left is dropped.

Measured with a headless browser on the dev server (vite dev mode, warm), a link to HellaSwag's both-wrong docs of the pair: before, first paint at 7.3 s and the first item row at 8.3 s; after, first paint at 0.9 s, the first item row at 2.4 s and the counts table at 7.0 s. A linked task's items take 0.73 s from Flock alone and 0.90 s while the diff runs: both run in threads of one Python process, so the diff slows them only a little.

---

## 3. Plan

1. The compare page's load returns `compared`, a promise of the diff or the refusal.
2. The page resolves it into its own state and offers the linked task until then.

## 4. How it was actually built

- `+page.server.ts` calls the diff route without awaiting it; the refusal and error handling moved into its `then`.
- The page keeps `diff`, the refusal and a failure in state, set by an effect on `data.compared`. Everything that read `data.diff` reads `diff`.
- The tests render the page with a resolved promise, and one with a promise held open, checking the items asked for the linked cell before the diff landed.

## 5. Known gaps

- The diff still costs its 5 to 6 s on a whole bank, now behind the items; each visit pays it again (no cache).
- In dev mode the items' request leaves only after hydration, about 1 s after first paint.
