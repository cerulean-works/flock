# Cerulean — Style Reference

> Open skies, tactile blue prints, and a human editorial voice

**Theme:** mixed — Daylight and Evening

**Use:** presentations, reports, research posters, teaching materials, social graphics, print, websites, and digital interfaces.

This guide follows the structure of the supplied Structured and Intercom references. Its colors, type pairing, mark, and artwork come from Cerulean's accepted identity. Cross-format sizes and composition recipes are recommended starting points, not measurements of existing print or presentation designs. Token names describe reusable roles in any design tool; they do not require CSS. Website-specific behavior and exact measurements live in [Web implementation](web-implementation.md).

Cerulean should feel like a small group of people doing serious work in a very large world. An expansive blue image creates distance and possibility; warm paper, readable serif type, and direct writing bring the reader close. The identity alternates between atmospheric openings and quiet spaces for thought. Its richness comes from the material quality of an image and the precision of its composition. The rest is deliberately simple: flat surfaces, restrained typography, sharp artwork edges, and room to read.

The system accommodates safety, interpretability, pretraining, SFT, infrastructure, tools, and education. These subjects share one identity. The current public presentation does not include Blue Magpie.

## Tokens — Colors

Colors have semantic roles. Choose Daylight or Evening for a composition, then use the corresponding text and surface pair. Neither appearance is a separate brand.

| Name                  | Daylight  | Evening   | Token             | Role                                                                   |
| --------------------- | --------- | --------- | ----------------- | ---------------------------------------------------------------------- |
| Paper / Evening field | `#fcfbf6` | `#10254b` | `color.canvas`    | Main reading surface; document page, slide, panel, or interface canvas |
| Ink / Warm reverse    | `#172a50` | `#f4f2e9` | `color.text`      | Titles, body text, essential labels                                    |
| Slate / Pale slate    | `#4b5d79` | `#c1cbe0` | `color.muted`     | Captions, dates, supporting text; still comfortably legible            |
| Deep blue             | `#102c60` | `#0c2048` | `color.deep`      | Closing field, cover, strong section boundary, image fallback          |
| Porcelain             | `#fffdf4` | `#fffdf4` | `color.on-image`  | Short inscription on dark artwork; text on a deep blue field           |
| Reading blue          | `#145a9a` | `#a8d4ff` | `color.link`      | Links and small functional emphasis; no default filled CTA color       |
| Focus blue            | `#1768af` | `#a8d4ff` | `color.focus`     | Visible focus in interactive work                                      |
| Quiet rule            | `#cdd3db` | `#415577` | `color.rule`      | Necessary table or structural rules; not decorative scaffolding        |
| Selection wash        | `#cce7fb` | `#235fa2` | `color.selection` | Selected text or a restrained functional highlight                     |

The supplied gradient-disc logo and cyanotype artwork retain their original colors in either appearance. Do not derive a replacement logo from these swatches. The brightest blue belongs primarily to the mark and imagery; a large electric-blue button is not a defining brand device.

These are sRGB reference values. For print, convert through the intended output profile and proof the warm paper, deep blues, and image detail. Do not invent a universal CMYK equivalent. Ordinary digital text should meet WCAG AA contrast; diagrams and charts must remain understandable without color alone.

## Tokens — Typography

### Newsreader — Editorial voice · `font.editorial`

- **Role:** Titles, mission statements, section headings, essays, explanatory prose, and short quotations. It carries both the scale of an opening and the intimacy of reading.
- **Substitute:** Georgia when Newsreader is unavailable. Recheck wrapping and spacing; substitution is a fallback, not a new identity.
- **Weights:** 400 by default; 500 for limited emphasis. Authored bold and italics remain available where meaning needs them.
- **Line height:** 1.08–1.23 for short titles; 1.4–1.5 for prose. Long multiline headings need more leading than compact titles.
- **Letter spacing:** Approximately −0.02em for large headings; near zero for body text. Never stretch small text into a decorative label.
- **Character:** Literary, open, and composed. Avoid monumental cropped wordmarks and compressed poster typography as routine treatments.

### DM Sans — Information and utility · `font.utility`

