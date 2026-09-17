---
name: DraftLoop
description: A Chinese-first writing spread and academic report on navy and warm paper.
colors:
  paper: "#f5f4ef"
  ink: "#08243c"
  rail: "#132e47"
  nav-active: "#26425e"
  action: "#08264b"
  action-hover: "#173f63"
  teal: "#005f58"
  rust: "#a64132"
  amber: "#92530b"
  muted: "#586575"
  rule: "#d5d8d8"
  editor: "#fdfdfd"
  white: "#ffffff"
typography:
  headline:
    fontFamily: '"Times New Roman", "Songti SC", "SimSun", serif'
    fontSize: "48px"
    fontWeight: 700
    lineHeight: 1.28
  title:
    fontFamily: '"Times New Roman", "Songti SC", "SimSun", serif'
    fontSize: "34px"
    fontWeight: 700
    lineHeight: 1.3
  body:
    fontFamily: '"Times New Roman", "Songti SC", "SimSun", serif'
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.65
  essay:
    fontFamily: '"Times New Roman", "Songti SC", "SimSun", serif'
    fontSize: "20px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: '"Times New Roman", "Songti SC", "SimSun", serif'
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.7
rounded:
  field: "4px"
  control: "5px"
  navigation: "8px"
spacing:
  sm: "8px"
  md: "16px"
  lg: "20px"
  section: "24px"
  spread: "32px"
components:
  button-primary:
    backgroundColor: "{colors.action}"
    textColor: "{colors.white}"
    rounded: "{rounded.control}"
    padding: "12px 20px"
  button-primary-hover:
    backgroundColor: "{colors.action-hover}"
    textColor: "{colors.white}"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.action}"
    rounded: "{rounded.control}"
    padding: "12px 20px"
  navigation-active:
    backgroundColor: "{colors.nav-active}"
    textColor: "{colors.white}"
    rounded: "{rounded.navigation}"
    padding: "13px 16px"
  revision-field:
    backgroundColor: "{colors.editor}"
    rounded: "{rounded.field}"
    padding: "22px 20px"
---

# Design System: DraftLoop

## Overview

**Creative North Star: "双页讲义 + 学术报告"**

DraftLoop presents writing as a calm bilingual study document. A deep navy navigation rail frames warm paper; Chinese Songti and English Times give the learner's writing and the explanation a shared editorial voice. Fine rules, serif hierarchy and explicit evidence establish authority without suggesting an official examination result.

The live workspace extends the retained navy-and-paper report identity. This recorded implementation supersedes the former Quiet Precision system-sans direction following the user's approved world replacement. Flat surfaces and restrained controls leave room for long, real writing; semantic outline icons support navigation and editing. The report surface now follows the supplied DraftLoop reference report: a masthead-like front matter, numbered section rails, a bordered five-cell score strip, and distinct blue-gray source, green optimization and navy table surfaces.

**Key Characteristics:**

- Navy navigation, warm paper and clean editor surfaces.
- Songti Chinese and Times English with a strong editorial hierarchy.
- Exact evidence remains visible before optional long explanations.
- Flat planes, fine rules and small control corners.
- State and assistance remain explicit in words.

## Colors

Deep navy provides structure; muted rust, amber and teal distinguish feedback on a warm neutral ground.

### Primary

- **Navigation Navy** (`rail`) frames the application; **Active Navy** (`nav-active`) identifies its current destination.
- **Action Navy** (`action`, `action-hover`) anchors the primary button and selected rewrite tab.

### Secondary

- **Study Teal** (`teal`) identifies improvements, strengths and selected deep-report content.
- **Correction Rust** (`rust`) and **Attention Amber** (`amber`) identify original problems and missing support alongside written labels.

### Neutral

- **Warm Paper** (`paper`) is the continuous workspace ground.
- **Reading Ink** (`ink`) carries headings and body content; **Muted Ink** (`muted`) carries helper copy and composer placeholders.
- **Fine Rule** (`rule`) divides columns and sections; **Editor Paper** (`editor`) distinguishes writable surfaces. **White** (`white`) supplies inverse control text.

**The State Speaks Rule.** Color accompanies visible labels; it does not carry assessment or assistance meaning alone.

## Typography

**Display Font:** Times New Roman with Songti SC, SimSun and serif fallback.  
**Body Font:** The same bilingual serif stack.

English resolves to Times and Chinese to Songti where installed. Heading weight and scale establish hierarchy; evidence and editable English remain large enough to compare directly. The actual installed fallback can change wrapping.

### Hierarchy

- **Headline:** page identity; steps down to 40, 38 and 34 pixels at the implemented compact breakpoints.
- **Title:** desktop spread headings; report section headings use a nearby 30-pixel scale, with smaller 20–24-pixel subheads.
- **Body:** Chinese explanatory prose, generally with generous 1.65–1.8 line height.
- **Essay:** desktop writing at the frontmatter role; evidence uses 18–19 pixels, and compact editors use 18 pixels.
- **Label:** metadata and explanatory disclosures; compact secondary labels may use 12–14 pixels.

