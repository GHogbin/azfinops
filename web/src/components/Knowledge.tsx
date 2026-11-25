import { useMemo, useState } from "react";

interface Topic {
  id: string;
  title: string;
  what: string;
  reads: string;
  how: string[];
  caveats: string[];
}

const PRINCIPLES: { title: string; body: string }[] = [
  { title: "Actual and amortized are never mixed", body: "Actual is what was billed and reconciles to the invoice. Amortized spreads reservation and savings-plan purchases over the period they cover and adds UnusedReservation and UnusedSavingsPlan rows. Every screen shows one basis at a time, and the basis is in the scope chip at the top." },
  { title: "Cost is the billed cost", body: "The authoritative figure is CostInBillingCurrency. Quantity multiplied by price is never assumed to equal cost. Sums are held at full precision and rounded only for display." },
  { title: "Currencies are converted only when you ask", body: "Native currencies are never added together. The Combined options convert them at the saved monthly rate; a saved reverse rate is inverted. Rates support 18 decimal places. Inverse rates and converted aggregate amounts are rounded to 18 places, ties to even, using exact decimal arithmetic rather than floating point. A month without its own rate uses the nearest saved month for that currency and is marked estimated. A currency with no rate is left out and named. These list-price or hand-entered rates do not reconcile to an invoice. Savings headroom remains separate in each native currency." },
  { title: "Signed values", body: "Credits, refunds and corrections are negative and are kept as negative. Nothing is shown as an absolute value, so totals match the source." },
  { title: "Missing is not zero", body: "When a unit price is missing it is treated as unknown and the row is left out of price comparisons. Exclusions are counted and shown, never valued at zero." },
  { title: "Verified versus unverified", body: "Figures that can be reconciled (totals against imports, aggregates against facts) are labelled as checks. Estimates, such as savings headroom, are labelled as upper bounds and are not promises." },
  { title: "Complete months only", body: "Completeness is checked separately for every enrollment, native currency, cost basis and month. Every calendar date must be present for every selected scope, including a scope with no rows in that month. Month comparisons and purchase-like resource-change figures are unavailable unless both months are complete. Date coverage cannot prove that every export partition was supplied; always select all partitions." },
];