- **Role:** Captions, authors, dates, navigation, chart labels, tables, short descriptions, and instructions. It makes the material easy to use.
- **Substitute:** Arial when DM Sans is unavailable.
- **Weights:** 400 for most text; 500 for compact emphasis or table headers.
- **Line height:** 1.4–1.5 for descriptions and captions; compact single-line labels may be tighter.
- **Letter spacing:** Normal. Sentence case is the default.
- **Character:** Clear and unobtrusive. Do not use tiny all-caps labels or wide tracking to manufacture a research aesthetic.

### Monospace — Technical content · `font.mono`

- **Role:** Code, literal identifiers, commands, and aligned technical output. Use the host's readable system monospace; it is not a third display voice.
- **Weights:** Regular, with semantic emphasis when needed.
- **Line height:** Around 1.6 for code blocks.
- **Mathematics:** Use the authoring tool's proper math typesetting. Do not simulate equations with decorative typography or force them into monospace.

### Type Scale

Sizes are starting ranges for the named medium. A 24px screen label is not automatically a 24pt printed label. Choose sizes for reading distance, then preserve the hierarchy. Slide values assume a conventional 16:9 presentation viewed in a room; print values assume a handheld A4/Letter document. Posters need a separate reading-distance check.

| Role                | Family     | Weight  | Screen reference | Slide reference | Print reference | Line height | Tracking | Token          |
| ------------------- | ---------- | ------- | ---------------- | --------------- | --------------- | ----------- | -------- | -------------- |
| Opening/title       | Newsreader | 400     | 44–56px          | 36–48pt         | 28–36pt         | 1.1–1.23    | −0.02em  | `type.display` |
| Section heading     | Newsreader | 400     | 32–42px          | 28–36pt         | 18–24pt         | 1.15        | −0.02em  | `type.heading` |
| Work/item title     | Newsreader | 400     | 24–34px          | 24–30pt         | 14–18pt         | 1.15–1.2    | −0.01em  | `type.title`   |
| Short introduction  | Newsreader | 400     | 24–28px          | 24–28pt         | 13–16pt         | 1.4         | 0        | `type.deck`    |
| Reading prose       | Newsreader | 400     | 22–25px          | 22–26pt         | 11–12pt         | 1.5         | 0        | `type.body`    |
| Utility/description | DM Sans    | 400     | 16–18px          | 20–24pt         | 10–11pt         | 1.45        | 0        | `type.utility` |
| Caption/metadata    | DM Sans    | 400     | 14–16px          | 16–18pt         | 9–10pt          | 1.5         | 0        | `type.caption` |
| Code                | Monospace  | Regular | 14–17px          | 18–22pt         | 9–10pt          | 1.6         | 0        | `type.code`    |

Projection, accessibility, or content density may require larger type or more pages. Split content before shrinking it. For journals, conferences, and other prescribed templates, their required fonts and sizes take priority over these brand defaults.

## Tokens — Spacing & Shapes

**Base unit:** `u` — a medium-specific spacing unit. Start at 4px for screen layouts, 4pt for slides, and 2pt for handheld print. These are independent starting grids, not physical equivalents.

**Density:** generous around ideas and imagery; comfortable within text; compact only where real data requires it.

### Spacing Scale

| Name      | Value | Token             | Use                                                  |
| --------- | ----- | ----------------- | ---------------------------------------------------- |
| Close     | 2u    | `space.close`     | Mark-to-name relationship or a tightly related label |
| Small     | 3u    | `space.small`     | Caption-to-image distance                            |
| Related   | 4u    | `space.related`   | Related lines or controls                            |
| Group     | 6u    | `space.group`     | Title, deck, and metadata grouping                   |
| Paragraph | 8u    | `space.paragraph` | Distinct prose paragraphs or adjacent content groups |
| Block     | 12u   | `space.block`     | Text-to-art gap or a substantial internal division   |
| Section   | 16u   | `space.section`   | Separate ideas in a continuing composition           |
| Pause     | 24u   | `space.pause`     | Major transition, opening, or closing field          |

Use the scale to establish relationships, then make optical adjustments for the medium. The website's exact responsive measurements are recorded in its companion, not imposed on a poster or report.

### Border Radius

