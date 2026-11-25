# Design system

How the dashboard looks and why, as it is built in `web/src/styles.css` and the shared components in
`web/src/components/`. Follow it for anything new; when the code and this file disagree, fix one of them in the
same change. The rules about the numbers themselves are in `AGENTS.md`.

## Principles

1. **The figure first, then its context.** A card leads with one number, then the period it covers, then how it
   splits by enrollment, then a note. Nothing decorative competes with the number.
2. **Every figure says what it is.** A label, the period, the currency, and an `info` tooltip with the definition.
   A short figure ("$1.2M") always has the exact value on hover.
3. **Both enrollments everywhere.** Every card on the Overview shows the combined figure with each enrollment
   below it, by name and in that enrollment's colour.
4. **Colour carries meaning, never decoration.** One accent for interaction, a fixed colour per enrollment and per
   pricing model, and status colours only for status.
5. **Readable without colour.** Every coloured mark has a text equivalent: a legend, a label, a tooltip or an
   `aria-label`. Charts have a table view.

## Themes

Four themes, chosen with the icon button in the header and remembered per browser: **system** (follows the
operating system), **light**, **dark** and **glass** (dark, with translucent frosted panels on a coloured
background). All colours are CSS custom properties on `:root`; a theme only redefines the tokens.

## Colour tokens

### Surfaces and text

| Token | Light | Dark | Use |
|---|---|---|---|
| `--page` | `#f9f9f7` | `#0d0d0d` | Page background |
| `--surface-1` | `#fcfcfb` | `#1a1a19` | Cards and tiles |
| `--surface-solid` | `#fcfcfb` | `#1a1a19` | Anything that must be opaque: popovers, sticky table headers, inputs |
| `--text-primary` | `#0b0b0b` | `#f0efec` | Body text and figures |
| `--text-secondary` | `#52514e` | `#c3c2b7` | Labels, inactive tabs |
| `--muted` | `#6b6a65` | `#a8a79f` | Notes, units, secondary values |
| `--grid` | `#e1e0d9` | `#2c2c2a` | Gridlines, table rules, empty tracks |
| `--axis` | `#c3c2b7` | `#383835` | Axes, dividers, totals rules |
| `--border` | 10% black | 10% white | Card and control borders |

### Accent and status

| Token | Light | Dark | Use |
|---|---|---|---|
| `--series-1` | `#2a78d6` | `#3987e5` | The accent: primary buttons, active states, a single data series |
| `--series-1-wash` | 14% of the accent | 18% | Selected and hover backgrounds |
| `--series-1-text` | `#184f95` | `#86b6ef` | Accent-coloured text and links |
| `--on-accent` | `#ffffff` | `#ffffff` (glass `#04201a`) | Text on the accent |
| `--focus` | `#2a78d6` | `#6da7ec` | Keyboard focus ring |
| `--warn-bg` / `--warn-border` | `#fff4d6` / `#eda100` | `#3a2f10` / `#c98500` | Warnings: partial months, estimated rates, data checks |
| `--err` / `--err-bg` | `#d03b3b` / `#fdecec` | `#e66767` / `#3a1b1b` | Errors, failed checks |
| `--ok-text` | `#006300` | `#0ca30c` | Passed checks |

Glass replaces the accent with mint (`#34d399`) and uses translucent surfaces; its values are in `styles.css`.

### Categorical palette

Eight slots from the validated dataviz reference palette, with separate light and dark values (selected, not
inverted). Slots are **assigned to meanings** so the same colour never means two things on one screen:

| Slot | Light | Dark | Assigned to |
|---|---|---|---|
| `--cat-1` | `#2a78d6` blue | `#3987e5` | Enrollment 1 (`--enr-1`) |
| `--cat-2` | `#eb6834` orange | `#d95926` | Enrollment 2 (`--enr-2`) |
| `--cat-7` | `#6250d6` violet | `#9085e9` | Enrollment 3 (`--enr-3`) |
| `--cat-5` | `#e87ba4` pink | `#d55181` | Enrollment 4 (`--enr-4`) |
| `--cat-3` | `#1baf7a` aqua | `#199e70` | Pricing model: On Demand |
| `--cat-4` | `#eda100` yellow | `#c98500` | Pricing model: Reservation |
| `--cat-6` | `#008300` green | `#008300` | Pricing model: Savings Plan |
| `--cat-8` | `#e34948` red | `#e66767` | Pricing model: Spot |

**Enrollment colours** come from `enrollmentColor(id)` in `EnrollmentSplit.tsx`. The slot is the enrollment's
position in the sorted list of enrollment IDs, so an enrollment keeps its colour on every page and after every
reload. Never pick an enrollment colour by its position in a particular list.

**Pricing-model colours** come from `payColor(name)` in `BentoTiles.tsx`, fixed by name.

**Increase and decrease:** ▲ for an increase and ▼ for a decrease, in `.mv-up` (`--ok-text`) and `.mv-down`
(`--err`). This follows the owner's choice and is the reverse of the usual cost convention; keep it consistent,
and always show the arrow so the direction doesn't depend on colour.

Don't add hex colours in components or new CSS rules. If a new meaning needs a colour, assign it a free slot here
first. The one known exception is `.banner.ok`, which still uses a fixed mint.

## Typography

- **Font:** the system UI stack (`system-ui, -apple-system, "Segoe UI", sans-serif`); monospace for IDs and SQL
  (`ui-monospace, Consolas`).
- **Sizes:** body 14px / 1.45; small and notes 12px; card titles (`h3`) 14px; page title 18px. Card figures 24px
  semibold; the headline figure 56px; a figure inside a group of cards 22px.
- **Numbers:** `font-variant-numeric: tabular-nums` wherever figures line up (tables, legends, lists), and right
  alignment for numeric columns (`td.num`).

## Numbers and words

- **Money:** `moneyShort` on cards and in legends ("£1.2M", "$350k"), with the exact value passed as `full` so it
  shows on hover; `money` with full precision in tables, tooltips and CSV downloads. Per-hour amounts use two
  decimals with "/h".
- **Ratios:** `pct`, with the basis in the note ("of eligible usage, at contract price").
- **Counts:** `count` (thousands separators); `compact` only where space is tight.
- **Periods:** `periodLabel`: "Sep 2026", "Aug–Sep 2026", "3 Aug – 17 Sep 2026". A headline always names its period.
- **Currencies:** always shown. In the Combined view, say "combined in USD"; outside it, each enrollment is in its
  own currency and amounts in different currencies are never added or stacked.
- **Names:** enrollments by name (`acctName`), falling back to "Enrollment <number>"; subscriptions by name with the
  ID on hover.
- **Missing values:** "n/a" with the reason in the note ("needs the amortized basis"), never 0 or a blank.
- **Tone:** plain and specific. Say what a figure is and what it is not ("an upper bound, not a recommendation").

## Layout

- **Page:** at most 1,480px wide, centred, 16px padding, 14px between blocks.
- **Header:** a sticky rounded bar with the brand, tabs grouped as **Analyse** (Overview, Explorer, Spend,
  Applications, AI & Database), **Save** (Commitments, Optimization) and **Reference** (Pricing, Data, Knowledge),
  the Activity button and the theme button. Below 1,100px the tabs fold into one menu.
- **Scope bar:** under the header: period buttons, the Combined chip, the data-warnings chip and Filters. The
  scope (period, basis, currency, filters) is always visible and lives in the URL.
- **Overview grid:** 12 columns, 16px gap. Headline 5 columns beside 7 for usage, payment model and the
  Commitments group; then What changed (7) and Opportunities (5); then services (8) and attribution (4); then the
  chart and the ranked lists. Below 1,000px every block takes the full width.