const TOPICS: Topic[] = [
  {
    id: "overview", title: "Portfolio Overview",
    what: "A one-page summary: total cost for the latest complete month and its change on the month before, usage and how it is paid for, commitments, what changed, where cost could come down, top services, attribution and the daily trend. Every card shows each enrollment in its own colour.",
    reads: "Daily aggregate table (agg_daily) for the chosen cost basis, currency and filters. Commitment figures come from the same table.",
    how: [
      "Total: the sum of cost for the selected period, on the selected basis and currency. The dashboard opens on the newest month every enrollment covers in full, and the label says which period the total covers.",
      "Summary strip (top of the page): covers every date with data for the selected basis, currency and filters; the period buttons do not change it. Total cost over all dates; latest complete month (the newest month with every day loaded) against the month before; subscriptions and distinct resources with cost; billing records and the date coverage.",
      "30-, 60- and 90-day trends: average cost per day over the last N days with data against the N days before. Each needs twice N days loaded, and says so until there are. Top 3 service share: the three largest meter categories' share of total cost, and the largest one's own share.",
      "Headline trend: the daily total as a line over a stacked area of each enrollment's contribution, in the selected currency (converted in the Combined view), from a zero baseline. Weekends are shaded and the dashed line is the daily average for the period. Hover a day for the total and each enrollment's amount and share. If an enrollment has a negative day the area is not split.",
      "Commitments group: unused commitment, reservation utilization, commitment coverage and observed discount, together and per enrollment.",
      "Change vs previous month: for a single month, its total minus the month before; for a longer range, the last month in it minus the one before. Shown as an amount and as a percentage of the earlier month.",
      "How usage is paid: Usage rows grouped by pricing model (on demand, reservation, savings plan, spot and so on) as a share of Usage cost.",
      "Unused commitments: the cost of UnusedReservation and UnusedSavingsPlan rows. These exist only on the amortized basis.",
      "Attributed share: cost with an application tag divided by total cost (see Applications).",
      "Where the money goes: a Sankey from enrollment to service to application (or subscription, resource group or pricing model). Band width is cost; bands keep their enrollment's colour. The 8 largest values in each column are shown and the rest are grouped as Other. A Sankey cannot draw a negative flow, so any path that nets below zero (credits, refunds) is left out and its total is stated under the chart. Show as table lists every path.",
      "What changed bridge: starts at the earlier month's total, adds each biggest increase, then everything else, takes off each biggest decrease, and ends at the later month's total. The axis starts near the lowest running total rather than zero, and says so, so that the changes are visible.",
      "What changed: per service or subscription, the later month's cost minus the earlier month's, for the two months being compared. The five biggest increases and decreases are shown; new items have no percentage.",
      "Opportunities: unused commitment; on-demand usage in services that already have commitments (eligible minus covered usage, at contract price); savings-plan headroom is on the Optimization tab and is not available in the Combined view.",
      "Top lists (subscriptions, applications, resource groups, services): ranked by cost, with each value's share of the total; the header says how much of the total the listed rows cover. Two coloured dots mean the value has cost in both enrollments (hover for the split). Names in italics, such as (untagged) or (none), are cost with no tag or no value: a gap to fix rather than a real name. Hover a name for its full text and ID; click it to filter, or ↗ to open it in Explorer.",
      "Enrollment splits: amounts that add up (cost, usage, unused, discount) are drawn as one stacked bar, but only when every enrollment is in one currency; ratios (utilization, coverage, attribution) are drawn as a marker per enrollment on a 0–100% track. Negative amounts are listed but take no width.",
      "Chart by enrollment: the daily and monthly chart stack each enrollment's cost, in the selected currency (converted in the Combined view), so the layers add up to the total. Days where an enrollment's amount is negative are not stacked; the chart shows one total instead.",
      "Card figures are shortened (for example $1.2M); hover over a figure for the exact value. Tables show exact values.",
      "Data warnings: a chip in the header appears when a Data Management check warns or fails. Row counts and the full checks are on that screen.",
    ],
    caveats: ["The overview ignores nothing you filter: if filters are applied, every tile is recalculated for that scope.", "Month-on-month needs two complete months; otherwise the change is shown as not available."],
  },
  {
    id: "analysis", title: "Cost analysis",
    what: "One chart you reshape, like Cost analysis in the Azure portal: group by any dimension, daily, monthly or accumulated, as stacked area, stacked or grouped column, line, donut or table, with the previous period, a month-end forecast and a budget line. Chart filters (+ Add filter) narrow this chart only, on top of the dashboard filters; where both filter the same field, only values in both count. Views can be saved and reopened, chart filters included.",
    reads: "agg_daily (through the Combined view when it is on). Budgets and saved views are JSON files next to the database (budgets.json, views.json).",
    how: [
      "Series: the 8 largest values of the chosen dimension over the whole period, by signed cost; the rest are added up as Others. Days (or months) with no cost are filled with zero so the chart has no gaps.",
      "Accumulated: a running total of the daily figures. Monthly: calendar months.",
      "Previous period: for a range of whole months, the same number of months before; otherwise the same number of days immediately before. It is drawn day 1 against day 1 (dotted), and its total is compared with this period's.",
      "Forecast to month end (accumulated only): the running total carried on to the end of the last month at the average daily cost of the last 7 days with data, for the series shown. Nothing is forecast when the month is already complete or the range ends earlier.",
      "Budget: one monthly amount per enrollment (filter to one enrollment) or for everything in the current currency. Daily view: budget ÷ days in the month; monthly view: the budget; accumulated: the budget of each month reached so far. '% used' compares the period's total (and the forecast) with the budget when the period is within one month.",
      "Legend: click a colour to hide or show a series (totals, forecast and CSV follow what is shown); click a name to filter the whole dashboard to it.",
    ],
    caveats: ["A stacked area cannot draw negative amounts (credits); they are left out of that chart only, and it says so. Stacked column, line and table show them.", "The forecast is a straight-line projection of recent days, not a prediction of known changes such as new reservations or month-end charges.", "Budgets are not converted between currencies: a budget belongs to one currency, and the Combined view uses the budget set for its target currency."],
  },
  {
    id: "explorer", title: "Cost Explorer",
    what: "Group and filter cost by any dimension (service, subscription, resource group, location, tag and others) and view it by day or by month. Clicking a service opens its meters.",
    reads: "agg_daily for the table and chart. The service drill-down reads the monthly resource aggregate (agg_resource_month).",
    how: [
      "Each row is the sum of cost for one value of the chosen dimension, with its share of the total.",
      "Daily and monthly views are the same data grouped at different grains; monthly totals equal the sum of the days.",
      "Service drill-down: the meters inside one service, one row per meter name and unit, sorted by cost, with the top n shown and the remainder as Other, so the rows add up to the service total. Azure gives a meter a separate ID per region and variant (Linux, Windows, low priority); those are merged into one row, which lists them with their sub-category, region and cost.",
    ],
    caveats: ["The drill-down is at monthly grain, so a few of the daily-only filters cannot be applied to it; the panel lists any that were not applied.", "A meter name can repeat across units; quantities are only comparable within one meter and unit."],
  },
  {
    id: "spend", title: "Spend / Charge Analysis",
    what: "What the bill is made of (charge type, frequency, pricing model) and why cost moved between two months.",
    reads: "agg_daily for composition. Month comparison uses meter-level monthly figures.",
    how: [
      "Composition: cost split by charge type (Usage, Purchase, Tax, Refund and so on) and by pricing model.",
      "Movement splits the change per meter and unit into volume and price. Volume = (q1 − q0) × p0. Price = c1 − q1 × p0, where p = cost / quantity, and 0 is the earlier month, 1 the later month.",
      "The two parts add up to the change in cost for that meter.",
      "New meters (nothing in the earlier month), removed meters (nothing in the later month) and meters with zero quantity are reported separately, because a unit price cannot be derived for them.",
    ],
    caveats: ["Quantities are only comparable within the same meter and unit, so different units are never merged.", "A change in mix between meters shows up as new or removed meters, not as a price change."],
  },
  {
    id: "commitments", title: "Commitments & Savings",
    what: "Reservations and savings plans: how much was used, how much was covered, what the observed discount was, which reservations waste the most, on-demand spend in services that already have commitments, and savings-plan headroom.",
    reads: "agg_daily, the reservation rows of agg_resource_month (amortized basis) and, for headroom, the loaded price sheet.",
    how: [
      "Utilization (hours) = used hours / (used hours + unused hours), reservations only. Hours of different reservations are different units (for example vCore-hours and instance-hours), so the blend is dominated by large reservations.",
      "Utilization (cost) = used amortized cost / (used + unused amortized cost): the share of reservation spend that was actually used. Per reservation it equals the hours ratio.",
      "Coverage = Contract-price value of benefit-covered usage / (covered + on-demand contract-price value) within meter categories that have any covered usage. Spot and Marketplace are excluded, and so are rows with no PayG price (counted separately).",
      "Observed discount (gross) = contract-price value of covered usage − the amortized cost of that usage. Net = gross − unused commitment cost. It is historical, not an estimate.",
      "The baseline is the contract unit price (UnitPrice). The export’s PayGPrice column is not imported, so this measures commitment savings against your contract price, not against retail. The number of rows valued this way is shown.",
      "Usage per hour not covered by savings plans: on-demand usage cost in meter categories a savings plan for compute can apply to (a standard list plus any category with savings-plan usage), per day ÷ 24. Shown for the latest day, the 7- and 30-day averages, and the lowest day in the last 30, beside what plans cover and leave unused per hour. These category averages cannot size an hourly purchase.",
      "On-demand next to commitments: on-demand cost in services where commitments already exist, which is where extra coverage is most likely to apply.",
      "Savings-plan headroom: only the latest month in the selected period that has on-demand usage is used, so it describes what is uncovered now. Each on-demand usage row (usage no reservation or savings plan covered) is matched to the price sheet for its month by meter, offer and billing unit. Where one on-demand price and one savings-plan price exist, saving = cost × (1 − plan price / on-demand price), never below zero.",
      "Hourly purchase recommendations are unavailable: daily category averages can stay steady while eligible meters disappear. Sizing needs hourly usage for the same matched, plan-eligible meters and existing commitment coverage. Monthly ceilings for the 1- and 3-year terms are alternatives, never amounts to add together.",
      "Eligible means the meter has a plan price. Cheaper means the plan price is below the on-demand contract price.",
      "Cost that cannot be matched, including rows without a meter ID, is ambiguous, or falls in a month with no price sheet gets no saving. All on-demand cost remains in the total and its exclusion category.",
    ],
    caveats: ["If there are no capacity rows, utilization is shown as not available rather than 100% or 0%.", "Per-hour figures are daily averages (the export is daily), and category eligibility is approximate: some meters in those categories are not covered by savings plans.", "Headroom is worked out per currency, each enrollment against its own price sheet, so in the Combined view there is one block per enrollment in its own currency rather than one converted total.", "Headroom is an upper bound. It assumes every eligible hour is covered, but a real plan is sized on a steady hourly baseline and the export is daily.", "Headroom: reservation prices are not compared because none are loaded, and only part of on-demand cost is matched to the sheet; the matched share is shown beside the figure.", "Savings plans have no hour denominator in this data, so only cost-based figures apply to them.", "Which meters are eligible for a commitment is inferred from the data, not taken from a published list."],
  },
  {
    id: "optimization", title: "Optimization",
    what: "Resources rightsized or decommissioned since the month before: what changed in the estate and what it is worth per month. Unused commitment, on-demand spend next to commitments and savings-plan headroom are on the Commitments & Savings tab.",
    reads: "agg_resource_month (usage rows, with the first and last day each resource ran in the month).",
    how: [
      "Rightsized and decommissioned: each resource's usage in the latest month is compared with the month before. Decommissioned means it ran at the end of the earlier month and stopped before the end of the later one. Resized means it ran at the end of both but on a different meter (for VMs, disks and databases, the size or tier). The change per month is a run-rate: cost per day of the meters running at month end, before and after, times the days in the later month.",
    ],
    caveats: ["A resource deleted and re-created under a new ID shows as decommissioned (the new one is not listed). A size change billed on the same meter, such as more vCores on a per-vCore meter, is not detected. Amortized cost includes the reservation or savings-plan share, so a resize that leaves a reservation can look like an upsize."],
  },
  {
    id: "applications", title: "Applications",
    what: "Cost by application, and how much cannot be tied to one.",
    reads: "agg_daily, using the application attribution computed at import.",
    how: [
      "Application = the first non-blank value of the mnd-applicationid tag, then the ApplicationID tag, in lower case.",
      "(untagged) means the resource has no tags at all. (unattributed) means it has tags but none of the configured application keys.",
      "Attributed share = cost with an application / total cost.",
      "Environment comes from the mnd-envtype tag (the key must match exactly, including case); cost with no value is reported as none. Set AZFINOPS_ENV_TAG_KEYS to read other keys.",
      "The gap by service shows which services hold the untagged and unattributed cost, so tagging effort goes where it matters.",
    ],
    caveats: ["Where the two application tags disagree, the first non-blank key in the order above wins.", "Workload families (AI, data platform, databases) are matched by meter category keywords."],
  },
  {
    id: "ai", title: "AI & Database",
    what: "Spend on AI services, the data platform and databases, grouped into families.",
    reads: "agg_daily by meter category and month.",
    how: [
      "Each meter category is assigned to a family (AI services, Data platform, Databases) by matching keywords in its name. A category that matches none is not included.",
      "For each family the screen shows total cost, the categories inside it with the most cost, and the monthly trend.",
      "Cost in categories outside any family is reported as other, so the families plus other equal the total.",
    ],
    caveats: ["The grouping is a keyword rule, not an official taxonomy: a new service with an unfamiliar name may fall outside until the keywords are updated."],
  },
  {
    id: "pricing", title: "Pricing & Reference Data",
    what: "Compares what was charged with the contract-price value (unit price × quantity) of the same usage, and shows which reference data is loaded.",
    reads: "agg_daily, price benchmarks, price sheet and subscription names.",
    how: [
      "Over Usage rows that have a unit price, the contract-price value is compared with the cost actually charged. The difference is the saving from commitments and other discounts below the contract price.",
      "Rows without a unit price are counted and left out, not valued at zero.",
      "Reservation and savings-plan rows are valued at their contract unit price like any other row.",
      "On the amortized basis the gap includes commitment discounts as well as negotiated rates.",
      "Reference data lists what is loaded: price sheet rows, offers, price types, benchmark rows and named subscriptions against subscriptions seen in the data.",
      "Exchange rates: pressing Query asks the public Azure Retail Prices API for the same items in two currencies and reports the median of (price in the target currency / price in the source currency), with the number of items and the lowest and highest ratio. You then save it against a month, or type a rate by hand. Rates are kept in a small file next to the database.",
    ],
    caveats: ["Price-sheet prices are per unit-of-measure block; a blank base price means unknown.", "Exchange rates are today’s list-price rates, not invoice rates. They are used only when you choose the Combined option in Filters. This is the only screen that contacts the internet.", "Units are normalised (block size, K and M suffixes, case, hours) but TB to GB and per-day to per-month are not converted, so those meters stay unmatched."],
  },
  {
    id: "data", title: "Data Management",
    what: "Import more usage-and-charges exports and price sheets, and see what is in the database and whether it can be trusted: quality checks, import history and a read-only SQL workbench.",
    reads: "Import batches, rejected rows, the fact view, aggregates and the schema catalogue.",
    how: [
      "Import files: lists the CSV files in the allowed import folders (<data folder>\\imports and the project's samples folder, or AZFINOPS_IMPORT_DIRS). Usage files are one batch each, except partitions of one export (names ending _0, _1, …), which go together; after each batch the aggregates for its months and the subscription and enrollment names are rebuilt, exactly as the command line does. Price sheet files are imported together and replace a period already loaded.",
      "Select all chooses every file shown for the current file type, including unrecognised files and files marked imported; it does not select hidden files of the other type. Clear selection unchecks them. The selected-file count and import size update together, changing file type resets the selection, and bulk selection is disabled while an import runs. Selecting files does not start an import or bypass duplicate/re-import checks. The API accepts up to 200 files per import; larger selections show a warning and cannot be submitted until reduced.",
      "While an import runs, the dashboard's read-only connection is closed (DuckDB allows one writer and no readers). A refused ingestion leaves old committed data intact. Once ingestion commits, pending aggregate work is recorded in the database. If a later step fails, data may already be committed: cost screens stay unavailable, even after restart, until you stop the server, run azfinops-aggregate and restart it. Import history and quality diagnostics remain available.",
      "Replacing an overlapping batch requires every enrollment, currency and date from that batch, including months outside the new selection. A narrower replacement is refused instead of hiding unrelated data. Always supply all export partitions; date coverage alone cannot detect an omitted partition.",
      "Successful cost imports retain one shared latest-12-calendar-month window across every enrollment, actual/amortized basis and currency. The newest committed cost month anchors the window, including a partial newest month: October 2026 keeps November 2025 through October 2026. Expired raw cost rows, including superseded copies, and their aggregate/freshness rows are purged atomically with the import commit. A refused import does not purge old data.",
      "Source CSVs, price sheets, saved rates/settings, backups and import audit history are not deleted by retention. Accepted/rejected file counts remain the original history; cumulative purge counts appear in affected batches' validation messages. An entirely out-of-window export is refused; older rows in a mixed-period export are counted as accepted then purged. Future cost months are refused before they can advance the destructive cutoff. Deletion frees reusable database space but does not promise a smaller file on disk.",
      "Fast integrity checks run automatically using import metadata and aggregates: pending rebuilds, failed imports, rejected rows, scoped month completeness, synthetic data, baseline-price gaps, attribution, price sheets and names. They do not scan raw facts or claim that totals have been reconciled.",
      "Run full reconciliation is an explicit Data Management action. It additionally compares imported facts with daily aggregates and daily totals with resource-month totals. These checks are marked not run until requested; the normal 30-second query deadline still applies. If a check cannot run, its error is shown rather than implying success.",
      "Import history lists each batch with its original file counts, retention messages and when it ran. Only committed facts inside the retained window contribute to analysis.",
      "SQL workbench: one SELECT statement at a time against the read-only database. Limits: 20,000 characters, 1,000 rows, 100 columns, 500 characters per cell and a 20 second timeout. File access and configuration changes are disabled.",
      "Results can be copied or downloaded as CSV; cells that could be read as formulas are quoted.",
    ],
    caveats: ["Only one process can write to the database. Stop the server before command-line imports, aggregation or migration; the dashboard import job pauses its readers itself.", "Every POST endpoint requires a custom header and an allowed Host. Cost data stays read-only outside the guarded import job; exchange rates, budgets and saved views use side files."],
  },
];