| Element                      | Value               | Treatment                                                    |
| ---------------------------- | ------------------- | ------------------------------------------------------------ |
| Artwork and editorial panels | 0                   | Sharp rectangle or square; no enclosing card shell           |
| Reading surfaces and tables  | 0                   | Flat, aligned, no floating panel effect                      |
| Logo                         | Circle              | Preserve the supplied mark and its proportions               |
| Icon-only utility control    | Circle where useful | A functional exception, not a shape to repeat around content |

### Layout

- **Outer margin:** Start near 5–7% of canvas width for slides and graphics; choose practical document margins for print. Full-bleed art is the intentional exception.
- **Reading measure:** Aim near 55–70 characters per line for prose. Use columns or another page when the format becomes too wide.
- **Section gap:** Usually two to three times the gap within a related group.
- **Artwork/text gap:** Give the caption a clear relationship to its image; give separate stories more space.
- **Alignment:** Prefer a few shared edges. Center a reading column or a single deliberate opening, not every paragraph.

## Components

Components are reusable compositions, not framework widgets. Each can become a slide, page region, printed panel, or digital module.

### Cerulean Mark and Name

**Role:** Authorship and recognition.

Use the supplied gradient disc, with “Cerulean” in Newsreader when a name is needed. Keep the circle round and its original color transitions intact. Start with a mark around the visual height of the adjacent type and approximately a quarter-disc gap. Leave at least half a disc of clear space around a standalone mark. These are general lockup recommendations; use the existing application component where available. Do not place the source PNG's rectangular matte on a contrasting surface: use a prepared circular crop or approved transparent asset. Do not redraw, recolor, stretch, or add a glow to the mark.

### Sky Inscription

**Role:** An opening, invitation, or major transition.

One expansive cyanotype sky fills the canvas or a deliberate large image region. Place a short Newsreader statement in a quiet area, usually toward the upper left, in Porcelain. Keep enough uninterrupted sky for the image to feel larger than the writing. The mark may precede the first line. Use the actual mission when introducing the lab:

> Cerulean is a lab for  
> breaking down barriers and  
> democratizing AI exploration.

Scale to the format and reflow if required; never squeeze the text to preserve line breaks. Use an inscription for a short opening, not a paragraph of research prose.

### Editorial Text Block

**Role:** Explain a question, argument, result, or invitation.

Newsreader title and body on a solid Daylight or Evening canvas. DM Sans carries authorship, dates, and supporting labels. Separate paragraphs with space, align their left edges, and keep a readable measure. A document can use a continuing column; a slide should carry one short argument. Preserve the author's actual words and technical distinctions.

### Work Portrait

**Role:** Give a project, course, or initiative a recognizable image.

A square artwork with sharp edges, a Newsreader title below it, and an optional DM Sans description. No shadow, floating enclosure, or repeated title inside the image. Use an adjacent-text variant when the format calls for a horizontal listing: the same art, title, description, and metadata become a coherent row. Text outside the image is the constant; its position follows the composition.

### Evidence Plate

**Role:** Present a chart, research figure, table, equation, or experiment result.

Use a solid surface, readable axes and labels, a concise title, and a caption that explains what the evidence supports. DM Sans handles chart labels and tables; mathematical notation uses proper typesetting. Give the evidence most of the usable area. Preserve units, uncertainty, legends, provenance, and meaningful series colors. The blue identity may frame the figure, but must not distort it.

### Caption and Credit

**Role:** Provide context, attribution, or navigation through a sequence.

A small, readable DM Sans block aligned to the image or text it belongs to. Use `color.muted` on a solid surface. Dates, authors, page numbers, source notes, and figure numbers belong here. The block earns its presence by conveying information; avoid ornamental issue numbers and pseudo-technical labels.

### Quiet Action

**Role:** Offer a real next step in interactive or printed material.

Use a plain, descriptive DM Sans link or a short underlined phrase in reading text. A digital action must have a real destination, visible focus, and an adequate target. A print action needs a useful, readable URL; add a QR code only when it helps the reader. Never imply that an informational project portrait is clickable. Primary actions do not need a new accent color or a large pill treatment.

### Closing Field

**Role:** End a presentation, document, or sequence with a clear connection to the lab.

A deep blue field with Porcelain text, a short contact invitation, and the relevant address or source. Use only the contact details the material needs. On a report this can be a small colophon; on a closing slide it can occupy the canvas. Real destinations are `hi@cerulean.works`, `github.com/cerulean-works/`, and `huggingface.co/cerulean-works`.