Overall and criterion scores use tabular figures and a larger numeric hierarchy. Their scale is a report-specific expression, not a default dashboard metric pattern.

## Layout

The desktop shell has a 214-pixel navigation rail and a separately scrolling workspace. Main padding is 30 pixels vertically at the top and 38 pixels horizontally. At 1200 pixels the rail narrows to 190 pixels and horizontal padding to 28 pixels; at 900 pixels navigation becomes a keyboard-contained drawer with a 56-pixel top bar. At 600 pixels main gutters are 20 pixels. Above 1600 pixels the main content is capped at 1470 pixels.

Current writing and rewriting surfaces use two paper columns with a fine seam; they stack at 1050 pixels. The academic report uses a narrow sticky contents column, which becomes a wrapping horizontal contents bar at that breakpoint. At 600 pixels the correction table reflows into labeled blocks and settings fields stack. Keep long English text wrappable and form controls within their parent width.

These compositions are current surface patterns, not mandatory layouts for every future screen. Their task-specific contract remains in `.impeccable/surfaces/frontend-src-liveworkspace-tsx.md`.

Recorded from `frontend/src/writing-studio.css`, `LiveWorkspace.tsx`, `RedesignedReport.tsx` and `ModelSettings.tsx`. Supplied visual review and fix evidence cover 1280×720 and 390×844 only. The approved comps are 1487×1058; exact-size fidelity remains unverified. See `.impeccable/review/finish-review.md` and `fix-verdict.md` for the bounded review, including the three resolved corrections.

## Elevation & Depth

The live studio uses flat paper, borders and tonal fields. Main buttons explicitly have no shadow. There is no paper grain asset or contextual glass sheet in the approved implementation. Pale teal distinguishes optimized paragraphs; the approved prominent teal edge belongs to this report component.

The tab change uses one 380-millisecond clipped reveal with a brief blur. The mobile drawer translates over 240 milliseconds. Both use the same ease-out curve, and reduced-motion preferences remove these effects. Exact motion and focus definitions live in the sidecar.

**The Paper Plane Rule.** Separate ordinary reading regions with spacing, tone and fine rules rather than floating card elevation.

## Shapes

Fields and report highlights use small corners; primary controls have slightly softened corners and active navigation has broader rounding. The report's numbered priorities are circular. Preserve these functional differences instead of applying one large radius to every surface.

## Components

### Buttons

Solid navy primary actions and transparent outlined secondary actions share restrained proportions, a minimum height of 48 pixels and no shadow. Hover changes the fill. Disabled primary actions use a pale neutral fill and readable muted text. Text and icon actions remain compact secondary controls. Visible focus uses an offset blue-green outline, with a lighter outline inside the navy rail.

### Inputs / Fields

The writing area is real editable text on clean paper with a fine border. Preserve explicit labels, readable placeholders, selection highlighting, teal caret and error text. Model configuration fields use a minimum height of 45 pixels and white fill; model ID supports direct entry as well as discovered suggestions.

### Navigation

The navy rail uses semantic outline icons and a filled current row. Report and settings tabs have selected underlines and keyboard navigation. Report contents follow ordinary workspace scrolling as well as anchor clicks. On compact screens the closed drawer is inert and its open state contains focus.

### Chips / Tags

Small softly rounded tonal tags identify strengths, paragraph contribution and topic context. Their words remain the source of meaning; they are not decorative status ornaments.

### Cards / Containers

Most content is continuous paper separated by fine rules. The bordered editor is the recurring container; pale study fields hold thesis or optimized prose. Deep reports use a formal score strip and numbered section markers to create document rhythm, while paragraph evidence keeps the original and revised text in distinct tonal fields. Do not turn each paragraph into an elevated card.

### Evidence and report patterns

Priority issues place the highlighted original, suggested expression and concrete editing action before the native long-explanation disclosure. The deep report preserves original/replacement/reason relationships and identifies optimized prose as assistance. The optimized paragraph uses a pale teal field and its approved four-pixel teal edge. Settings reuse the tabs and form grammar for model configuration and personal data.

**The Evidence First Rule.** Keep the exact original and suggested edit visible before folding longer explanation into an optional disclosure.

## Do's and Don'ts

### Do:

- **Do** preserve the navy-and-paper identity and bilingual serif hierarchy.
- **Do** keep exact source evidence and its suggested edit visually connected.
- **Do** pair state and assistance colors with explicit labels.
- **Do** preserve keyboard focus, reduced motion and readable mobile reflow.
- **Do** use flat tonal containers and fine rules for extended reading.

### Don't:

- **Don't** turn report sections into a rounded dashboard-card grid or metric wall.
- **Don't** invent decorative raster grain, floating glass or ornamental icons.
- **Don't** hide the actionable edit behind a long explanation.
- **Don't** present incomplete output as a normal final report.