- **Card rows:** `.kpis` fits as many cards as there is room for, at least 165px each.
- **Lists:** `.grid-2` and `.grid-3` wrap at 380px and 300px per column.

## Components

| Component | File | Use it for |
|---|---|---|
| `Kpi` | `Kpi.tsx` | One figure with label, note, `info` tooltip, optional `full` value and children (usually a split). `hero` for the headline |
| `SplitBar` | `EnrollmentSplit.tsx` | Per-enrollment split of an amount that adds up: a 6px stacked bar plus a legend. The bar is drawn only when every part is in one currency; negative parts take no width but stay in the legend |
| `RatioSplit` | `EnrollmentSplit.tsx` | Per-enrollment split of a ratio: a marker per enrollment on a 0–100% track. Never stack ratios |
| `StackTrack`, `Dot` | `EnrollmentSplit.tsx` | The bar alone, and the colour dot for an enrollment in text |
| `BarList` | `BarList.tsx` | Ranked lists. Each bar is split into enrollment colours; the row name filters to it; ↗ opens it in the Explorer one level deeper |
| `CostChart` | `CostChart.tsx` | Daily or monthly cost: area or bars, optionally stacked by enrollment, with hover tooltip and a table view |
| `PayBar` | `BentoTiles.tsx` | How usage is paid: one bar of pricing models, with a mini bar per enrollment |
| `SortTable` | `SortTable.tsx` | Sortable tables; sticky header, numeric columns right-aligned |
| `PeriodPicker` | `PeriodPicker.tsx` | Period buttons worked out from the data, plus Custom |
| `CombinedNote` | `CombinedNote.tsx` | The basis and currency chip; in the Combined view it opens the conversions and adds a warning banner only when a rate is estimated or missing |
| Banners | `.banner`, `.banner.warn`, `.banner.error` | Page-level status: partial periods, synthetic data, estimated rates, import progress |
| Segmented control | `.seg` | Two to five mutually exclusive options (grain, measure, split). The selected button has `aria-pressed="true"` |
| Chips | `.chip-pill` | Compact status in the scope bar |

### Charts

- Use a chart for five or more values of one measure, and a sentence or a small table for fewer.
- Lines and areas for levels over time, bars for months and comparisons, and stacked only when the parts add up to
  the total.
- Gridlines in `--grid` and axes in `--axis`; a single series in `--series-1`; enrollments in their colours.
- Every chart has a title with its unit and currency, a legend when it has more than one series, a hover value,
  and a table view.

## Interaction

- **Filtering:** clicking a name in a list or table filters the whole workspace to it. Filters show as removable
  chips and are kept in the URL.
- **Drilling in:** ↗ opens a value in the Explorer one level deeper (service → meter sub-category, subscription →
  resource group).
- **Loading:** a muted "Loading…" in place of the content; a previous result stays visible but dimmed while a
  refresh loads (`.loading`, `aria-busy`).
- **Errors:** say what failed, with a Retry button. A long import pauses the dashboard and every page says so.
- **Actions that change data** (saving a rate, starting an import) are explicit buttons that say exactly what will
  happen, never a side effect of navigation.

## Accessibility

- Keyboard: everything is a real `button`, `select` or `input`; the focus ring is `--focus` and 2px.
- Contrast: text tokens meet WCAG AA on their surfaces in every theme. White text on the dark-theme accent is the
  weakest pairing; check it after any palette change.
- Screen readers: coloured bars have `role="img"` with an `aria-label` that reads the values; status updates use
  `role="status"` / `aria-live="polite"`; icon-only buttons have `aria-label`.
- Touch: interactive targets are at least 34–40px high in the header and scope bar.

## Adding something new

1. Use the existing component if one fits. If not, build it from the tokens above and add it to the table here.
2. Show both enrollments, by name and colour, and the period and currency.
3. Give every figure a definition (`info`), a short and a full form, and a text equivalent for any colour.
4. Check it in all four themes and at phone width.
5. Describe it on the Knowledge tab in the same change.