## Do's and Don'ts

### Do

- Use one strong image composition and enough space to perceive it.
- Let Newsreader carry both the opening voice and substantial reading; let DM Sans make information easy to find.
- Choose a coherent Daylight or Evening palette for each reading surface.
- Keep prose and evidence on quiet backgrounds.
- Make abstract artwork express a specific idea through a material or physical structure.
- Adapt type, density, and margins to reading distance and the actual format.
- Use real research, real people, actual project names, and clear credits to create warmth.
- Keep project titles outside artwork, with card and row variants explicitly chosen.

### Don't

- Don't turn every topic into a bird, nest, flock, castle, cloud, or permanent brand subdivision.
- Don't use generic neural networks, luminous orbs, plastic ribbons, stock laptop scenes, or interchangeable AI imagery.
- Don't add scattered rules, miniature labels, or arbitrary technical diagrams to make a simple layout appear sophisticated.
- Don't set long prose over textured imagery or apply texture to research evidence.
- Don't replace the actual logo with a CSS gradient, letter O, or newly generated approximation.
- Don't shrink slide or poster text to fit a document-length argument.
- Don't fill sparse space with invented work, metrics, affiliations, quotations, or slogans.
- Don't force blue into every chart series, recolor third-party marks without an appropriate asset, or override a required publication template.

## Surfaces

These are composition choices, not a stack of progressively elevated cards.

| Level | Name            | Value                 | Purpose                                                                       |
| ----- | --------------- | --------------------- | ----------------------------------------------------------------------------- |
| 0     | Daylight paper  | `#fcfbf6`             | Reports, teaching sheets, essays, and quiet informational slides              |
| 0     | Evening field   | `#10254b`             | Alternate reading canvas, presentations, and digital surfaces                 |
| 1     | Deep field      | `#102c60` / `#0c2048` | Cover, section boundary, closing, or a deliberate strong block                |
| Image | Cyanotype field | Approved artwork      | Atmosphere, project identity, or a conceptual visual; not a body-text surface |

## Elevation

Depth comes from the image itself, scale, cropping, and the transition between a broad field and a close reading surface. Editorial compositions use no decorative drop shadows, frosted panels, beveled cards, or simulated floating layers. The logo's original gradient and the tonal variation within artwork are intentional; do not extend them into gradient text or generic UI decoration. Functional overlays in a product may need their own interaction treatment, but should not become the visual language of every page or slide.

## Imagery

The signature is **a material treatment plus an intentional composition**. Use the vocabulary of cyanotype, photogravure, textile grain, fine mesh, restrained ink breaks, dusty blue, porcelain highlights, and large indigo fields. Richness should remain legible at the final reproduction size. Fine grain that looks good in a large image may become noise on a small card or in print.

The approved image family establishes three distinct ideas:

| Subject       | Concept                                      | Composition                                                                     | Asset                                                    |
| ------------- | -------------------------------------------- | ------------------------------------------------------------------------------- | -------------------------------------------------------- |
| Sky           | Room to explore                              | A broad indigo field with cloud movement and open space for a short inscription | [sky.jpg](../public/images/design/sky.jpg)               |
| Castor        | Architectural tension and testing boundaries | An oblique, closely cropped irregular fortress rising from fractured cliffs     | [castor.jpg](../public/images/design/castor.jpg)         |
| Fellowship    | Learning through interaction                 | Fine mesh patterns overlapping to produce a new interference field              | [fellowship.jpg](../public/images/design/fellowship.jpg) |
| Cerulean mark | Authorship                                   | The actual supplied gradient disc                                               | [logo.png](../public/images/design/logo.png)             |

For new art, write the idea in one sentence before choosing a subject. Decide the dominant mass, empty area, viewpoint, crop, and material process. A castle should have particular, irregular architecture and a deliberate angle. An abstract fellowship image should express interaction through structure, not through a generic group portrait or a copied scientific diagram. Keep the source reference's idea separate from its exact arrangement.

Atmospheric images may be metaphorical. Scientific figures must be accurate. Real documentary photographs of people and work can appear in their natural colors with straightforward credits; they do not need to be turned blue to belong. Use decorative imagery selectively so that the actual work remains visible.

