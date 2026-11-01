# Cerulean — Dashboard Style Reference

> Open skies, a clean white page, and one grotesque doing all the talking

**Theme:** light, dark or the system's, chosen on the account page and kept in the `flock-theme` cookie (`src/lib/theme.ts`)

**Use:** the Flock dashboard. It mirrors the current [cerulean.works](https://cerulean.works) website so the product and the lab read as one thing.

Cerulean should feel like a small group of people doing serious work in a very large world. The sky image on the signed-out pages carries the distance and the atmosphere; everything else is a quiet, precise instrument. Surfaces are pure white and flat, type is a single sans-serif family, and color is reserved for information: links, focus, run states and chart series.

The earlier paper language is gone: no cream or beige canvas, no grain or texture, no warm brown accents, no soft card shadows, no serif display type.

## Tokens — Colors

Every value lives once, in `src/routes/layout.css`, mapped onto shadcn's semantic tokens plus a few roles shadcn has no slot for. Ink, slate, reading blue, focus blue, the quiet rule and the selection wash are the website's values (`--c-ink`, `--c-muted`, `--c-link`, `--c-focus`, `--c-rule`, `--c-selection`). The canvas is pure white where the website uses paper.

| Name           | Light     | Dark      | Token                              | Role                                                                       |
| -------------- | --------- | --------- | ---------------------------------- | -------------------------------------------------------------------------- |
| Canvas         | `#ffffff` | `#0f1012` | `background`, `popover`, `sidebar` | Page, sidebar, menus and dialogs. Pure white; never tinted                 |
| Panel          | `#f6f7f8` | `#17181b` | `card`                             | Chart panels, the log panel, inline notes. Ink at 4% over white            |
| Panel hover    | `#eff0f3` | `#222428` | `muted`, `secondary`, `accent`     | Hover and current-item fills, quiet controls, meter tracks. Ink at 7%      |
| Ink            | `#172a50` | `#e8eaed` | `foreground`                       | Titles, body text, essential labels                                        |
| Slate          | `#4b5d79` | `#a0a6b0` | `muted-foreground`                 | Captions, dates, supporting text; still comfortably legible                |
| Faint          | `#7b88a0` | `#6e747e` | `faint`                            | Tertiary text: counts, empty cells, placeholders                           |
| Quiet rule     | `#cdd3db` | `#33363c` | `border`, `input`                  | Control outlines and necessary structural rules                            |
| Divider        | `#e6e9ee` | `#24262b` | `divider`                          | Row and section dividers, lighter than a rule                              |
| Reading blue   | `#145a9a` | `#7ab4f5` | `primary`, `link`                  | Links, the one filled action per form, switches, checks, budget meter      |
| Focus blue     | `#1768af` | `#7ab4f5` | `ring`                             | Visible focus                                                              |
| Selection wash | `#cce7fb` | `#1e3a5f` | `selection`                        | Selected text, the selected log line                                       |
| Deep blue      | `#102c60` | `#24406f` | `deep`                             | Initials tiles and avatars, the sky's fallback                             |
| Running        | `#1768af` | `#5ea2ec` | `state-running`                    | Live runs                                                                  |
| Finished       | `#23804f` | `#45b87f` | `state-finished`                   | Finished runs                                                              |
| Preempting     | `#b86e00` | `#e3a33c` | `state-preempting`                 | Preempted runs; the budget meter past 80%. A warning amber, not decoration |
| Failed         | `#c0392f` | `#f07167` | `state-failed`, `destructive`      | Failed and crashed runs, over budget, destructive actions                  |

Text on a filled blue or deep surface is plain white (near-black on dark mode's lighter reading blue). Chart series keep their own categorical palette (`workspace.svelte.ts`), with a lifted variant for dark pages; the brand blue is not forced into every series. Terminal output uses the `ansi-*` tokens.

Dark is a neutral near-black in the manner of Linear, not a navy: graphite surfaces with no blue cast, each layer a step lighter (canvas, panel, hover, then menus and dialogs at `#1b1c20`), and the reading blue lightened so links and focus stay the only saturated thing on the page.

## Tokens — Typography

### Host Grotesk · `font-sans`

The only typeface. It carries titles, numbers, navigation, tables, chart labels and body text, as on cerulean.works. It is linked from Google Fonts in `src/app.html` (weights 300–800, with italics) behind a preconnect; the fallback is Arial. There is no serif and no `font-serif` utility in use.

- **Weights:** 400 for almost everything, including large titles (the website sets its headings at 400). 500 (`font-medium`) for the Flock wordmark, table headers, current navigation items and compact emphasis. 300 only for long, quiet descriptions.
- **Letter spacing:** −0.025em on titles of 28px and up, −0.01 to −0.02em on 22–24px section headings, normal below that. Never track small text out into decorative labels.
- **Line height:** 1.15 for titles, 1.45 for body text and descriptions.
- **Numerals:** `tabular-nums` wherever numbers line up or update in place: tables, chart axes and tooltips, the pager, budget figures, durations and counts.
- **Case:** sentence case. No tiny all-caps labels.

### Monospace · `font-mono`

The system monospace for code, run IDs, commands, config keys and console output. It is not a display voice.

### Type scale

| Role                | Size    | Weight | Tracking | Example                               |
| ------------------- | ------- | ------ | -------- | ------------------------------------- |
| Page title          | 34px    | 400    | −0.025em | "Activity", a project or run name     |
| Onboarding title    | 28px    | 400    | −0.02em  | An empty project                      |
| Section heading     | 22–24px | 400    | −0.01em  | "Budget", "charts", "Members"         |
| Wordmark            | 19px    | 500    | −0.025em | "Flock" beside the logo disc          |
| Lead / reading text | 17px    | 400    | 0        | Text under an onboarding title        |
| Body                | 14px    | 400    | 0        | Feed entries, forms, table cells      |
| Caption / metadata  | 12–13px | 400    | 0        | Dates, GPU hours, axis ticks          |
| Big figure          | 34px    | 400    | −0.025em | Budget remaining, with `tabular-nums` |

## Tokens — Spacing & Shapes

Spacing follows Tailwind's 4px scale: tight within a control, generous between groups, compact only where real data needs it (the runs table, the log).

| Element                                     | Radius                    | Treatment                                                       |
| ------------------------------------------- | ------------------------- | --------------------------------------------------------------- |
| Chart panels, log panel, tables, notes      | 0                         | Flat panel tint, no border, no shadow (the website's post card) |
| Buttons, inputs, selects, segmented control | `--radius` (0.5rem) scale | Quiet rule outline or panel-hover fill                          |
| Menus, popovers, tooltips, dialogs          | `rounded-lg`              | White, a 1px ink-10% ring and a small shadow: functional only   |
| Avatars, the logo disc, status dots         | Circle                    | People and the mark are round; organizations are square tiles   |

## Surfaces & Elevation

The page is one white plane. Grouping comes from the panel tint, dividers and space, not from stacked cards. A panel is the website's card: a flat `#f6f7f8` rectangle with no outline. Nothing reading-related floats.

Shadows exist only where something genuinely sits above the page and must be told apart from it: menus, popovers, the floating pager, the plot tooltip, a sheet, and a chart panel while it is being dragged. They are small and neutral. No paper shadows, frosted panels, bevels, gradients or textures anywhere in the interface.

## Components

### Mark and wordmark

The supplied gradient disc (`LogoMark`) followed by "Flock" in Host Grotesk 500. Keep the disc round and its colors intact; never redraw it, recolor it, or replace it with a CSS gradient.

### Sky (signed-out pages)

`AuthShell` places the cyanotype sky beside a narrow white form column. It is the only atmospheric image in the app and never sits behind text or data.

### Page header

A 34px title, an optional muted description on the same line, then the page's controls. Titles are plain text: no rules, labels or ornaments around them.

### Panels

Chart panels and the log panel use `bg-card`. Their headings are small and medium-weight; axes and readouts are `text-muted-foreground` with `tabular-nums`. Evidence gets most of the area; series colors stay meaningful.

### Notes and warnings

An inline note is a flat panel (`bg-card`, `px-3 py-2.5`) with body-size text and a link to the fix. Use a state color only for the thing that is in that state (a badge, a meter, a log line), never as a decorative border.

### Meters

A 6px rounded track in panel hover, filled with reading blue; amber past 80% of a budget, red when over.

### State badges

A tinted pill: the state color at 10% behind its text in the state color, with a dot that pulses while the run is live.

### Actions

One filled reading-blue button for a form's primary action; everything else is outline or ghost. Links are reading blue and underline on hover. Every interactive element has a visible focus ring in focus blue.

## Do's and Don'ts

### Do

- Keep the canvas pure white and let the panel tint do the grouping.
- Set everything in Host Grotesk; make hierarchy with size and space, not with weight jumps or a second family.
- Use `tabular-nums` for any number that aligns or changes in place.
- Use color for information: links, focus, states, chart series.
- Reach for the tokens (`bg-card`, `text-muted-foreground`, `border-divider`, `bg-state-*`) instead of hex values in components.

### Don't

- Don't reintroduce cream, beige or other warm tints, paper grain, or textures.
- Don't add serif display type, or brown and sepia accents.
- Don't use left-border callouts, decorative rules or drop shadows on in-page content.
- Don't put text over the sky or apply texture to data.
- Don't replace the logo with a CSS gradient or letter.

## Quick reference

```yaml
identity: Cerulean (Flock dashboard)
source: cerulean.works (BaseLayout CSS), with a pure white canvas instead of paper
colors:
  canvas: '#ffffff'
  panel: '#f6f7f8' # ink 4% over white
  panel-hover: '#eff0f3' # ink 7% over white
  ink: '#172a50'
  slate: '#4b5d79'
  faint: '#7b88a0'
  rule: '#cdd3db'
  divider: '#e6e9ee'
  link: '#145a9a'
  focus: '#1768af'
  selection: '#cce7fb'
  deep: '#102c60'
fonts:
  sans: Host Grotesk (Google Fonts, 300–800), Arial fallback
  mono: system monospace
type:
  title: 34px / 400 / -0.025em
  heading: 22–24px / 400 / -0.01em
  wordmark: 19px / 500 / -0.025em
  body: 14px / 400 / 1.45
shapes:
  panel-radius: 0
  control-radius: 0.5rem
  logo: supplied circular artwork
```