function matches(t: Topic, q: string): boolean {
  if (!q) return true;
  const text = [t.title, t.what, t.reads, ...t.how, ...t.caveats].join(" ").toLowerCase();
  return q.toLowerCase().split(/\s+/).filter(Boolean).every((w) => text.includes(w));
}

export function Knowledge() {
  const [q, setQ] = useState("");
  const shown = useMemo(() => TOPICS.filter((t) => matches(t, q)), [q]);
  const showPrinciples = !q || PRINCIPLES.some((p) => `${p.title} ${p.body}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="knowledge">
      <div className="know-search">
        <label htmlFor="know-q" className="muted small">Search this guide</label>
        <input id="know-q" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. utilization, coverage, tags" />
      </div>
      <nav className="know-toc" aria-label="Guide contents">
        {showPrinciples && <a href="#k-principles">Principles</a>}
        {shown.map((t) => <a key={t.id} href={`#k-${t.id}`}>{t.title}</a>)}
      </nav>
      {showPrinciples && (
        <section className="card" id="k-principles" aria-label="Principles">
          <header className="card-head"><h3>Principles that apply everywhere</h3></header>
          <dl className="know-dl">
            {PRINCIPLES.map((p) => (<div key={p.title}><dt>{p.title}</dt><dd>{p.body}</dd></div>))}
          </dl>
        </section>
      )}
      {shown.map((t) => (
        <section className="card" id={`k-${t.id}`} key={t.id} aria-label={t.title}>
          <header className="card-head"><h3>{t.title}</h3></header>
          <p>{t.what}</p>
          <h4>Data it reads</h4>
          <p className="muted">{t.reads}</p>
          <h4>How it is calculated</h4>
          <ul>{t.how.map((h) => <li key={h}>{h}</li>)}</ul>
          <h4>Things to know</h4>
          <ul>{t.caveats.map((c) => <li key={c}>{c}</li>)}</ul>
        </section>
      ))}
      {shown.length === 0 && !showPrinciples && <p className="empty">Nothing in the guide matches “{q}”.</p>}
    </div>
  );
}