Use the same approved source artwork across an initiative's outputs, with deliberate crops for each format. Record the source and crop instead of silently substituting different imagery. Provide accessible descriptions when an image conveys information; decorative art should not repeat nearby text in its description.

## Layout

The central rhythm is **expanse → explanation → evidence or work → invitation**. It can unfold across a website, a slide sequence, or a printed spread. A single asset only needs the parts relevant to its purpose.

### Atmospheric composition

Let one image occupy most or all of the format. Place one short inscription in its quiet region. Use asymmetric placement and a few shared edges. A cover or opening slide can be visually expansive without oversized type, corner labels, or additional decorative objects.

### Editorial composition

Use a solid surface, a clear title, a restrained metadata line, and a readable text measure. An image or evidence plate can sit beside or between text blocks. The title is outside project artwork; only a deliberate short sky inscription overlays atmosphere.

### Adaptation by medium

| Medium                       | Starting composition                                                              | Adaptation rule                                                                                          |
| ---------------------------- | --------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Presentation                 | One atmospheric opening, followed by solid text and evidence slides               | One main claim per slide; diagrams get room; do not repeat a sky background on every slide               |
| Report or working paper      | Restrained title/author block, readable body, clearly captioned figures           | Use Daylight for practical printing; preserve a venue's required template; an art cover is optional      |
| Research poster              | Visible title, question, method, main result, supporting evidence, contact        | Prioritize the result at viewing distance; allocate space to actual figures rather than decorative art   |
| Teaching material            | A question or learning goal, explanation, worked example, exercise                | Keep instructions and technical notation easy to scan; use the serif for explanation and sans for labels |
| Social or event graphic      | One message, one image or solid field, essential details                          | Recompose for the aspect ratio; test at feed size; date, place, and action remain readable               |
| Website or digital interface | Atmospheric introduction where useful, then clear reading and task surfaces       | Use real responsive layouts and accessible interactions; apply the separate web implementation contract  |
| Print collateral             | A strong crop or quiet title side, with information in a controlled reading block | Respect trim, bleed, and safe areas; proof blues and detail at the final size                            |

## Agent Prompt Guide

### Quick Color Reference

- Daylight: `#fcfbf6` canvas, `#172a50` text, `#4b5d79` secondary text.
- Evening: `#10254b` canvas, `#f4f2e9` text, `#c1cbe0` secondary text.
- Deep field: `#102c60` in Daylight compositions or `#0c2048` in Evening compositions.
- Text on a dark image or closing field: `#fffdf4`.
- Functional links: `#145a9a` on Daylight, `#a8d4ff` on Evening.
- No separate mandatory CTA fill; no added brand color for each research area.

### Example Component Prompts

1. **Opening slide:** Create a 16:9 Cerulean opening slide using the approved sky image edge to edge. Place the supplied mark before the mission's first line in the upper-left quiet region, with approximately 6% side margins. Use Newsreader 40pt regular, Porcelain, around 1.23 line height. Keep the three authored mission lines where they fit. Preserve a large unoccupied blue field. No subtitle, invented slogan, or extra graphic. Check the image crop and text at presentation size.

2. **Research report opening:** Create an A4 Daylight title page with the supplied report title in Newsreader 32pt, actual authors and date in DM Sans 10pt, and the abstract in Newsreader 12pt/1.5. Use a restrained logo lockup and a readable column with practical print margins. Let the actual title determine line breaks. Include an image only if it serves this report; do not invent results or affiliations. Follow a required venue template if one applies.

3. **Results slide:** Create an Evening 16:9 slide for the supplied experiment. Use a factual Newsreader 32pt title, give the provided chart most of the canvas, and set its labels in DM Sans at presentation-readable sizes. Include the supplied units, legend, uncertainty, and source. Add one short explanatory sentence below. Preserve meaningful series colors and keep texture out of the plot.

4. **Fellowship announcement:** Create a square Cerulean Fellowship graphic from the approved interference artwork and supplied event information. Use a sharp image region above a solid Daylight text region, a Newsreader title, and DM Sans for date, place, and action. Keep all text outside the art. Use only provided details; test legibility at the intended feed size rather than relying on a large export.

5. **Project overview spread:** Create a landscape spread with three Work Portraits for A place to begin, Castor, and Cerulean Fellowship. Use the approved square art, Newsreader titles below, and supplied DM Sans descriptions. Align image edges and title baselines; use generous outer margins and equal column gaps. If the format is too narrow, stack the portraits or use separate pages. Do not shrink descriptions into microtype or suggest destinations that do not exist.

For every prompt, supply the medium, dimensions or aspect ratio, audience/viewing distance, actual text, approved assets, and chosen appearance. Ask for an exported-size review. Missing facts should remain missing rather than being filled with plausible inventions.

## Typographic Voice

Newsreader makes Cerulean feel authored: a mission can read like a sentence written into the sky, and a technical explanation can feel like a thoughtful letter. DM Sans gives that voice a reliable supporting structure. The distinction is between sustained reading and navigational or factual information, not merely between large and small type.

Create emphasis through hierarchy, placement, and space. Avoid shouting headlines, excessive weight changes, decorative tracking, and slogans that say less than the real mission. Warmth comes from specific language about people, access, experiments, and shared work. Scale comes from the image and the room around it. Use precise, direct prose and preserve the author's meaning.

## Similar Brands

These are directional references from the supplied brief and documents, not templates or assertions that their current designs match Cerulean.

- **Thinking Machines:** The brief's reference for straightforward, text-led storytelling.
- **Reflection:** The brief's reference for human warmth in a research identity.
- **Structured reference document:** A useful model for coherent art direction, explicit material choices, and clear token/component descriptions. Cerulean uses its own blue image family and readable type hierarchy.
- **Intercom reference document:** A useful model for warmth, editorial rhythm, and separating expressive typography from utility information. Cerulean retains Newsreader and DM Sans.
- **The supplied blue-patterned uniforms:** A material reference for controlled indigo fields, irregular porcelain edges, textile detail, and the combination of a precise silhouette with expressive pattern.

## Quick Start

### Portable Token Recipe

Use these names as styles, swatches, or variables in the authoring tool. Keep the role names even when the file format represents them differently.

```yaml
identity: Cerulean
appearance: Daylight # or Evening
colors:
  Daylight:
    canvas: '#fcfbf6'
    text: '#172a50'
    muted: '#4b5d79'
    deep: '#102c60'
    link: '#145a9a'
  Evening:
    canvas: '#10254b'
    text: '#f4f2e9'
    muted: '#c1cbe0'
    deep: '#0c2048'
    link: '#a8d4ff'
  on-image: '#fffdf4'
fonts:
  editorial: Newsreader
  utility: DM Sans
  technical: system monospace
shapes:
  artwork-radius: 0
  panel-radius: 0
  logo: supplied circular artwork
spacing:
  unit: choose for the medium
  multiples: [2, 3, 4, 6, 8, 12, 16, 24]
composition:
  opening: expansive image with a short inscription
  reading: solid field with a readable text measure
  evidence: accurate figures with clear labels and captions
  closing: concise contact or source
```

### Apply in Any Medium

1. Choose the format, viewing distance, and actual communication task.
2. Set up Daylight or Evening swatches and Newsreader/DM Sans styles. Choose type sizes from that medium's reference range.
3. Select an atmospheric, editorial, work, or evidence composition. Establish margins and a few alignment edges before adding detail.
4. Place real content and approved assets. Keep technical figures accurate and essential information readable.
5. Review at the intended size: projected, printed, on a phone, or at feed scale. Check contrast, crop, wrapping, attribution, and the clarity of the next step.

### Website Adapter

The website maps `color.canvas/text/muted/deep/on-image/link/focus/rule/selection` to `--c-paper/ink/muted/deep/on-sky/link/focus/rule/selection`. Font roles map to `--font-editorial`, `--font-ui`, and `--font-mono`. Use the existing shared tokens and components rather than copying this portable recipe into a second palette.

Read [Web implementation](web-implementation.md) for Astro components, responsive measurements, full-viewport sky behavior, persistent navigation, content contracts, and validation. Read [the implementation trial](design-system-trial.md) for the observed ambiguities that shaped those contracts. This guide owns the cross-format identity; adapters own their medium's behavior.
