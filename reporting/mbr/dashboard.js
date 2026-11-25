"use strict";
(() => {
  const SCALE = 18;
  const FACTOR = 10n ** 18n;
  const MISSING = "(not supplied)";
  const DIMENSIONS = ["month", "enrollment", "service", "subscription", "region", "pricing", "portfolio", "allocation", "eligible", "usage"];
  const LABELS = { month: "Reporting month", enrollment: "Enrollment", service: "Service", subscription: "Subscription", region: "Region", pricing: "Pricing model", portfolio: "Portfolio", allocation: "Benefit allocation", eligible: "Credit eligible", usage: "Usage type", query: "Subscription contains", source: "Workbook", publisher: "Publisher type" };
  const SUBSCRIPTION_CHANGE_DEFINITION = "Signed cost change is current minus comparison cost. Percentage change is 100 x change / comparison cost, available only when both enrollment periods are supplied and the comparison cost is positive. New, zero-base and negative-base subscriptions have no invented percentage. Groups use source subscription names within each enrollment, not subscription IDs; renamed subscriptions can appear as new/removed names. Annual history never widens the two-month detailed comparison. Combined USD changes may include FX movement.";
  const DEFINITIONS = [
    ["Recorded spend", "Native views use original signed source costs. Combined USD leaves original dollars untouched and converts other original native amounts at saved monthly rates, once per retained reporting group with exact half-even 18-place arithmetic. Saved Cost GBP is source evidence, not a reverse-FX basis for dollars. Credits/refunds remain signed; native currencies are never added together without the explicit Combined selection."],
    ["Reporting currency", "All calculation tabs share the selected enrollment and its original native currency, without FX. Combined USD ($) is the explicit converted alternative. Direct saved rates take precedence over inverse rates for the same month; nearest saved months are estimated and named. Missing rates exclude and name the affected data, never value it at zero. No internet lookup or guessed rate; list-price conversions are not invoice FX."],
    ["Benefit cost", "Selected recorded cost whose PricingModel is Reservation or SavingsPlan. This is cost, including unused allocations, not a retail-equivalent value, a coverage percentage or an achieved saving."],
    ["Unused benefit cost", "Cost of Reservation or SavingsPlan rows whose workbook Utilization Type is Unused. This is a cost measure, not hourly utilization or a rightsizing recommendation."],
    ["Credit-eligible spend", "Cost on selected rows explicitly marked IsAzureCreditEligible = true. Its share is that signed cost divided by all selected signed cost. Eligibility does not establish that credits were consumed."],
    ["Portfolio", "The source Sub Type classification is preserved. Greenfield / Brownfield is a workbook naming rule, not independently verified application attribution."],
    ["Advisor estimates", "Annual estimates keep their own snapshot dates and may overlap. Native reporting sums only matching original source currencies. Combined USD prefers original estimates, converting foreign ones at the source update month's saved rate; an explicit source GBP estimate is used only when native amount/currency is unavailable and is labelled as such. Missing rates/dates remain unavailable, never zero. Snapshot changes can include FX movement. No enrollment mapping or realized saving is inferred."],
    ["Corrected source-price savings estimate", "Native used Reservation/SavingsPlan estimates are source Unit price x Quantity minus original native cost, calculated before reporting aggregation without FX. Combined USD converts the separately retained native gross/tier components and unused costs at saved monthly rates; it does not reverse the stored GBP estimate. OnDemand aliases do not generate covered savings. Commitment tiers require explicit discounts. Missing native calculation inputs remain unpriced. Baseline provenance is unverified: these are estimates, not realized savings."],
    ["Credit proxy and coverage", "Eligible cost uses the selected reporting currency. Native views compare only same-currency source pools. Combined USD converts a unique source pool at the selected reporting month's saved rate, explicitly estimated when carried; source references remain in their original currency. Conflicting or unconvertible pools are unavailable. This is not an issuance-date FX assumption or proof of drawdown. Pool less eligible cost is not a certified credit balance. Missing months are never zero."],
    ["Calendar-year run-rate scenario", "Future months use the selected month's daily pace, scaled by the explicit pace-change assumption and each future month's days. Each projected month is rounded half-even to 18 places and totals sum those months. Missing past months prevent a full-year scenario unless separate earlier-period amounts are explicitly supplied. Supplied periods are assumed complete for the scenario, not certified complete."],
    ["Cost-weighted utilization", "Used benefit cost / (used + unused benefit cost), not hours, instances or vCores. The workbook SavingsPlan headline uses Virtual Machines; Reservation uses all services. Service rows use that service's covered cost. A non-positive denominator is unavailable."],
    ["Source history", "Detailed reports compare the two newest supplied billing months shared across enrollments. Separate exact grouped annual history is used only for calendar-year/YTD figures, credit/savings matrices and projections; backfills do not widen detailed comparisons or repeat current months. Annual filters still apply, years stay separate, and missing periods are unavailable. Replacements need explicit account/month confirmation, including historical scopes. Advisor keeps two separate dated snapshots."],
    ["Corrected publisher split", "Azure/Marketplace figures use the source PublisherType column, not the workbook Usage Type heuristic, which misclassifies Marketplace rows. Original Usage Type remains in detailed exports. The Trend workbook's Azure-only publisher scope can be selected explicitly."],
    ["Cost-driver tables and charts", "Region and service pivots, paired monthly column charts and mover lists use the same selected enrollment, currency, two named source periods and publisher filters. Azure is the default publisher for this workspace. Other reconciles the remaining chart groups; it is not omitted spend. Changes require both periods for every selected enrollment. Percent ranks require a positive prior base and use exact ratios, not rounded percentages. New and negative-base groups keep their signed changes but have no percent rank."],
    ["Annual credit-eligibility presentation", "The January-December matrix and combined chart show credit-eligible cost, not verified credits consumed. Missing, expired and future actual months remain unavailable. Columns show known cumulative eligible cost in reporting currency; lines show coverage only against positive same-currency source pools. Full-year baseline requires all earlier source months. Recorded plus future can be shown separately with missing past periods explicitly excluded."],
    ["Workbook savings presentation", "The savings/utilization comparison is grouped by selected enrollment and currency. Savings movement is (current minus prior) / prior, unavailable at zero or with missing/unpriced periods. Cost-weighted utilization movement is percentage points, not percent-relative change. January-December model rows are non-overlapping: SavingsPlan, Reservation, explicit tiers from source categories Log Analytics / Azure Monitor, and any other explicit tiers. Monthly bars and asterisked amounts can show known partial components; these are not complete realized savings. Complete full-year baselines require missing history and pricing to be resolved."],
    ["Enrollment chart colours", "Enrollment colours come from the sorted source enrollment names, rather than the order of a filtered chart. Chart points, lines, columns and legends use the same theme-aware colour. Combined aggregate totals stay neutral; signed values, partial markers and scenario dashes keep their meaning."],
    ["Dashboard appearance", "Clean light is the default. Interface controls and highlighted surfaces use neutral charcoal/grey accents, not pink. Dark charcoal uses the dark palette; Compact workbook uses denser desktop tables/cards; Executive review uses larger desktop figures and more spacing. All preserve enrollment colours and financial values. Only the appearance preference is saved in this browser; commentary, scenario inputs and financial data are not saved there. Print uses the standard report layout."],
    ["Subscription changes", SUBSCRIPTION_CHANGE_DEFINITION],
  ];
  const pricingModel = value => ({ "On Demand": "OnDemand", "Savings Plan": "SavingsPlan" })[value] || value;
  const WORKBOOK_VIEWS = ["summary", "trend", "drivers", "subscriptions", "credits", "savings", "forecast"];
  class ScenarioInputError extends Error {}

  function defaultEnrollment(data) {
    return data.dimensions.enrollment.find(name=>data.rows.some(row=>
      data.dimensions.enrollment[row.ids[1]]===name && row.nativeCurrency==="USD"))
      || data.dimensions.enrollment[0] || "";
  }

  function enrollmentColor(name, enrollments) {
    const index=[...enrollments].sort().indexOf(String(name||"").trim());
    return index<0?null:`var(--cp-enrollment-${index%2+1})`;
  }

  function parseUnits(value) {
    if (typeof value !== "string" || !/^-?\d+$/.test(value)) throw new Error("Invalid monetary integer in snapshot");
    return BigInt(value);
  }

  function roundDivide(numerator, denominator) {
    if (denominator === 0n) throw new Error("Cannot divide by zero");
    const negative = (numerator < 0n) !== (denominator < 0n);
    const n = numerator < 0n ? -numerator : numerator;
    const d = denominator < 0n ? -denominator : denominator;
    let result = n / d;
    const remainder = n % d;
    if (remainder * 2n > d || (remainder * 2n === d && result % 2n !== 0n)) result += 1n;
    return negative ? -result : result;
  }

  function decimalToUnits(value) {
    const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(String(value));
    if (!match) throw new Error("Invalid decimal amount");
    const fraction = match[3] || "";
    let result = BigInt(match[2] + fraction);
    const places = SCALE - fraction.length;
    result = places >= 0 ? result * 10n ** BigInt(places) : roundDivide(result, 10n ** BigInt(-places));
    return match[1] ? -result : result;
  }

  function fixedInteger(value, digits, grouping = true) {
    const negative = value < 0n;
    const absolute = negative ? -value : value;
    const factor = 10n ** BigInt(digits);
    const whole = absolute / factor;
    const fraction = absolute % factor;
    return (negative ? "-" : "") + (grouping ? whole.toLocaleString("en-GB") : whole.toString())
      + (digits ? "." + fraction.toString().padStart(digits, "0") : "");
  }

  function currencyPrefix(currency) {
    return currency === "GBP" ? "\u00a3" : currency === "USD" ? "$" : currency + " ";
  }

  function formatMoney(value, digits = 2, currency = "GBP") {
    if (value === null) return "n/a";
    if (!Number.isInteger(digits) || digits < 0 || digits > SCALE) throw new Error("Invalid money precision");
    const rounded = roundDivide(value, 10n ** BigInt(SCALE - digits));
    const prefix = currencyPrefix(currency);
    const sign = rounded < 0n ? "-" : "";
    return sign + prefix + fixedInteger(rounded < 0n ? -rounded : rounded, digits);
  }

  function formatCompact(value, currency = "GBP") {
    if (value === null) return "n/a";
    const prefix = currencyPrefix(currency);
    const absolute = value < 0n ? -value : value;
    for (const [amount, suffix] of [[1000000000n, "B"], [1000000n, "M"], [1000n, "k"]]) {
      if (absolute >= amount * FACTOR) {
        const rounded = roundDivide(value * 100n, amount * FACTOR);
        return (rounded < 0n ? "-" : "") + prefix + fixedInteger(rounded < 0n ? -rounded : rounded, 2) + suffix;
      }
    }
    return formatMoney(value, 2, currency);
  }

  function formatRatio(numerator, denominator, digits = 1) {
    if (denominator === 0n) return "n/a";
    return fixedInteger(roundDivide(numerator * 100n * 10n ** BigInt(digits), denominator), digits) + "%";
  }

  function decimalString(value) {
    const absolute = value < 0n ? -value : value;
    const fraction = (absolute % FACTOR).toString().padStart(SCALE, "0").replace(/0+$/, "");
    return (value < 0n ? "-" : "") + (absolute / FACTOR).toString() + (fraction ? "." + fraction : "");
  }

  function prepare(snapshot, inheritedUsdFx = null) {
    if (snapshot.version !== 1 || snapshot.scale !== SCALE || JSON.stringify(snapshot.dimension_order) !== JSON.stringify(DIMENSIONS)) {
      throw new Error("Unsupported dashboard snapshot schema");
    }
    const dimensions = snapshot.dimensions;
    const usdFx=snapshot.usd_fx||inheritedUsdFx;
    const usdRates=new Map();
    if(usdFx){
      if(usdFx.version!==1||usdFx.target!=="USD"||!Array.isArray(usdFx.rows))throw new Error("Invalid USD FX evidence schema");
      for(const row of usdFx.rows){
        if(!/^[A-Z]{3}$/.test(row.from)||row.from==="USD"||row.to!=="USD"||!/^\d{4}-(0[1-9]|1[0-2])$/.test(row.month)
          ||!/^\d{4}-(0[1-9]|1[0-2])$/.test(row.rate_month)||typeof row.rate!=="string"
          ||!/^\d+(?:\.\d{1,18})?$/.test(row.rate)||typeof row.estimated!=="boolean")throw new Error("Invalid USD FX evidence row");
        const rateUnits=decimalToUnits(row.rate),key=row.from+"|"+row.month;
        if(rateUnits<=0n||rateUnits>=1000000n*FACTOR||usdRates.has(key))throw new Error("Invalid or ambiguous USD FX rate");
        usdRates.set(key,{...row,rateUnits});
      }
    }
    if (snapshot.retention && (snapshot.retention.limit !== 2 || dimensions.month.length > 2
      || JSON.stringify(snapshot.retention.months) !== JSON.stringify(dimensions.month))) throw new Error("Report retention does not match the stored months");
    for (const name of DIMENSIONS) {
      if (!Array.isArray(dimensions[name]) || dimensions[name].some(value => typeof value !== "string")) {
        throw new Error("Invalid dimension: " + name);
      }
    }
    const sources = snapshot.meta.sources || [snapshot.meta];
    const sourceIds = snapshot.row_sources || snapshot.rows.map(() => 0);
    if (sourceIds.length !== snapshot.rows.length) throw new Error("Source lineage does not match report rows");
    const calculations = snapshot.calculations;
    const publishers = snapshot.row_publishers || snapshot.rows.map(() => MISSING);
    if (publishers.length !== snapshot.rows.length || publishers.some(value => typeof value !== "string")) throw new Error("Invalid publisher lineage");
    if (calculations && (calculations.version !== 1 || calculations.rows.length !== snapshot.rows.length)) throw new Error("Invalid savings calculation schema");
    const nativeCurrencies = snapshot.row_native_currencies || snapshot.rows.map(() => MISSING);
    const chargeTypes = snapshot.row_charge_types || snapshot.rows.map(() => MISSING);
    const nativeCosts = snapshot.row_native_costs || snapshot.rows.map(() => null);
    const nativeCalculations = snapshot.native_calculations;
    if (nativeCalculations && (nativeCalculations.version !== 1 || nativeCalculations.rows.length !== snapshot.rows.length)) throw new Error("Invalid native calculation schema");
    if ([nativeCurrencies,chargeTypes,nativeCosts].some(values=>values.length!==snapshot.rows.length)) throw new Error("Invalid original-usage lineage");
    const rows = snapshot.rows.map((raw, index) => {
      if (!Array.isArray(raw) || raw.length !== DIMENSIONS.length + 2) throw new Error("Invalid report row");
      const ids = raw.slice(0, DIMENSIONS.length);
      ids.forEach((value, index) => {
        if (!Number.isInteger(value) || value < 0 || value >= dimensions[DIMENSIONS[index]].length) throw new Error("Invalid dimension reference");
      });
      if (!Number.isSafeInteger(raw[11]) || raw[11] < 1) throw new Error("Invalid source row count");
      const source = sourceIds[index];
      if (!Number.isInteger(source) || source < 0 || source >= sources.length) throw new Error("Invalid source reference");
      const calculation = calculations ? calculations.rows[index] : null;
      const nativeCalculation = nativeCalculations ? nativeCalculations.rows[index] : null;
      for (const item of [calculation, nativeCalculation]) {
        if (item && (!Array.isArray(item) || item.length !== 4 || item.slice(2).some(value => !Number.isSafeInteger(value) || value < 0)
          || item[2] + item[3] > raw[11])) throw new Error("Invalid savings calculation row");
      }
      return { ids, cost: parseUnits(raw[10]), count: raw[11], source, publisher: publishers[index],
        nativeCurrency: nativeCurrencies[index], chargeType: chargeTypes[index], nativeCost: nativeCosts[index]===null?null:parseUnits(nativeCosts[index]),
        nativeCalculation: nativeCalculation ? { gross: parseUnits(nativeCalculation[0]), tier: parseUnits(nativeCalculation[1]),
          pricedRows: nativeCalculation[2], missingRows: nativeCalculation[3] } : null,
        gross: calculation ? parseUnits(calculation[0]) : null,
        tier: calculation ? parseUnits(calculation[1]) : null,
        pricedRows: calculation ? calculation[2] : 0, missingRows: calculation ? calculation[3] : 0 };
    });
    const lookup = Object.fromEntries(DIMENSIONS.map(name => [name, new Map(dimensions[name].map((value, index) => [value, index]))]));
    const scopes = new Map();
    for (const row of rows) {
      const month = dimensions.month[row.ids[0]], enrollment = dimensions.enrollment[row.ids[1]];
      scopes.set(row.source + "|" + month + "|" + enrollment, { source: row.source, month, enrollment });
    }
    if(snapshot.annual_history&&(snapshot.annual_history.annual_history
      ||snapshot.annual_history.history_policy?.version!==1||snapshot.annual_history.retention
      ||snapshot.annual_history.meta.basis!==snapshot.meta.basis||snapshot.annual_history.meta.currency!==snapshot.meta.currency)){
      throw new Error("Invalid separate annual history schema");
    }
    return { snapshot, dimensions, rows, lookup, sources, scopes: [...scopes.values()],
      usdRates,
      annualData:snapshot.annual_history?prepare(snapshot.annual_history,usdFx):null,
      calculationsAvailable: Boolean(calculations),
      advisorSnapshots: snapshot.advisor_snapshots || [{ source: 0, rows: snapshot.advisor || [] }] };
  }

  function advisorAmount(row) {
    return Object.hasOwn(row, "annual_amount") ? row.annual_amount : row.annual_gbp;
  }

  function usdRate(data, currency, month) {
    return currency==="USD"?{from:"USD",to:"USD",month,rate:"1",rate_month:month,rateUnits:FACTOR,estimated:false,source:"Native USD; no FX"}
      :data.usdRates.get(currency+"|"+month)||null;
  }

  function usdMoney(amount, rate) {
    if(amount===null||rate===null)return null;
    const value=roundDivide(amount*rate.rateUnits,FACTOR);
    if(value<=-(10n**38n)||value>=10n**38n)throw new Error("USD conversion exceeds reporting decimal precision");
    return value;
  }

  function fxDescription(rate) {
    return rate.from==="USD"?"Native USD; no FX"
      :rate.from+" "+rate.month+" at "+rate.rate+" USD/"+rate.from+"; saved "+rate.rate_month
        +(rate.estimated?" (estimated)":"")+(rate.inverted?" (inverse)":"")+". "+(rate.source||"Source details not supplied")+". Not invoice FX.";
  }

  function combinedUsd(data) {
    const excluded=[],details=new Map();
    const rows=[];
    for(const row of data.rows){
      const month=data.dimensions.month[row.ids[0]],rate=usdRate(data,row.nativeCurrency,month);
      if(row.nativeCost===null||!rate){
        excluded.push({...row,fxReason:row.nativeCost===null?"Original native amount unavailable":"No saved "+row.nativeCurrency+"-to-USD rate for "+month});
        continue;
      }
      if(rate.from!=="USD")details.set(rate.from+"|"+month,rate);
      rows.push({...row,cost:usdMoney(row.nativeCost,rate),fx:rate,
        gross:row.nativeCalculation?usdMoney(row.nativeCalculation.gross,rate):null,
        tier:row.nativeCalculation?usdMoney(row.nativeCalculation.tier,rate):null,
        pricedRows:row.nativeCalculation?.pricedRows??0,missingRows:row.nativeCalculation?.missingRows??row.count});
    }
    const scopes=new Set(rows.map(row=>row.source+"|"+data.dimensions.month[row.ids[0]]+"|"+data.dimensions.enrollment[row.ids[1]]));
    const incompletePeriods=new Set(excluded.map(row=>data.dimensions.month[row.ids[0]]+"|"+data.dimensions.enrollment[row.ids[1]]));
    const advisorSnapshots=data.advisorSnapshots.map(item=>({...item,rows:item.rows.map(row=>{
      const native=row.native_annual!==null&&/^[A-Z]{3}$/.test(row.native_currency);
      const currency=native?row.native_currency:row.annual_gbp!==null?"GBP":null;
      const amount=native?decimalToUnits(row.native_annual):row.annual_gbp!==null?parseUnits(row.annual_gbp):null;
      const month=(row.updated||"").slice(0,7),rate=currency===null?null:usdRate(data,currency,month);
      const converted=usdMoney(amount,rate);
      return {...row,estimate_currency:"USD",annual_amount:converted===null?null:String(converted),
        estimate_basis:native?"Original "+currency+" estimate":row.annual_gbp!==null
          ?"Explicit source GBP estimate; original native amount unavailable":"No supplied annual monetary estimate",
        estimate_fx:rate,
        estimate_note:converted===null?(amount===null?"Source estimate unavailable":"No dated saved "+currency+"-to-USD conversion"):fxDescription(rate)};
    })}));
    return {...data,reportingCurrency:"USD",reportingMode:"converted",calculationsAvailable:true,rows,
      sourceScopesOriginal:data.scopes,fxExcluded:excluded,conversionDetails:[...details.values()],
      scopes:data.scopes.filter(scope=>scopes.has(scope.source+"|"+scope.month+"|"+scope.enrollment)
        &&!incompletePeriods.has(scope.month+"|"+scope.enrollment)),
      annualData:data.annualData?combinedUsd(data.annualData):null,advisorSnapshots};
  }

  function currencyEvidence(data) {
    if(data.reportingMode!=="converted")return data.reportingCurrency?"Native "+data.reportingCurrency+"; no FX":"Stored source GBP";
    const exclusions=[...new Set((data.fxExcluded||[]).map(row=>data.dimensions.enrollment[row.ids[1]]+" "
      +data.dimensions.month[row.ids[0]]+": "+row.fxReason))];
    return "Combined USD from original native amounts. "+(data.conversionDetails||[]).map(fxDescription).join(" | ")
      +(exclusions.length?" Excluded, not zero: "+exclusions.join(" | "):"");
  }

  function reportingData(data, enrollment = null, annualCurrency = null) {
    if (data.reportingMode) throw new Error("Create reporting currency contexts from original prepared source data");
    if (enrollment === null) return combinedUsd(data);
    const rows = data.rows.filter(row => data.dimensions.enrollment[row.ids[1]] === enrollment);
    if (!rows.length&&!annualCurrency) throw new Error("No native reporting data for " + enrollment);
    const currencies = [...new Set(rows.map(row => row.nativeCurrency))];
    if (!annualCurrency&&(currencies.length !== 1 || !/^[A-Z]{3}$/.test(currencies[0]) || rows.some(row => row.nativeCost === null))) {
      throw new Error(enrollment + ": native reporting requires one explicit currency and every original amount; no reverse FX is inferred");
    }
    const currency = annualCurrency||currencies[0];
    return { ...data, reportingCurrency: currency, reportingEnrollment: enrollment, reportingMode: "native",
      calculationsAvailable: true,
      annualData:data.annualData?reportingData(data.annualData,enrollment,currency):null,
      rows: rows.map(row => ({ ...row, cost: row.nativeCost??0n,
        nativeMoneyMissing:row.nativeCost===null||row.nativeCurrency!==currency,
        gross: row.nativeCalculation?.gross ?? null, tier: row.nativeCalculation?.tier ?? null,
        pricedRows: row.nativeCalculation?.pricedRows ?? 0, missingRows: row.nativeCalculation?.missingRows ?? row.count })),
      scopes: data.scopes.filter(scope => scope.enrollment === enrollment),
      advisorSnapshots: data.advisorSnapshots.map(item => ({ ...item, rows: item.rows.map(row => ({
        ...row, estimate_currency: currency,
        annual_amount: row.native_currency === currency && row.native_annual !== null
          ? String(decimalToUnits(row.native_annual)) : null,
      })) })),
    };
  }

  function filterSpend(data, filters) {
    const exact = DIMENSIONS.flatMap((name, index) => filters[name] ? [[index, data.lookup[name].get(filters[name])]] : []);
    const query = (filters.query || "").trim().toLowerCase();
    return data.rows.filter(row => (!filters.source || String(row.source) === filters.source)
      && (!filters.publisher || row.publisher === filters.publisher)
      && exact.every(([index, id]) => row.ids[index] === id)
      && (!query || data.dimensions.subscription[row.ids[3]].toLowerCase().includes(query)));
  }

  function summarizeSpend(data, rows) {
    let total = 0n, benefit = 0n, unused = 0n, eligible = 0n, used = 0n, gross = 0n, tier = 0n;
    let sourceRows = 0, unknownEligibility = 0, estimateRows = 0, estimateMissing = 0, nativeMissing = 0;
    const subscriptions = new Set(), months = new Set(), enrollments = new Set();
    for (const row of rows) {
      total += row.cost;
      sourceRows += row.count;
      if(row.nativeMoneyMissing)nativeMissing+=row.count;
      const pricing = pricingModel(data.dimensions.pricing[row.ids[5]]);
      if (pricing === "Reservation" || pricing === "SavingsPlan") {
        benefit += row.cost;
        if (data.dimensions.allocation[row.ids[7]] === "Unused") unused += row.cost;
        if (data.dimensions.allocation[row.ids[7]] === "Used") used += row.cost;
      }
      if (row.gross !== null) gross += row.gross;
      if (row.tier !== null) tier += row.tier;
      estimateRows += row.pricedRows;
      estimateMissing += row.missingRows;
      const flag = data.dimensions.eligible[row.ids[8]];
      if (flag === "Yes") eligible += row.cost;
      if (flag === MISSING) unknownEligibility += row.count;
      subscriptions.add(row.ids[3]);
      months.add(data.dimensions.month[row.ids[0]]);
      enrollments.add(data.dimensions.enrollment[row.ids[1]]);
    }
    const estimatesAvailable = data.calculationsAvailable && (estimateRows > 0 || !estimateMissing);
    return { available: rows.length > 0&&!nativeMissing, total, benefit, used, unused, eligible, sourceRows,nativeMissing,
      grossSavings: estimatesAvailable ? gross : null, tierSavings: estimatesAvailable ? tier : null,
      savings: estimatesAvailable ? gross + tier - unused : null,
      estimateRows, estimateMissing, subscriptions: subscriptions.size, unknownEligibility,
      months: [...months].sort(), enrollments: [...enrollments].sort(),
      ...(nativeMissing?{total:null,benefit:null,used:null,unused:null,eligible:null,grossSavings:null,tierSavings:null,savings:null}:{}),
    };
  }

  function addMonth(month, count = 1) {
    const [year, number] = month.split("-").map(Number);
    const date = new Date(Date.UTC(year, number - 1 + count, 1));
    return date.getUTCFullYear() + "-" + String(date.getUTCMonth() + 1).padStart(2, "0");
  }

  function monthRange(first, last) {
    const months = [];
    for (let month = first; month <= last; month = addMonth(month)) {
      months.push(month);
      if (months.length > 1200) throw new Error("Reporting range exceeds 100 years");
    }
    return months;
  }

  function daysInMonth(month) {
    const [year, number] = month.split("-").map(Number);
    return new Date(Date.UTC(year, number, 0)).getUTCDate();
  }

  function sourceScopes(data, filters = {}) {
    return data.scopes.filter(scope => (!filters.source || String(scope.source) === filters.source)
      && (!filters.enrollment || scope.enrollment === filters.enrollment));
  }

  function defaultReportingMonth(data, filters = {}) {
    const scopes = sourceScopes(data, filters);
    const names = [...new Set(scopes.map(scope => scope.enrollment))];
    const months = [...new Set(scopes.map(scope => scope.month))].sort();
    if (data.snapshot.retention) return months.at(-1) || null;
    return [...months].reverse().find(month => names.every(name => scopes.some(scope => scope.month === month && scope.enrollment === name)))
      || months[months.length - 1] || null;
  }

  function creditPool(data, filters, name) {
    const entries = data.sources.flatMap((source, index) => !filters.source || filters.source === String(index) ? source.credit_inputs || [] : [])
      .filter(row => row.enrollment === name);
    const currency = data.reportingCurrency || "GBP";
    const references = [...new Map(entries.map(row => [
      (row.currency || "GBP") + "|" + row.amount, { currency: row.currency || "GBP", amount: parseUnits(row.amount) },
    ])).values()];
    if(data.reportingMode==="converted"){
      if(references.length!==1)return {amount:null,currency,references,reason:references.length?"Conflicting source pool inputs; choose one workbook":"No source pool input"};
      const original=references[0],month=filters.month||defaultReportingMonth(data)||"";
      const rate=usdRate(data,original.currency,month),amount=usdMoney(original.amount,rate);
      return {amount,currency,references,conversion:rate,
        reason:amount===null?"No saved "+original.currency+"-to-USD pool rate for "+month
          :fxDescription(rate)+" Pool conversion uses the selected reporting month, not an issuance date or proof of credit drawdown."};
    }
    const amounts = [...new Set(entries.filter(row => (row.currency || "GBP") === currency).map(row => row.amount))];
    return { amount: amounts.length === 1 ? parseUnits(amounts[0]) : null,
      currency, references,
      reason: amounts.length > 1 ? "Conflicting source pool inputs; choose one workbook" : amounts.length ? null
        : entries.length ? "Source pool is in " + [...new Set(references.map(row => row.currency))].join(" / ") + "; no " + currency + " pool or explicit conversion supplied"
          : "No source pool input" };
  }

  function workbookModel(data, filters) {
    if (data.reportingEnrollment) {
      if (filters.enrollment && filters.enrollment !== data.reportingEnrollment) throw new Error("Enrollment does not match the reporting currency context");
      filters = { ...filters, enrollment: data.reportingEnrollment };
    }
    const scopes = sourceScopes(data, filters);
    const actualScopes=sourceScopes({...data,scopes:data.sourceScopesOriginal||data.scopes},filters);
    const asOf = filters.month || actualScopes.map(scope => scope.month).sort().at(-1);
    if (!asOf) throw new Error("No supplied reporting month for this source scope");
    const year = asOf.slice(0, 4);
    const intendedNames = data.sources.flatMap((source, index) => !filters.source || filters.source === String(index) ? (source.credit_inputs || []).map(row => row.enrollment) : []);
    const names = filters.enrollment ? [filters.enrollment] : [...new Set([...actualScopes.map(scope => scope.enrollment), ...intendedNames])].sort();
    const provided = new Set(scopes.map(scope => scope.month + "|" + scope.enrollment));
    const rows = filterSpend(data, { ...filters, month: "" }).filter(row => data.dimensions.month[row.ids[0]] <= asOf);
    const indexed = new Map();
    for (const row of rows) {
      const key = data.dimensions.month[row.ids[0]] + "|" + data.dimensions.enrollment[row.ids[1]];
      if (!indexed.has(key)) indexed.set(key, []);
      indexed.get(key).push(row);
    }
    const cell = (month, name) => {
      const selected = indexed.get(month + "|" + name) || [];
      const metrics=summarizeSpend(data,selected);
      return { month, enrollment: name, rows: selected, ...metrics,
        available: provided.has(month + "|" + name)&&!metrics.nativeMissing };
    };
    const months = monthRange(year + "-01", asOf);
    const annualFilters={...filters,month:asOf};
    if(data.annualData&&filters.source){
      const digest=data.sources[Number(filters.source)]?.source_sha256;
      const index=data.annualData.sources.findIndex(source=>source.source_sha256===digest);
      annualFilters.source=index<0?"not-available":String(index);
    }
    const annualModel=data.annualData?workbookModel(data.annualData,annualFilters):null;
    const historyData=annualModel?.data||data;
    const historyRows = annualModel?annualModel.historyRows.filter(row=>names.includes(historyData.dimensions.enrollment[row.ids[1]]))
      :rows.filter(row => data.dimensions.month[row.ids[0]].startsWith(year));
    const currentRows = rows.filter(row => data.dimensions.month[row.ids[0]] === asOf);
    const comparisonMonth = data.snapshot.retention
      ? scopes.map(scope => scope.month).filter(month => month < asOf).sort().at(-1) || addMonth(asOf, -1)
      : addMonth(asOf, -1);
    const accounts = names.map(name => {
      const current = cell(asOf, name), previous = cell(comparisonMonth, name);
      const history = annualModel?annualModel.accounts.find(account=>account.name===name)?.history
        :{ ...summarizeSpend(data, historyRows.filter(row => data.dimensions.enrollment[row.ids[1]] === name)),
          available: months.some(month => cell(month,name).available)&&!historyRows.some(row=>row.nativeMoneyMissing&&data.dimensions.enrollment[row.ids[1]]===name) };
      if(!history)throw new Error("Annual history does not contain the selected enrollment scope");
      const missingMonths = months.filter(month => !(annualModel?annualModel.cell(month,name):cell(month,name)).available);
      return { name, current, previous, history, missingMonths, pool: creditPool(data, {...filters,month:asOf}, name),
        change: current.available && previous.available && previous.total !== 0n ? current.total - previous.total : null };
    });
    const cells = months.flatMap(month => names.map(name => cell(month, name)));
    const earliest = scopes.map(scope => scope.month).filter(month => month.startsWith(year) && month <= asOf).sort()[0] || year + "-01";
    return { data, filters, asOf, year, names, accounts, months, cells, rows, historyRows,historyData,historyFilters:annualFilters, currentRows,
      annualCell:annualModel?.cell||cell,
      annualSources:historyData.sources.filter((_source,index)=>!annualFilters.source||annualFilters.source===String(index)),
      previousMonth: comparisonMonth, adjacentComparison: comparisonMonth === addMonth(asOf, -1),
      chartMonths: monthRange(earliest, asOf), cell,
      current: { ...summarizeSpend(data, currentRows), available: accounts.some(account => account.current.available) },
      history: { ...summarizeSpend(historyData, historyRows), available: accounts.some(account => account.history.available)
        &&!historyRows.some(row=>row.nativeMoneyMissing) },
      completeCurrentScope: accounts.every(account => account.current.available) };
  }

  function nativePeriod(data, filters, month, enrollment) {
    const original = filterSpend(data, { source: filters.source || "", month, enrollment });
    const selected = filterSpend(data, { ...filters, month, enrollment });
    const supplied = original.length > 0;
    const currencies = [...new Set(original.map(row => row.nativeCurrency))];
    const currency = currencies.length === 1 && /^[A-Z]{3}$/.test(currencies[0]) ? currencies[0] : null;
    let reason = null;
    if (!supplied) reason = "Source month not supplied";
    else if (!currency) reason = "Native currency is missing or mixed; no conversion or total was inferred";
    else if (selected.some(row => row.nativeCost === null)) reason = "Native source amounts are not available";
    return { month, enrollment, currency, supplied, reason,
      cost: reason ? null : selected.reduce((sum,row)=>sum+row.nativeCost,0n),
      sourceRows: selected.reduce((sum,row)=>sum+row.count,0) };
  }

  function nativeComparison(data, filters) {
    const model = workbookModel({...data,annualData:null}, filters);
    return {
      asOf: model.asOf, previousMonth: model.previousMonth, adjacentComparison: model.adjacentComparison,
      months: model.chartMonths,
      accounts: model.names.map(name => {
        const current = nativePeriod(data, filters, model.asOf, name);
        const previous = nativePeriod(data, filters, model.previousMonth, name);
        const comparable = current.cost !== null && previous.cost !== null && current.currency === previous.currency;
        return { name, current, previous, currency: current.currency || previous.currency,
          change: comparable ? current.cost - previous.cost : null,
          percentage: comparable && previous.cost !== 0n ? formatRatio(current.cost-previous.cost,previous.cost) : "n/a",
          series: model.chartMonths.map(month=>nativePeriod(data,filters,month,name)) };
      }),
    };
  }

  function nativeComparisonCsv(data, filters) {
    const model = nativeComparison(data,filters);
    return toCsv(["Enrollment","Reporting month","Native currency","Native cost","Comparison month","Prior native currency","Prior native cost","Native change","Period change percent","Source rows","Definition"],
      model.accounts.map(account=>[account.name,model.asOf,account.current.currency,
        account.current.cost===null?null:decimalString(account.current.cost),model.previousMonth,account.previous.currency,
        account.previous.cost===null?null:decimalString(account.previous.cost),account.change===null?null:decimalString(account.change),
        account.percentage,account.current.sourceRows,
        "Original native currency; no FX applied and no cross-currency sum. "+(account.current.reason||"")]),
      [3,6,7,9]);
  }

  function nativeComparisonText(data, filters, notes) {
    return [
      "AZURE MONTHLY BUSINESS REVIEW - NATIVE CURRENCIES",
      "Each enrollment is reported in its original native currency. No FX applied; no mixed-currency total.",
      "Scope: "+JSON.stringify(filters),"",
      ...data.sources.map(source=>"Source: "+source.source_name+" | SHA-256 "+source.source_sha256),
      "",nativeComparisonCsv(data,filters),"COMMENTARY AND NEXT ACTIONS",notes.trim()||"No commentary added.",
      "Combined USD is a separately selected conversion of native costs, credit pools and source estimates with its FX/date assumptions labelled.",
    ].join("\r\n")+"\r\n";
  }

  function enrollmentDetail(data, filters, enrollment) {
    if(!data.dimensions.enrollment.includes(enrollment))throw new Error("Unknown enrollment detail scope");
    const scoped={...filters,enrollment};
    const comparison=nativeComparison(data,scoped);
    const account=comparison.accounts[0];
    const project=period=>period.cost===null?[]:filterSpend(data,{...scoped,month:period.month}).map(row=>({
      ...row,cost:row.nativeCost,gross:null,tier:null,
    }));
    const currentRows=project(account.current),previousRows=project(account.previous);
    const nativeData={...data,calculationsAvailable:false};
    const currentMetrics={...summarizeSpend(nativeData,currentRows),available:account.current.cost!==null};
    const previousMetrics={...summarizeSpend(nativeData,previousRows),available:account.previous.cost!==null};
    currentMetrics.sourceRows=account.current.sourceRows;
    previousMetrics.sourceRows=account.previous.sourceRows;
    currentMetrics.subscriptions=new Set(filterSpend(data,{...scoped,month:account.current.month}).map(row=>row.ids[3])).size;
    previousMetrics.subscriptions=new Set(filterSpend(data,{...scoped,month:account.previous.month}).map(row=>row.ids[3])).size;
    const sourceIds=new Set(sourceScopes(data,scoped).filter(scope=>[comparison.asOf,comparison.previousMonth].includes(scope.month)).map(scope=>scope.source));
    const sources=data.sources.filter((_source,index)=>sourceIds.has(index));
    return {data,filters:scoped,enrollment,currency:account.currency,...comparison,account,
      currentRows,previousRows,currentMetrics,previousMetrics,sources};
  }

  function comparisonBreakdown(data, currentMetric, previousMetric, dimension, sort="cost", sameCurrency=true) {
    if(!["cost","increase","decrease","name"].includes(sort))throw new Error("Unsupported enrollment sort");
    const group=rows=>{
      if(DIMENSIONS.includes(dimension))return groupSpend(data,rows,dimension);
      if(!["publisher","charge_type"].includes(dimension))throw new Error("Unsupported enrollment grouping");
      const groups=new Map();
      for(const row of rows){
        const name=(dimension==="publisher"?row.publisher:row.chargeType)||MISSING;
        const value=groups.get(name)||{name,cost:0n,count:0};
        value.cost+=row.cost;value.count+=row.count;groups.set(name,value);
      }
      return [...groups.values()];
    };
    const current=new Map(group(currentMetric.rows).map(row=>[row.name,row]));
    const previous=new Map(group(previousMetric.rows).map(row=>[row.name,row]));
    const comparable=currentMetric.available&&previousMetric.available&&sameCurrency;
    const result=[...new Set([...current.keys(),...previous.keys()])].map(name=>{
      const now=currentMetric.available?(current.get(name)?.cost??0n):null;
      const before=previousMetric.available?(previous.get(name)?.cost??0n):null;
      const change=comparable?now-before:null;
      return {name,current:now,previous:before,change,
        percentage:change!==null&&before!==0n?formatRatio(change,before):"n/a",
        share:now!==null?formatRatio(now,currentMetric.total):"n/a",
        count:current.get(name)?.count??0,previousCount:previous.get(name)?.count??0};
    });
    return sortComparisonRows(result, sort);
  }

  function sortComparisonRows(rows, sort="cost") {
    if(!["cost","increase","decrease","name"].includes(sort))throw new Error("Unsupported comparison sort");
    const result=[...rows].sort((a,b)=>a.current===b.current?a.name.localeCompare(b.name):a.current===null?1:b.current===null?-1:a.current>b.current?-1:1);
    if(sort==="name")result.sort((a,b)=>a.name.localeCompare(b.name));
    if(sort==="increase"||sort==="decrease")result.sort((a,b)=>{
      if(a.change===null||b.change===null)return a.change===b.change?a.name.localeCompare(b.name):a.change===null?1:-1;
      if(a.change===b.change)return a.name.localeCompare(b.name);
      return (a.change>b.change?-1:1)*(sort==="decrease"?-1:1);
    });
    return result;
  }

  function enrollmentBreakdown(detail, dimension, sort="cost") {
    return comparisonBreakdown(detail.data,
      {...detail.currentMetrics,rows:detail.currentRows}, {...detail.previousMetrics,rows:detail.previousRows},
      dimension, sort, detail.account.current.currency===detail.account.previous.currency);
  }

  function compareDriverChange(a, b, rate=false) {
    const left=rate?a.change*b.previous:a.change, right=rate?b.change*a.previous:b.change;
    return left===right?0:left>right?-1:1;
  }

  function subscriptionChanges(model, sort="percentage-increase") {
    if(!["percentage-increase","percentage-decrease","increase","decrease","cost","name"].includes(sort)){
      throw new Error("Unsupported subscription comparison sort");
    }
    const rows=model.accounts.flatMap(account=>
      comparisonBreakdown(model.data,account.current,account.previous,"subscription").map(row=>({
        ...row,enrollment:account.name,
        percentage:row.change!==null&&row.previous>0n?formatRatio(row.change,row.previous):"n/a",
        status:row.change===null?"Source period unavailable":!row.previousCount?"New subscription name"
          :!row.count?"No current activity":row.previous===0n?"Zero comparison base"
            :row.previous<0n?"Negative comparison base":"Comparable",
      })));
    if(!sort.startsWith("percentage-"))return sortComparisonRows(rows,sort);
    return rows.sort((a,b)=>{
      const left=a.change!==null&&a.previous>0n,right=b.change!==null&&b.previous>0n;
      if(left!==right)return left?-1:1;
      const change=left?compareDriverChange(a,b,true)*(sort==="percentage-decrease"?-1:1):0;
      return change||a.name.localeCompare(b.name)||a.enrollment.localeCompare(b.enrollment);
    });
  }

  function driverModel(model) {
    const current={...model.current,rows:model.currentRows,available:model.completeCurrentScope};
    const previousRows=model.accounts.flatMap(account=>account.previous.rows);
    const previous={...summarizeSpend(model.data,previousRows),rows:previousRows,
      available:model.accounts.every(account=>account.previous.available)};
    const ranked=dimension=>{
      const rows=comparisonBreakdown(model.data,current,previous,dimension).map(row=>({
        ...row,percentage:row.change!==null&&row.previous>0n?formatRatio(row.change,row.previous):"n/a",
        amountRank:null,percentageRank:null,
        status:row.change===null?"Source period unavailable":row.previous===0n?"New / zero comparison base"
          :row.previous<0n?"Negative comparison base":row.current===0n?"No current activity":"Comparable",
      }));
      for(const rate of [false,true]){
        const sorted=rows.filter(row=>row.change!==null&&(!rate||row.previous>0n))
          .sort((a,b)=>compareDriverChange(a,b,rate)||a.name.localeCompare(b.name));
        let rank=0;
        sorted.forEach((row,index)=>{
          if(!index||compareDriverChange(row,sorted[index-1],rate)!==0)rank=index+1;
          row[rate?"percentageRank":"amountRank"]=rank;
        });
      }
      return rows;
    };
    const regions=ranked("region"),services=ranked("service");
    const movers=(rate,increase)=>services.filter(row=>row.change!==null&&(increase?row.change>0n:row.change<0n)&&(!rate||row.previous>0n))
      .sort((a,b)=>(increase?1:-1)*compareDriverChange(a,b,rate)||a.name.localeCompare(b.name)).slice(0,3);
    return {model,current,previous,regions,services,
      movers:{amountIncrease:movers(false,true),amountDecrease:movers(false,false),
        percentageIncrease:movers(true,true),percentageDecrease:movers(true,false)},
      definition:"Current minus comparison signed cost. Percent changes require a positive comparison base; new/zero/negative bases are unavailable, not forced to 0% or 100%. Rankings use exact changes or exact ratios, descending, with shared competition ranks for ties. Missing enrollment periods block comparison. Source date coverage does not certify export completeness.",
    };
  }

  function driverChartRows(rows, limit) {
    if(!Number.isInteger(limit)||limit<1)throw new Error("Invalid driver chart limit");
    const selected=rows.slice(0,limit);
    if(rows.length>limit){
      const rest=rows.slice(limit);
      const sum=field=>rest.every(row=>row[field]!==null)?rest.reduce((total,row)=>total+row[field],0n):null;
      selected.push({name:"Other ("+rest.length+" groups)",current:sum("current"),previous:sum("previous"),other:true});
    }
    return selected;
  }

  function enrollmentCsv(detail, dimension, sort="cost") {
    const amount=value=>value===null?null:decimalString(value);
    return toCsv(["Enrollment","Current native currency","Current month","Prior native currency","Prior month",
      "Current native cost","Prior native cost","Native change","Period change","Group dimension","Group",
      "Current source rows","Prior source rows","Definition"],
      enrollmentBreakdown(detail,dimension,sort).map(row=>[
        detail.enrollment,detail.account.current.currency,detail.asOf,detail.account.previous.currency,detail.previousMonth,
        amount(row.current),amount(row.previous),amount(row.change),row.percentage,dimension,row.name,row.count,row.previousCount,
        "Original signed source cost; no FX. Missing months are unavailable; absent groups in supplied months are zero.",
      ]),[5,6,7,11,12]);
  }

  function enrollmentRowsCsv(detail) {
    return toCsv([...DIMENSIONS.map(name=>LABELS[name]),"Native currency","Native cost","Source rows","Original charge type","Publisher","Source file"],
      detail.currentRows.map(row=>[
        ...row.ids.map((id,index)=>detail.data.dimensions[DIMENSIONS[index]][id]),
        row.nativeCurrency,decimalString(row.cost),row.count,row.chargeType,row.publisher,detail.data.sources[row.source].source_name,
      ]),[11,12]);
  }

  function enrollmentReportText(detail, dimension, notes, sort="cost") {
    const current=detail.account.current,previous=detail.account.previous;
    const metrics=detail.currentMetrics;
    const values=detail.currentMetrics.available;
    const groups=enrollmentBreakdown(detail,dimension,sort);
    return [
      "AZURE MONTHLY BUSINESS REVIEW - ENROLLMENT DETAIL",detail.enrollment,
      "Current: "+detail.asOf+" | "+formatMoney(current.cost,2,current.currency||"GBP"),
      "Comparison: "+detail.previousMonth+" | "+formatMoney(previous.cost,2,previous.currency||"GBP"),
      "Change: "+formatMoney(detail.account.change,2,detail.currency||"GBP")+" | "+detail.account.percentage,
      "Scope: "+JSON.stringify(detail.filters),
      "Benefit cost: "+formatMoney(values?metrics.benefit:null,2,detail.currency||"GBP"),
      "Unused benefit cost: "+formatMoney(values?metrics.unused:null,2,detail.currency||"GBP"),
      "Credit-eligible cost: "+formatMoney(values&&!metrics.unknownEligibility?metrics.eligible:null,2,detail.currency||"GBP")+" (not credit consumption)",
      "Original source rows: "+metrics.sourceRows+"; subscriptions: "+metrics.subscriptions,
      "",...detail.sources.map(source=>"Source: "+source.source_name+" | SHA-256 "+source.source_sha256),
      "","DETAIL GROUPS: "+dimension+" ("+groups.length+" groups; first 20 in this briefing)",
      ...groups.slice(0,20).map(row=>row.name+" | "+formatMoney(row.current,2,current.currency||"GBP")+" | change "+formatMoney(row.change,2,detail.currency||"GBP")),
      "CSV exports include every group/current scoped source row, not just this briefing or table page.",
      "","COMMENTARY AND NEXT ACTIONS",notes.trim()||"No commentary added.",
      "","DEFINITIONS",
      "Native cost is the signed original source amount. No FX applied; no currencies combined.",
      "Period change is current minus the named comparison month's native cost; percentage divides by non-zero prior cost in the same currency.",
      "Group share divides signed group cost by all selected current cost; zero denominators are unavailable.",
      "Utilization is used benefit cost / (used + unused benefit cost), not hours, capacity or achieved savings.",
      "Credit eligibility is a source flag, not proof of drawdown. GBP estimates and credit pools remain separate.",
      "Source-month availability does not certify partition or invoice completeness; missing metrics remain unavailable.",
    ].join("\r\n")+"\r\n";
  }

  function assumptionMoney(raw, label) {
    const value = String(raw ?? "").trim();
    if (!value) return null;
    if (!/^-?\d+(?:\.\d{1,18})?$/.test(value)) throw new ScenarioInputError(label + ": enter decimal digits with at most 18 decimal places in the selected reporting currency");
    const result = decimalToUnits(value);
    if (result <= -(10n ** 38n) || result >= 10n ** 38n) throw new ScenarioInputError(label + ": amount exceeds reporting precision");
    return result;
  }

  function paceBasisPoints(raw) {
    const value = String(raw);
    if (!/^-?\d+(?:\.\d{1,2})?$/.test(value)) throw new ScenarioInputError("Daily pace change must be a percentage with at most two decimals");
    const result = decimalToUnits(value) / (10n ** 16n);
    if (result < -10000n || result > 100000n) throw new ScenarioInputError("Daily pace change must be between -100% and +1000%");
    return result;
  }

  function monthlyPaceProjection(amount, asOf, months, pace=0n) {
    return amount===null?null:months.map(month=>({month,
      amount:roundDivide(amount*BigInt(daysInMonth(month))*(10000n+pace),BigInt(daysInMonth(asOf))*10000n)}));
  }

  function forecastModel(model, assumptions = { change: "0", actuals: {} }) {
    const pace = paceBasisPoints(assumptions.change ?? "0");
    const futureMonths = monthRange(addMonth(model.asOf), model.year + "-12");
    const accounts = model.accounts.map(account => {
      const inputs = assumptions.actuals?.[account.name] || {};
      const extra = Object.fromEntries(["cost", "eligible", "savings"].map(name => [name, assumptionMoney(inputs[name], account.name + " earlier " + name)]));
      if (!account.missingMonths.length && Object.values(extra).some(value => value !== null)) throw new ScenarioInputError(account.name + ": no missing earlier months; do not add actuals twice");
      const current = account.current;
      const project = amount => !current.available ? null : monthlyPaceProjection(amount,model.asOf,futureMonths,pace);
      const projected = {
        cost: project(current.total), eligible: project(current.unknownEligibility ? null : current.eligible),
        savings: project(current.estimateMissing ? null : current.savings),
      };
      const knownSavingsProjection = project(current.savings);
      const knownRemainingSavings = knownSavingsProjection === null ? null : knownSavingsProjection.reduce((sum, item) => sum + item.amount, 0n);
      const remaining = Object.fromEntries(Object.entries(projected).map(([name, values]) => [name, values === null ? null : values.reduce((sum, item) => sum + item.amount, 0n)]));
      const observed = { cost: account.history.total, eligible: account.history.unknownEligibility ? null : account.history.eligible, savings: account.history.savings };
      const covered = Object.fromEntries(["cost", "eligible", "savings"].map(name => [name,
        account.history.available && observed[name] !== null && remaining[name] !== null ? observed[name] + remaining[name] : null]));
      const full = Object.fromEntries(["cost", "eligible", "savings"].map(name => {
        const complete = !account.missingMonths.length || extra[name] !== null;
        const priced = name !== "savings" || !account.history.estimateMissing;
        return [name, complete && priced && observed[name] !== null && remaining[name] !== null
          ? observed[name] + remaining[name] + (extra[name] || 0n) : null];
      }));
      return { ...account, extra, projected, remaining, observed, covered, full, knownRemainingSavings };
    });
    const total = field => Object.fromEntries(["cost", "eligible", "savings"].map(name => [name, accounts.every(account => account[field][name] !== null)
      ? accounts.reduce((sum, account) => sum + account[field][name], 0n) : null]));
    return { accounts, pace, futureMonths, remaining: total("remaining"), covered: total("covered"), full: total("full"),
      knownRemainingSavings: accounts.every(account => account.knownRemainingSavings !== null)
        ? accounts.reduce((sum, account) => sum + account.knownRemainingSavings, 0n) : null };
  }

  const LOG_TIER_SERVICES=["Log Analytics","Azure Monitor"];

  function savingsRows(data, rows, definition) {
    return rows.filter(row=>{
      if(definition.service&&data.dimensions.service[row.ids[2]]!==definition.service)return false;
      if(definition.services&&!definition.services.includes(data.dimensions.service[row.ids[2]]))return false;
      const pricing=pricingModel(data.dimensions.pricing[row.ids[5]]);
      const covered=["Reservation","SavingsPlan"].includes(pricing);
      if(definition.tier)return !covered&&((row.tier!==null&&row.tier!==0n)||row.pricedRows>0||row.missingRows>0);
      return definition.pricing?pricing===definition.pricing:covered;
    });
  }

  function costUtilization(data, rows, available) {
    const metrics=summarizeSpend(data,rows),denominator=metrics.used+metrics.unused;
    const unclassified=rows.filter(row=>["Reservation","SavingsPlan"].includes(pricingModel(data.dimensions.pricing[row.ids[5]]))
      &&!["Used","Unused"].includes(data.dimensions.allocation[row.ids[7]]));
    const unknownCost=unclassified.reduce((total,row)=>total+(row.cost<0n?-row.cost:row.cost),0n);
    const known=available&&denominator>0n&&unknownCost===0n;
    return {numerator:metrics.used,denominator,available:known,
      unclassifiedRows:unclassified.reduce((total,row)=>total+row.count,0),unknownCost,
      display:known?formatRatio(metrics.used,denominator):"n/a"};
  }

  function savingsTable(model) {
    const definitions = [
      { name: "Savings plan", pricing: "SavingsPlan", utilizationService: "Virtual Machines" },
      { name: "Reservation", pricing: "Reservation" },
      ...["Virtual Machines", "Microsoft Fabric", "SQL Database", "SQL Managed Instance", "Azure Database for PostgreSQL", "Redis Cache", "Foundry Models"].map(service => ({ name: service, service })),
      { name:"Log Analytics / Monitor tiers", services:LOG_TIER_SERVICES, tier:true },
      { name: "Commitment tiers", tier: true },
    ];
    return definitions.flatMap(definition => model.accounts.map(account => {
      const select = rows => savingsRows(model.data,rows,definition);
      const currentRows = select(account.current.rows), priorRows = select(account.previous.rows);
      const current = summarizeSpend(model.data, currentRows), previous = summarizeSpend(model.data, priorRows);
      const utilizationFor=(rows,available)=>costUtilization(model.data,
        definition.utilizationService?rows.filter(row=>model.data.dimensions.service[row.ids[2]]===definition.utilizationService):rows,available);
      const utilization=utilizationFor(currentRows,account.current.available),previousUtilization=utilizationFor(priorRows,account.previous.available);
      const deltaNumerator=utilization.available&&previousUtilization.available
        ?utilization.numerator*previousUtilization.denominator-previousUtilization.numerator*utilization.denominator:null;
      const deltaDenominator=utilization.denominator*previousUtilization.denominator;
      return { name: definition.name, enrollment: account.name, current, previous,
        available: account.current.available, previousAvailable: account.previous.available,
        utilization:utilization.display,previousUtilization:previousUtilization.display,
        utilizationCurrent:utilization,utilizationPrevious:previousUtilization,
        utilizationChange:deltaNumerator===null?"n/a":fixedInteger(roundDivide(deltaNumerator*1000n,deltaDenominator),1)+" pp",
        utilizationChangePp:deltaNumerator===null?null:roundDivide(deltaNumerator*100n*FACTOR,deltaDenominator),
        change: account.current.available && account.previous.available && !previous.estimateMissing && !current.estimateMissing && previous.savings !== null && current.savings !== null && previous.savings !== 0n
          ? formatRatio(current.savings - previous.savings, previous.savings) : "n/a" };
    }));
  }

  function savingsPresentation(model) {
    const months=monthRange(model.year+"-01",model.year+"-12"),future=months.filter(month=>month>model.asOf);
    const historyData=model.historyData||model.data;
    const scenario=forecastModel(model);
    const componentDefinitions=[
      {name:"Savings plan",pricing:"SavingsPlan"},{name:"Reservation",pricing:"Reservation"},
      {name:"Log Analytics / Monitor tiers",tier:true,services:LOG_TIER_SERVICES},
      {name:"Other explicit tiers",tier:true,other:true},
    ];
    const accounts=model.accounts.map(account=>{
      const components=componentDefinitions.map(definition=>{
        const select=(rows,data=model.data)=>savingsRows(data,
          definition.other?rows.filter(row=>!LOG_TIER_SERVICES.includes(data.dimensions.service[row.ids[2]])):rows,definition);
        const historyRows=select(model.historyRows.filter(row=>historyData.dimensions.enrollment[row.ids[1]]===account.name),historyData);
        const history=summarizeSpend(historyData,historyRows),current=summarizeSpend(model.data,select(account.current.rows));
        const projected=monthlyPaceProjection(account.current.available?current.savings:null,model.asOf,future);
        const remaining=projected===null?null:projected.reduce((sum,row)=>sum+row.amount,0n);
        const recorded=account.history.available?history.savings:null;
        const knownCovered=recorded!==null&&remaining!==null?recorded+remaining:null;
        return {name:definition.name,recorded,missing:history.estimateMissing,currentMissing:current.estimateMissing,
          knownCovered,fullYear:!account.missingMonths.length&&!history.estimateMissing&&!current.estimateMissing?knownCovered:null,
          active:historyRows.length>0,
          periods:months.map(month=>{
            const cell=model.annualCell(month,account.name),metrics=summarizeSpend(historyData,select(cell.rows,historyData));
            const available=month<=model.asOf&&cell.available;
            return {month,available,amount:available?metrics.savings:null,missing:available?metrics.estimateMissing:0};
          })};
      }).filter(component=>component.name!=="Other explicit tiers"||component.active);
      const forecast=scenario.accounts.find(row=>row.name===account.name);
      const recorded=account.history.available?account.history.savings:null;
      const knownCovered=recorded!==null&&forecast.knownRemainingSavings!==null?recorded+forecast.knownRemainingSavings:null;
      const total={name:"Total known estimate",total:true,recorded,missing:account.history.estimateMissing,
        currentMissing:account.current.estimateMissing,knownCovered,fullYear:forecast.full.savings,
        periods:months.map(month=>{
          const cell=model.annualCell(month,account.name),available=month<=model.asOf&&cell.available;
          return {month,available,amount:available?cell.savings:null,missing:available?cell.estimateMissing:0};
        })};
      return {...account,components,total,recorded,knownCovered,fullYear:forecast.full.savings};
    });
    return {model,months,accounts,comparison:savingsTable(model),
      definition:"Known source-price savings estimates, not realized savings. Savings-plan and reservation rows plus non-overlapping explicit tiers form the enrollment total. The Log Analytics / Monitor tier group includes explicit tier components from the source categories Log Analytics and Azure Monitor, not all Monitor usage; other tiers remain separate. Service comparison rows overlap model totals and must not be added again. Missing and future actual months are unavailable. An asterisk marks known partial estimates with unpriced inputs. Recorded-to-date and component shares use separate same-calendar-year history, never adding detailed months twice. Full-year baseline requires all earlier source months and complete pricing; known components plus future excludes named gaps. Utilization is cost-weighted, SavingsPlan utilization is VM-only, and its movement is percentage points, not a percent-relative change. Non-zero unclassified covered cost blocks utilization.",
    };
  }

  function savingsMatrixCsv(model) {
    const presentation=savingsPresentation(model),currency=model.data.reportingCurrency||"GBP";
    const amount=value=>value===null?null:decimalString(value);
    return toCsv(["Enrollment","Component","Currency",...presentation.months,"Recorded known estimate","Full-year baseline","Known components plus future excluding gaps","Share of recorded known total (%)","Unpriced historical source rows","Unpriced current source rows","As of","Missing earlier months","Definition","Scope","Sources"],
      presentation.accounts.flatMap(account=>[...account.components,account.total].map(row=>[
        account.name,row.name,currency,...row.periods.map(cell=>amount(cell.amount)),amount(row.recorded),amount(row.fullYear),amount(row.knownCovered),
        row.recorded!==null&&account.recorded!==null&&account.recorded!==0n?decimalString(roundDivide(row.recorded*100n*FACTOR,account.recorded)):null,
        row.missing,row.currentMissing,model.asOf,account.missingMonths.join("; "),presentation.definition+" "+currencyEvidence(model.historyData),JSON.stringify(model.filters),
        model.annualSources.map(source=>source.source_name).join(" | "),
      ])),Array.from({length:18},(_unused,index)=>index+3));
  }

  function advisorPotential(data, source, filters = {}) {
    const selected = data.advisorSnapshots.find(snapshot => snapshot.source === Number(source));
    if (!selected) throw new Error("Unknown Advisor snapshot");
    const rows = filterAdvisor(selected.rows, { ...filters, production: "yes" });
    const categories = [
      ["Virtual Machines", "virtual machine"], ["Database for PostgreSQL", "Database for PostgreSQL"],
      ["SQL PaaS DB", "SQL PaaS DB"], ["Cache for Redis", "Cache for Redis"], ["Microsoft Fabric", "Microsoft Fabric"],
    ];
    const names = filters.enrollment ? [filters.enrollment] : [...new Set(selected.rows.map(row => row.enrollment).filter(name => name !== MISSING))].sort();
    const groups = categories.flatMap(([label, key]) => names.map(name => {
      const matching = rows.filter(row => row.enrollment === name && row.service === key);
      return { service: label, enrollment: name, ...summarizeAdvisor(matching) };
    }));
    return { source: selected.source, groups, rows,
      total: groups.some(group => group.total !== null) ? groups.reduce((sum, group) => sum + (group.total || 0n), 0n) : null,
      missing: rows.filter(row => advisorAmount(row) === null).length };
  }

  function latestAdvisorSource(data) {
    return data.advisorSnapshots.reduce((latest, item) =>
      (data.sources[item.source].advisor_date_end || "") > (data.sources[latest.source].advisor_date_end || "") ? item : latest,
      data.advisorSnapshots[0]).source;
  }

  function potentialComparison(data, source, filters = {}) {
    const current = advisorPotential(data, source, filters), selected = data.sources[current.source];
    const prior = data.advisorSnapshots.filter(item => (data.sources[item.source].advisor_date_end || "") < (selected.advisor_date_end || ""))
      .sort((a, b) => (data.sources[b.source].advisor_date_end || "").localeCompare(data.sources[a.source].advisor_date_end || ""))[0];
    const previous = prior ? advisorPotential(data, String(prior.source), filters) : null;
    return { current, previous, priorSource: prior?.source ?? null, rows: current.groups.map(group => {
      const before = previous?.groups.find(row => row.service === group.service && row.enrollment === group.enrollment)?.total ?? null;
      return { ...group, before, change: group.total !== null && before !== null ? group.total - before : null };
    }) };
  }

  function summaryMeasures(model, advisorSource = latestAdvisorSource(model.data)) {
    const data = model.data, scenario = forecastModel(model);
    const potential = advisorPotential(data, String(advisorSource), {});
    const part = (metric, predicate) => metric.rows.filter(predicate).reduce((sum, row) => sum + row.cost, 0n);
    const change = (account, predicate) => {
      const previous = part(account.previous, predicate), current = part(account.current, predicate);
      return account.current.available && account.previous.available && previous !== 0n ? formatRatio(current - previous, previous) : "n/a";
    };
    const definitions = [
      ["Recorded cost", account => account.current.available ? account.current.total : null, account => account.change !== null ? formatRatio(account.change, account.previous.total) : "n/a"],
      ...["Azure", "Marketplace"].map(publisher => [publisher + " publisher", account => account.current.available ? part(account.current, row => row.publisher === publisher) : null,
        account => change(account, row => row.publisher === publisher)]),
      ...["Greenfield", "Brownfield"].map(name => [name, account => account.current.available ? part(account.current, row => data.dimensions.portfolio[row.ids[6]] === name) : null,
        account => change(account, row => data.dimensions.portfolio[row.ids[6]] === name)]),
      ["Monthly credit-eligible cost proxy", account => account.current.available && !account.current.unknownEligibility ? account.current.eligible : null, account => account.current.available && !account.current.unknownEligibility ? formatRatio(account.current.eligible, account.current.total) + " of cost" : "eligibility unavailable"],
      ["Recorded-to-date credit-eligible proxy", account => account.history.available && !account.history.unknownEligibility ? account.history.eligible : null,
        account => account.history.available && !account.history.unknownEligibility && account.pool.amount !== null && account.pool.amount > 0n ? formatRatio(account.history.eligible, account.pool.amount) + " of source pool" : "n/a"],
      ["Monthly known source-price savings estimate", account => account.current.available ? account.current.savings : null],
      ["Recorded-to-date known savings estimate", account => account.history.available ? account.history.savings : null],
      ["Remaining-year known-priced savings scenario", account => scenario.accounts.find(row => row.name === account.name).knownRemainingSavings],
      ["Annual Advisor potential (separate snapshot)", account => {
        const groups = potential.groups.filter(group => group.enrollment === account.name && group.total !== null);
        return groups.length ? groups.reduce((sum, group) => sum + group.total, 0n) : null;
      }],
    ];
    return definitions.map(([label, amount, context]) => ({
      label, cells: model.accounts.map(account => ({ enrollment: account.name, amount: amount(account), context: context ? context(account) : "\u2014" })),
    }));
  }

  function creditTimeline(model) {
    const totals = new Map(model.names.map(name => [name, { value: 0n, known: false, incomplete: false }]));
    return model.months.flatMap(month=>model.names.map(name=>model.annualCell(month,name))).map(cell => {
      const total = totals.get(cell.enrollment);
      if (cell.available) { total.value += cell.eligible; total.known = true; total.incomplete ||= cell.unknownEligibility > 0; }
      return { ...cell, cumulative: total.known && !total.incomplete ? total.value : null };
    });
  }

  function creditPresentation(model) {
    const months=monthRange(model.year+"-01",model.year+"-12");
    const timeline=new Map(creditTimeline(model).map(cell=>[cell.enrollment+"|"+cell.month,cell]));
    const scenario=forecastModel(model);
    const coverage=(amount,pool)=>amount!==null&&pool!==null&&pool>0n?roundDivide(amount*FACTOR,pool):null;
    const sum=values=>values.every(value=>value!==null)?values.reduce((total,value)=>total+value,0n):null;
    const accounts=model.accounts.map(account=>{
      const forecast=scenario.accounts.find(item=>item.name===account.name);
      const recorded=account.history.available&&!account.history.unknownEligibility?account.history.eligible:null;
      return {...account,recorded,recordedCoverage:coverage(recorded,account.pool.amount),
        covered:forecast.covered.eligible,fullYear:forecast.full.eligible,
        fullYearCoverage:coverage(forecast.full.eligible,account.pool.amount),
        timeline:months.map(month=>{
          const cell=timeline.get(account.name+"|"+month);
          const available=Boolean(cell?.available);
          const eligible=available&&!cell.unknownEligibility?cell.eligible:null;
          const cumulative=available?cell.cumulative:null;
          return {month,available,eligible,cumulative,coverage:coverage(cumulative,account.pool.amount),
            status:month>model.asOf?"Future; no actual supplied":!available?"Missing / expired source month"
              :cell.unknownEligibility?"Eligibility unavailable":"Supplied; completeness not certified"};
        })};
    });
    const totals=months.map((month,index)=>({month,
      eligible:sum(accounts.map(account=>account.timeline[index].eligible)),
      cumulative:sum(accounts.map(account=>account.timeline[index].cumulative)),
    }));
    return {model,months,accounts,totals,
      daysRemaining:months.map(month=>monthRange(addMonth(month),model.year+"-12").reduce((total,future)=>total+daysInMonth(future),0)),
      recorded:sum(accounts.map(account=>account.recorded)),covered:sum(accounts.map(account=>account.covered)),
      fullYear:sum(accounts.map(account=>account.fullYear)),
      definition:"Calendar-year credit-eligible signed cost proxy, not credits consumed. Missing, expired and future actual months are unavailable, never zero. Cumulative values include known supplied months only; unknown eligibility blocks the cumulative figure. Pool coverage requires a positive pool in the same currency. Remaining days are calendar days after each month end. Full-year baseline requires all earlier months; recorded plus future excludes named gaps and is not a complete annual forecast.",
    };
  }

  function creditMatrixRows(presentation) {
    const currency=presentation.model.data.reportingCurrency||"GBP";
    return [
      {label:"Days remaining after month end",kind:"days",unit:"days",values:presentation.daysRemaining},
      {label:"Monthly eligible cost - total",kind:"money",unit:currency,values:presentation.totals.map(cell=>cell.eligible)},
      ...presentation.accounts.map(account=>({label:"Monthly eligible cost - "+account.name,kind:"money",unit:currency,values:account.timeline.map(cell=>cell.eligible)})),
      {label:"Recorded cumulative proxy - total",kind:"money",unit:currency,values:presentation.totals.map(cell=>cell.cumulative)},
      ...presentation.accounts.map(account=>({label:"Recorded cumulative proxy - "+account.name,kind:"money",unit:currency,values:account.timeline.map(cell=>cell.cumulative)})),
      ...presentation.accounts.map(account=>({label:"Source pool coverage - "+account.name,kind:"coverage",unit:"percent",
        values:account.timeline.map(cell=>cell.coverage),numerators:account.timeline.map(cell=>cell.cumulative),denominator:account.pool.amount})),
    ];
  }

  function creditMatrixCsv(model) {
    const presentation=creditPresentation(model);
    return toCsv(["Measure","Unit",...presentation.months,"As of","Definition","Scope","Sources"],
      creditMatrixRows(presentation).map(row=>[row.label,row.unit,...row.values.map((value,index)=>value===null?null
        :row.kind==="days"?String(value):decimalString(row.kind==="coverage"?roundDivide(row.numerators[index]*100n*FACTOR,row.denominator):value)),
      model.asOf,presentation.definition+" "+currencyEvidence(model.historyData),JSON.stringify(model.filters),
      model.annualSources.map(source=>source.source_name).join(" | ")]),
      presentation.months.map((_month,index)=>index+2));
  }

  function groupSpend(data, rows, name) {
    const index = DIMENSIONS.indexOf(name);
    if (index < 0) throw new Error("Unknown grouping dimension");
    const groups = new Map();
    for (const row of rows) {
      const label = data.dimensions[name][row.ids[index]];
      const group = groups.get(label) || { name: label, cost: 0n, count: 0 };
      group.cost += row.cost;
      group.count += row.count;
      groups.set(label, group);
    }
    return [...groups.values()].sort((a, b) => a.cost === b.cost ? a.name.localeCompare(b.name) : a.cost > b.cost ? -1 : 1);
  }

  function filterAdvisor(rows, filters) {
    const query = (filters.query || "").trim().toLowerCase();
    return rows.filter(row => ["enrollment", "service", "impact", "production"].every(name => !filters[name] || filters[name] === row[name])
      && (!query || [row.recommendation, row.subscription, row.resource, row.subscription_id].join(" ").toLowerCase().includes(query)))
      .sort((a, b) => {
        const left = advisorAmount(a), right = advisorAmount(b);
        if (left === null || right === null) return left === right ? a.row - b.row : left === null ? 1 : -1;
        const x = parseUnits(left), y = parseUnits(right);
        return x === y ? a.row - b.row : x > y ? -1 : 1;
      });
  }

  function summarizeAdvisor(rows) {
    const valued = rows.filter(row => advisorAmount(row) !== null);
    if (new Set(valued.map(row => row.estimate_currency || "GBP")).size > 1) throw new Error("Advisor amounts in different currencies cannot be added");
    return {
      count: rows.length, valued: valued.length, missing: rows.length - valued.length,
      unmapped: rows.filter(row => row.enrollment === MISSING).length,
      total: valued.length ? valued.reduce((sum, row) => sum + parseUnits(advisorAmount(row)), 0n) : null,
    };
  }

  function csvCell(value, numeric = false) {
    if (value === null || value === undefined) return "";
    let result = String(value);
    if (numeric) {
      if (!/^-?\d+(?:\.\d+)?$/.test(result)) throw new Error("Invalid numeric CSV cell");
    } else if (typeof value === "string" && /^\s*[=+\-@\t\r]/.test(result)) result = "'" + result;
    return /[",\r\n]/.test(result) ? '"' + result.replace(/"/g, '""') + '"' : result;
  }

  function toCsv(headers, rows, numericColumns = []) {
    const numeric = new Set(numericColumns);
    return [headers.map(value => csvCell(value)).join(","), ...rows.map(row => {
      if (row.length !== headers.length) throw new Error("CSV row width does not match headers");
      return row.map((value, index) => csvCell(value, numeric.has(index))).join(",");
    })].join("\r\n") + "\r\n";
  }

  function spendCsv(data, rows) {
    const converted=data.reportingMode==="converted";
    const excluded=converted&&data.fxExcluded.length?" Dataset context: "+currencyEvidence(data):"";
    return toCsv([...DIMENSIONS.map(name => LABELS[name]), "Cost " + (data.reportingCurrency || "GBP"), "Source rows", "PublisherType", "Source workbook", "Original charge type", "Native currency", "Native cost",
      ...(converted?["USD FX rate","Saved FX month","Estimated FX","Conversion definition"]:[])], rows.map(row =>
      [...row.ids.map((id, index) => data.dimensions[DIMENSIONS[index]][id]), decimalString(row.cost), row.count, row.publisher, data.sources[row.source].source_name,
        row.chargeType,row.nativeCurrency,row.nativeCost===null?null:decimalString(row.nativeCost),
        ...(converted?[row.fx.rate,row.fx.rate_month,row.fx.estimated?"yes":"no",fxDescription(row.fx)+excluded]:[])]), [10, 11, 16,...(converted?[17]:[])]);
  }

  function advisorCsv(rows, sourceName = null, currency = "GBP") {
    const converted=rows.some(row=>Object.hasOwn(row,"estimate_note"));
    return toCsv(["Source sheet row", "Enrollment", "Service mapping", "Subscription", "Subscription ID", "Resource", "Recommendation", "Impact", "Production mapping", "Annual estimate " + currency, "Original annual estimate", "Original currency", "Updated date", ...(sourceName === null ? [] : ["Snapshot workbook"]),
      ...(converted?["USD estimate basis","USD FX rate","Saved FX month","USD conversion note"]:[])],
      rows.map(row => [row.row, row.enrollment, row.service, row.subscription, row.subscription_id, row.resource, row.recommendation, row.impact, row.production,
        advisorAmount(row) === null ? null : decimalString(parseUnits(advisorAmount(row))), row.native_annual, row.native_currency, row.updated, ...(sourceName === null ? [] : [sourceName]),
        ...(converted?[row.estimate_basis,row.estimate_fx?.rate??null,row.estimate_fx?.rate_month??null,row.estimate_note]:[])]), [0, 9, 10,...(converted?[sourceName===null?14:15]:[])]);
  }

  function driversCsv(model, options={}) {
    const drivers=driverModel(model),currency=model.data.reportingCurrency||"GBP";
    const dimensions=options.dimension==="region"?["region","service"]:["service","region"];
    const amount=value=>value===null?null:decimalString(value);
    return toCsv(["Dimension","Group","Comparison month","Current month","Currency","Comparison cost","Current cost","Signed change","Percent change (%)","Change rank","Percent rank","Comparison source rows","Current source rows","Status","Scope","Sources","Definition"],
      dimensions.flatMap(dimension=>sortComparisonRows(dimension==="region"?drivers.regions:drivers.services,options.sort||"cost").map(row=>[
        dimension,row.name,model.previousMonth,model.asOf,currency,amount(row.previous),amount(row.current),amount(row.change),
        row.change!==null&&row.previous>0n?decimalString(roundDivide(row.change*100n*FACTOR,row.previous)):null,row.amountRank,row.percentageRank,row.previousCount,row.count,row.status,
        JSON.stringify(model.filters),model.data.sources.filter((_source,index)=>!model.filters.source||model.filters.source===String(index)).map(source=>source.source_name).join(" | "),drivers.definition+" "+currencyEvidence(model.data),
      ])),[5,6,7,8,9,10,11,12]);
  }

  function subscriptionChangesCsv(model, sort="percentage-increase") {
    const amount=value=>value===null?null:decimalString(value);
    const sources=model.data.sources.filter((_source,index)=>!model.filters.source||model.filters.source===String(index))
      .map(source=>source.source_name).join(" | ");
    return toCsv(["Subscription name","Enrollment","Comparison month","Current month","Currency",
      "Comparison cost","Current cost","Signed change","Percent change (%)","Comparison source rows","Current source rows",
      "Status","Definition","Scope","Sources"],
      subscriptionChanges(model,sort).map(row=>[row.name,row.enrollment,model.previousMonth,model.asOf,
        model.data.reportingCurrency||"GBP",amount(row.previous),amount(row.current),amount(row.change),
        row.change!==null&&row.previous>0n?decimalString(roundDivide(row.change*100n*FACTOR,row.previous)):null,
        row.previousCount,row.count,row.status,SUBSCRIPTION_CHANGE_DEFINITION+" "+currencyEvidence(model.data),
        JSON.stringify(model.filters),sources]),[5,6,7,8,9,10]);
  }

  function workbookCsv(model, view, forecast = null, advisorSource = latestAdvisorSource(model.data), options={}) {
    if(view==="drivers")return driversCsv(model,options);
    if(view==="subscriptions")return subscriptionChangesCsv(model,options.sort);
    const amount = value => value === null ? null : decimalString(value);
    const currency = model.data.reportingCurrency || "GBP";
    const csv = (headers, rows, numeric) => toCsv(headers.map(label => label.replace(/\bGBP\b/g, currency)), rows, numeric);
    const provenance = (["summary","credits","forecast"].includes(view)?model.annualSources
      :model.data.sources.filter((_source, index) => !model.filters.source || model.filters.source === String(index))).map(source => source.source_name).join(" | ")
      + " | Filter scope: " + JSON.stringify(model.filters)+" | "+currencyEvidence(["summary","credits","forecast"].includes(view)?model.historyData:model.data);
    if (view === "forecast") {
      if (!forecast) throw new Error("Forecast assumptions must be valid before export");
      return csv(["Enrollment", "Calendar year", "As of", "Missing past months", "Measure", "Recorded GBP", "Earlier-month input GBP", "Remaining-year scenario GBP", "Recorded plus future excluding missing past GBP", "Full-year scenario GBP", "Known-priced-row savings scenario GBP", "Daily pace change percent", "Source workbooks"],
        forecast.accounts.flatMap(account => ["cost", "eligible", "savings"].map(name => [
          account.name, model.year, model.asOf, account.missingMonths.join("; "), name,
          amount(account.history.available ? account.observed[name] : null), amount(account.extra[name]),
          amount(account.remaining[name]), amount(account.covered[name]), amount(account.full[name]), name === "savings" ? amount(account.knownRemainingSavings) : null, fixedInteger(forecast.pace, 2, false), provenance,
        ])), [5, 6, 7, 8, 9, 10, 11]);
    }
    if (view === "savings") return csv(["Service or model", "Enrollment", "Month", "Gross source-price estimate GBP", "Tier estimate GBP", "Unused cost GBP", "Known net estimate GBP", "Unpriced rows", "Cost-weighted utilization", "Source workbooks", "Change vs prior supplied month","Comparison known net estimate GBP","Comparison cost-weighted utilization","Utilization change (percentage points)","Unclassified current allocation rows","Unclassified comparison allocation rows"],
      savingsTable(model).map(row => [row.name, row.enrollment, model.asOf,
        amount(row.available ? row.current.grossSavings : null), amount(row.available ? row.current.tierSavings : null),
        amount(row.available ? row.current.unused : null), amount(row.available ? row.current.savings : null),
        row.current.estimateMissing, row.available ? row.utilization : "n/a", provenance, row.change,
        amount(row.previousAvailable?row.previous.savings:null),row.previousUtilization,amount(row.utilizationChangePp),
        row.utilizationCurrent.unclassifiedRows,row.utilizationPrevious.unclassifiedRows]), [3, 4, 5, 6, 7,11,13,14,15]);
    if (view === "summary") return csv(["Measure", "Enrollment", "Month", "Value GBP", "Change or context", "Missing past months", "Source workbooks", "Advisor snapshot", "Advisor as of", "Comparison month", "Consecutive months"],
      summaryMeasures(model, advisorSource).flatMap(measure => measure.cells.map(cell => [
        measure.label, cell.enrollment, model.asOf, amount(cell.amount), cell.context,
        model.accounts.find(account => account.name === cell.enrollment).missingMonths.join("; "), provenance,
        model.data.sources[Number(advisorSource)].source_name, model.data.sources[Number(advisorSource)].advisor_date_end, model.previousMonth, model.adjacentComparison ? "yes" : "no",
      ])), [3]);
    if (view === "credits") return csv(["Month", "Enrollment", "Source period supplied", "Eligible cost GBP", "Recorded cumulative eligible cost GBP", "Source pool input GBP", "Recorded plus future eligible proxy at reporting month GBP", "Coverage", "Source workbooks", "Original pool currency", "Original pool amount", "Pool availability note"],
      creditTimeline(model).map(cell => [
        cell.month, cell.enrollment, cell.available ? "yes" : "no", amount(cell.available && !cell.unknownEligibility ? cell.eligible : null), amount(cell.cumulative),
        amount(model.accounts.find(account => account.name === cell.enrollment).pool.amount),
        cell.month === model.asOf ? amount(forecastModel(model).accounts.find(account => account.name === cell.enrollment).covered.eligible) : null,
        cell.available ? "Supplied; completeness not certified" : "Missing month; excluded, not zero", provenance,
        model.accounts.find(account => account.name === cell.enrollment).pool.references.map(row => row.currency).join(" | "),
        model.accounts.find(account => account.name === cell.enrollment).pool.references.map(row => decimalString(row.amount)).join(" | "),
        model.accounts.find(account => account.name === cell.enrollment).pool.reason,
      ]), [3, 4, 5, 6]);
    const months = view === "trend" ? model.chartMonths : model.months;
    return csv(["Month", "Enrollment", "Source period supplied", "Recorded cost GBP", "Credit-eligible cost GBP", "Known net savings estimate GBP", "Unpriced rows", "Source workbooks"],
      months.flatMap(month => model.names.map(name => {
        const cell = model.cell(month, name);
        return [month, name, cell.available ? "yes" : "no", amount(cell.available ? cell.total : null),
          amount(cell.available && !cell.unknownEligibility ? cell.eligible : null), amount(cell.available ? cell.savings : null), cell.estimateMissing, provenance];
      })), [3, 4, 5, 6]);
  }

  function workbookReportText(model, view, notes, forecast = null, advisorSource = latestAdvisorSource(model.data), options={}) {
    const sources = [...new Map([...model.data.sources.filter((_source, index) => !model.filters.source || model.filters.source === String(index)),
      ...model.annualSources].map(source=>[source.source_sha256,source])).values()];
    const lines = [
      "AZURE MONTHLY BUSINESS REVIEW - " + view.toUpperCase(), "Reporting month: " + model.asOf,
      "Basis: " + model.data.snapshot.meta.basis + "; reporting currency: " + (model.data.reportingCurrency || "GBP")
        + (model.data.reportingMode === "native" ? "; native source amounts, no FX" : "; converted original native amounts, estimated USD rates remain labelled"),
      "Scope: " + Object.entries(model.filters).filter(([, value]) => value).map(([name, value]) => (LABELS[name] || name) + ": " + value).join("; "),
      ...sources.map(source => "Source: " + source.source_name + " | SHA-256 " + source.source_sha256),
      "", "RECORDS AND ASSUMPTIONS",
      "Credit eligibility is not credit consumption. Savings are estimates against source prices, not certified realized savings.",
      "Supplied billing months are assumed complete only for a run-rate scenario; missing months are not zero.",
      "Detailed comparison months: "+model.data.dimensions.month.join(", ")+"; annual history applies only to YTD/matrices/scenarios, not the detail comparison.",
      ...model.accounts.map(account => account.name + ": missing earlier months " + (account.missingMonths.join(", ") || "none")),
      "", "VIEW DATA (CSV)", workbookCsv(model, view, forecast, advisorSource, options).trim(),
      ...(view==="credits"?["","TWELVE-MONTH CREDIT-ELIGIBILITY MATRIX (CSV)",creditMatrixCsv(model).trim()]:[]), "",
      ...(view==="savings"?["TWELVE-MONTH KNOWN-SAVINGS MATRIX (CSV)",savingsMatrixCsv(model).trim(),""]:[]),
      "COMMENTARY AND NEXT ACTIONS", notes.trim() || "No commentary added.", "", "DEFINITIONS",
      ...DEFINITIONS.map(([label, definition]) => label + ": " + definition),
    ];
    return lines.join("\r\n") + "\r\n";
  }

  function reportText(snapshot, view, filters, metrics, groups, notes, currency = "GBP") {
    const money = value => formatMoney(value, 2, currency);
    const scope = Object.entries(filters).filter(([, value]) => value).map(([name, value]) => (LABELS[name] || name) + ": " + value);
    const lines = [
      "AZURE MONTHLY BUSINESS REVIEW", view === "advisor" ? "Advisor annual estimates" : view === "quality" ? "Source quality and definitions" : "Recorded spend",
      "", "Scope: " + (scope.join("; ") || "All available source records"),
      "Source: " + snapshot.meta.source_name, "Extracted: " + snapshot.meta.extracted_utc,
      "Cost basis: " + snapshot.meta.basis + " (workbook label)", "Reporting currency: " + currency,
      (snapshot.meta.sources?.length > 1 ? "Source set SHA-256: " : "Source SHA-256: ") + snapshot.meta.source_sha256, "",
    ];
    if(view==="quality"&&snapshot.annual_history){
      const history=snapshot.annual_history;
      lines.push("Detailed comparison months: "+snapshot.dimensions.month.join(", "),
        "Separate annual history months: "+history.dimensions.month.join(", "),
        "Annual grouped source records: "+history.meta.source_rows+"; includes comparison scopes once, do not add these counts together.",
        ...history.meta.sources.map(source=>"Annual source: "+source.source_name+" | SHA-256 "+source.source_sha256+" | "+source.currency_label),"");
    }
    if(view==="quality"&&snapshot.usd_fx)lines.push("COMBINED USD FX EVIDENCE",
      ...snapshot.usd_fx.rows.map(fxDescription),"");
    if (view === "advisor") {
      lines.push("Recommendations: " + metrics.count, "With " + currency + " estimates: " + metrics.valued,
        "Missing " + currency + " estimates: " + metrics.missing, "Unmapped enrollment: " + metrics.unmapped,
        "Sum of listed annual " + currency + " estimates: " + money(metrics.total),
        "Estimates may overlap; this is not realized savings and is not additive to monthly spend.",
        "Advisor dates and filters are independent of spend.", "");
      for (const row of groups.slice(0, 15)) lines.push(
        row.enrollment + " | " + row.recommendation + " | "
        + (advisorAmount(row) === null ? currency + " estimate unavailable" : money(parseUnits(advisorAmount(row))) + " / year"));
    } else if (view === "spend") {
      lines.push("Billing months: " + (metrics.months.join(", ") || "No populated rows"),
        "Enrollments: " + (metrics.enrollments.join(", ") || "No populated rows"),
        "Recorded spend (" + currency + "): " + money(metrics.available ? metrics.total : null),
        "Reservation and savings-plan cost (" + currency + "): " + money(metrics.available ? metrics.benefit : null),
        "Unused benefit cost (" + currency + "): " + money(metrics.available ? metrics.unused : null),
        "Credit-eligible spend (" + currency + "): " + money(metrics.available && !metrics.unknownEligibility ? metrics.eligible : null),
        "Credit eligibility is not credit consumption.",
        "Source rows: " + metrics.sourceRows + "; subscriptions: " + metrics.subscriptions, "", "Top services:");
      for (const group of groups.slice(0, 10)) lines.push(group.name + ": " + money(group.cost));
    }
    lines.push("", "COMMENTARY AND NEXT ACTIONS", notes.trim() || "No commentary added.", "", "SOURCE LIMITATIONS");
    const warnings=[...new Map([...snapshot.audit.warnings,...(view==="quality"?snapshot.annual_history?.audit.warnings||[]:[])]
      .map(warning=>[JSON.stringify([warning.code,warning.source_name,warning.detail]),warning])).values()];
    for (const warning of warnings) lines.push("- " + warning.title + ": " + warning.detail);
    lines.push("", "DEFINITIONS");
    for (const [label, definition] of DEFINITIONS) lines.push(label + ": " + definition);
    return lines.join("\r\n") + "\r\n";
  }

  const core = { parseUnits, roundDivide, decimalToUnits, formatMoney, formatCompact, formatRatio, decimalString, prepare, reportingData,combinedUsd,usdRate,usdMoney,fxDescription,currencyEvidence,defaultEnrollment,enrollmentColor,
    advisorAmount,filterSpend, summarizeSpend, groupSpend, filterAdvisor, summarizeAdvisor, csvCell, toCsv, spendCsv, advisorCsv, reportText,
    comparisonBreakdown,sortComparisonRows,subscriptionChanges,subscriptionChangesCsv,driverModel,driverChartRows,driversCsv,creditPresentation,creditMatrixRows,creditMatrixCsv,
    addMonth, monthRange, daysInMonth, defaultReportingMonth, workbookModel, nativePeriod, nativeComparison, nativeComparisonCsv, nativeComparisonText,
    enrollmentDetail,enrollmentBreakdown,enrollmentCsv,enrollmentRowsCsv,enrollmentReportText,forecastModel,monthlyPaceProjection,
    savingsRows,costUtilization,savingsTable,savingsPresentation,savingsMatrixCsv,
    advisorPotential, potentialComparison, summaryMeasures, creditTimeline, workbookCsv, workbookReportText, assumptionMoney, paceBasisPoints };
  if (typeof module !== "undefined" && module.exports) module.exports = core;
  if (typeof document === "undefined") return;

  const $ = id => document.getElementById(id);
  const escape = value => String(value).replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  const count = value => value.toLocaleString("en-GB");
  const monthLabel = value => {
    const [year, month] = value.split("-");
    return ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(month) - 1] + " " + year;
  };
  const info = definition => '<span class="info" tabindex="0" title="' + escape(definition) + '" aria-label="' + escape(definition) + '">i</span>';
  const state = { view: "enrollment", spend: {}, advisor: {}, spendPage: 0, advisorPage: 0, detailPage:0, detailEnrollment:"", rows: [], recommendations: [],
    advisorSource: "0", reportScope: "", scenarios: new Map(), forecast: null, drivers:null, driversPage:0, driversPublisher:"Azure",
    subscriptions: null, subscriptionsPage: 0, uploadPreview: null, uploading: false };
  const PAGE_SIZE = 25;

  function fatal(error) {
    $("fatal-error").hidden = false;
    $("fatal-error").textContent = "The report could not complete: " + error.message;
    console.error(error);
  }

  try {
    const appearanceThemes = {
      "clean-light": "light", "dark-charcoal": "dark", "compact-workbook": "light", "executive-light": "light",
    };
    function appearanceStorage(value) {
      try {
        if (value === undefined) return localStorage.getItem("azfinops-mbr-appearance");
        localStorage.setItem("azfinops-mbr-appearance", value);
        $("appearance-status").hidden = true;
        return value;
      } catch (error) {
        if (!(error instanceof DOMException) || !["SecurityError", "QuotaExceededError"].includes(error.name)) throw error;
        console.warn("Appearance preferences unavailable", error);
        $("appearance-status").textContent = "This browser blocks saved appearance preferences. Your choice applies to this tab only.";
        $("appearance-status").hidden = false;
        return null;
      }
    }
    function applyAppearance(value) {
      if (!Object.hasOwn(appearanceThemes, value)) throw new Error("Unknown dashboard appearance");
      document.documentElement.dataset.appearance = value;
      document.documentElement.dataset.theme = appearanceThemes[value];
      $("appearance-select").value = value;
    }
    const savedAppearance = appearanceStorage();
    const requestedTheme = new URLSearchParams(window.location.search).get("scoutTheme");
    if (savedAppearance && !Object.hasOwn(appearanceThemes, savedAppearance)) {
      console.warn("Unrecognized saved dashboard appearance", savedAppearance);
      $("appearance-status").textContent = "The saved appearance is not recognized. Clean light has been applied; choose another appearance to update your preference.";
      $("appearance-status").hidden = false;
    }
    applyAppearance(requestedTheme === "dark" ? "dark-charcoal" : requestedTheme === "light" ? "clean-light"
      : Object.hasOwn(appearanceThemes, savedAppearance) ? savedAppearance : "clean-light");
    $("appearance-select").addEventListener("change", () => {
      const value = $("appearance-select").value;
      applyAppearance(value);
      appearanceStorage(value);
    });
    const originalData = prepare(JSON.parse($("mbr-data").textContent));
    let data = originalData;
    const currency = () => data.reportingCurrency || "GBP";
    const formatMoney = (value, digits = 2, unit = currency()) => core.formatMoney(value, digits, unit);
    const formatCompact = (value, unit = currency()) => core.formatCompact(value, unit);
    const chartColors = ["var(--cp-accent)", "var(--cp-text-muted)", "var(--cp-text-soft)", "var(--cp-border-strong)"];
    const enrollmentColor = name => core.enrollmentColor(name, originalData.dimensions.enrollment);
    const seriesColor = (item, index) => enrollmentColor(item.enrollment || item.name)
      || chartColors[(item.colorIndex ?? index) % chartColors.length];
    const snapshot = data.snapshot;
    const host = JSON.parse($("report-host").textContent);
    const canUpload = host.available === true && window.location.origin === host.origin && window.location.hostname === "127.0.0.1";
    const lastMonth = data.dimensions.month[data.dimensions.month.length - 1];
    const initialMonth = defaultReportingMonth(data) || lastMonth;
    state.spend.month = initialMonth;
    state.detailEnrollment=defaultEnrollment(data);
    state.reportScope = state.detailEnrollment;
    state.spend.enrollment = state.detailEnrollment;
    state.advisor.enrollment = state.detailEnrollment;
    const enrollmentLabel=name=>{
      const currencies=[...new Set(data.rows.filter(row=>data.dimensions.enrollment[row.ids[1]]===name).map(row=>row.nativeCurrency))];
      return name+" | "+(currencies.length===1&&currencies[0]!==MISSING?currencies[0]:"native currency unavailable");
    };
    $("enrollment-links").innerHTML=data.dimensions.enrollment.map(name=>'<button data-view="enrollment" data-enrollment="'+escape(name)+'" aria-pressed="false">'+escape(enrollmentLabel(name)+" details")+"</button>").join("");
    $("detail-enrollment").innerHTML=data.dimensions.enrollment.map(name=>'<option value="'+escape(name)+'">'+escape(enrollmentLabel(name))+"</option>").join("");
    $("detail-enrollment").value=state.detailEnrollment;
    $("report-scope").innerHTML = data.dimensions.enrollment.map(name => '<option value="' + escape(name) + '">'
      + escape(enrollmentLabel(name)) + "</option>").join("") + '<option value="combined-usd">Combined USD ($; converted; saved FX)</option>';
    $("report-scope").value = state.reportScope;
    state.advisorSource = String(latestAdvisorSource(data));
    const filterMedia = window.matchMedia("(max-width:760px)");
    $("scope-options").open = !filterMedia.matches;
    filterMedia.addEventListener("change", () => { $("scope-options").open = !filterMedia.matches; });

    function options(element, values, allLabel) {
      element.innerHTML = '<option value="">' + escape(allLabel) + "</option>" + values.map(value =>
        '<option value="' + escape(value) + '">' + escape(element.id === "filter-month" ? monthLabel(value)
          : element.id === "filter-source" ? data.sources[Number(value)].source_name : value) + "</option>").join("");
    }
    document.querySelectorAll("[data-filter]").forEach(element => {
      const name = element.dataset.filter;
      if (name === "source") options(element, [...new Set(data.rows.map(row => String(row.source)))], "All retained cost sources");
      else if (name === "publisher") options(element, [...new Set(data.rows.map(row => row.publisher))].sort(), "All publishers");
      else if (name !== "query") {
        const values = name === "enrollment"
          ? [...new Set([...data.dimensions[name], ...snapshot.meta.missing_spend_enrollments])].sort()
          : data.dimensions[name];
        options(element, values, "All " + (name === "portfolio" ? "portfolios" : name === "pricing" ? "pricing models" : name === "month" ? "recorded months" : name + "s"));
      }
      element.value = state.spend[name] || "";
      element.addEventListener(name === "query" ? "input" : "change", () => {
        if(name==="publisher"&&state.view==="drivers")state.driversPublisher=element.value;
        else state.spend[name] = element.value;
        if (name === "source") {
          state.spend.month = defaultReportingMonth(data, state.spend) || lastMonth;
          $("filter-month").value = state.spend.month;
        }
        state.spendPage = 0;
        state.detailPage = 0;
        state.driversPage = 0;
        state.subscriptionsPage = 0;
        setView(state.view);
      });
    });
    $("advisor-source").innerHTML = data.advisorSnapshots.map(item => '<option value="' + item.source + '">'
      + escape(data.sources[item.source].source_name + " | " + (data.sources[item.source].advisor_date_end || "date not supplied")) + "</option>").join("");
    $("advisor-source").value = state.advisorSource;
    $("advisor-source").addEventListener("change", () => {
      state.advisorSource = $("advisor-source").value;
      state.advisorPage = 0;
      setView(state.view);
    });
    document.querySelectorAll("[data-advisor-filter]").forEach(element => {
      const name = element.dataset.advisorFilter;
      if (name !== "query") options(element, [...new Set(data.advisorSnapshots.flatMap(item => item.rows.map(row => row[name])))].sort(), "All " + (name === "production" ? "production mappings" : name + "s"));
      element.value = state.advisor[name] || "";
      element.addEventListener(name === "query" ? "input" : "change", () => {
        state.advisor[name] = element.value;
        state.advisorPage = 0;
        setView(state.view);
      });
    });

    function kpi(label, value, note, definition, full = "", hero = false) {
      return '<article class="card kpi' + (hero ? " hero" : "") + '"><div class="kpi-label">' + escape(label) + info(definition)
        + '</div><div class="kpi-value" title="' + escape(full || value) + '">' + escape(value) + '</div><p class="note">' + escape(note) + "</p></article>";
    }

    function moneyCell(value, note = "") {
      return '<td class="num">' + escape(formatMoney(value)) + (note ? '<span class="estimate-note">' + escape(note) + "</span>" : "") + "</td>";
    }

    function reportHeading(title, note) {
      $("page-eyebrow").textContent = "Workbook-style monthly business review";
      $("page-title").textContent = title;
      $("page-subtitle").textContent = note;
      scopeChips(activeSpendFilters());
    }

    function activeSpendFilters() {
      return state.view==="drivers"?{...state.spend,publisher:state.driversPublisher}:state.spend;
    }

    function workbookOptions() {
      return state.view==="drivers"?{dimension:$("drivers-dimension").value,sort:$("drivers-sort").value}
        :state.view==="subscriptions"?{sort:$("subscriptions-sort").value}:{};
    }

    function lineChart(id, months, series, label, unit = currency()) {
      const values = series.flatMap(item => item.values).filter(value => value !== null);
      if (!values.length) { $(id).innerHTML = '<p class="empty">No supplied values for this scope.</p>'; return; }
      let minimum = values.reduce((a, b) => a < b ? a : b, 0n);
      let maximum = values.reduce((a, b) => a > b ? a : b, 0n);
      if (minimum === maximum) maximum = minimum + FACTOR;
      const ratio = value => Number((value - minimum) * 1000000n / (maximum - minimum)) / 1000000;
      const x = index => months.length === 1 ? 480 : 80 + index * 800 / (months.length - 1);
      const y = value => 220 - ratio(value) * 195;
      let svg = '<svg class="line-chart" viewBox="0 0 920 270" role="img" aria-label="' + escape(label) + '"><title>' + escape(label) + "</title>";
      for (let index = 0; index <= 4; index += 1) {
        const value = minimum + (maximum - minimum) * BigInt(index) / 4n;
        svg += '<line x1="80" x2="880" y1="' + y(value) + '" y2="' + y(value) + '" stroke="var(--cp-border)" />'
          + '<text x="72" y="' + (y(value) + 4) + '" text-anchor="end" font-size="12" fill="var(--cp-text-muted)">' + escape(formatCompact(value,unit)) + "</text>";
      }
      months.forEach((month, index) => { svg += '<text x="' + x(index) + '" y="248" text-anchor="middle" font-size="12" fill="var(--cp-text-muted)">' + escape(monthLabel(month)) + "</text>"; });
      series.forEach((item, index) => {
        let drawing = false, path = "";
        item.values.forEach((value, point) => {
          if (value === null) { drawing = false; return; }
          path += (drawing ? " L " : " M ") + x(point) + " " + y(value);
          drawing = true;
        });
        const color = seriesColor(item, index);
        svg += '<path d="' + path + '" fill="none" stroke="' + color + '" stroke-width="2.5"' + (index % 2 || item.projected ? ' stroke-dasharray="6 5"' : "") + " />";
        item.values.forEach((value, point) => {
          if (value === null) return;
          const description = item.name + " " + monthLabel(months[point]) + ": " + formatMoney(value,2,unit);
          svg += '<circle cx="' + x(point) + '" cy="' + y(value) + '" r="4" fill="' + color + '" tabindex="0" role="img" aria-label="' + escape(description) + '"><title>' + escape(description) + "</title></circle>";
        });
      });
      $(id).innerHTML = svg + '</svg><div class="chart-legend">' + series.map((item, index) => '<span><span class="chart-key'
        + (index % 2 || item.projected ? " secondary" : "") + '" style="border-color:' + seriesColor(item, index) + '"></span>' + escape(item.name) + "</span>").join("") + "</div>";
    }

    function renderNative() {
      const model = nativeComparison(data,state.spend);
      reportHeading("Native-currency spend comparison",monthLabel(model.previousMonth)+" to "+monthLabel(model.asOf)
        +". Each enrollment remains in its source currency; no combined USD/GBP total.");
      $("page-eyebrow").textContent="Source currencies - no conversion";
      $("native-currency-heading").textContent=model.accounts.map(account=>account.name+": "+(account.currency||"currency unavailable")).join(" | ");
      $("native-kpis").innerHTML=model.accounts.map(account=>kpi(account.name+" | "+(account.currency||"currency unavailable"),
        formatCompact(account.current.cost,account.currency||"GBP"),
        account.current.reason||monthLabel(model.asOf)+" original native cost",
        "Signed original usage cost in this enrollment's native currency. No FX conversion and no sum across currencies.",
        formatMoney(account.current.cost,2,account.currency||"GBP"),true)).join("");
      $("native-detail-links").innerHTML=model.accounts.map(account=>'<button data-open-enrollment="'+escape(account.name)+'">Open '+escape(account.name)+" details</button>").join("");
      $("native-current-month").textContent=monthLabel(model.asOf)+" native cost";
      $("native-prior-month").textContent=monthLabel(model.previousMonth)+" native cost";
      $("native-table").innerHTML=model.accounts.map(account=>"<tr><td>"+escape(account.name)+"</td><td>"+escape(account.current.currency||account.previous.currency||"not supplied")
        +'</td><td class="num">'+escape(formatMoney(account.current.cost,2,account.current.currency||"GBP"))
        +'</td><td class="num">'+escape(formatMoney(account.previous.cost,2,account.previous.currency||"GBP"))
        +'</td><td class="num">'+escape(formatMoney(account.change,2,account.currency||"GBP"))
        +'</td><td class="num">'+escape(account.percentage)
        +'</td><td class="num">'+count(account.current.sourceRows)+"</td></tr>").join("");
      $("native-charts").innerHTML=model.accounts.map((account,index)=>'<article class="card"><h2>'+escape(account.name+" | "+(account.currency||"native currency unavailable"))
        +'</h2><p class="note">Original cost; no FX applied. Different currencies use separate charts.</p><div id="native-chart-'+index+'"></div></article>').join("");
      model.accounts.forEach((account,index)=>lineChart("native-chart-"+index,model.months,[{name:account.name,values:account.series.map(period=>
        period.currency===account.currency?period.cost:null)}],"Native "+(account.currency||"unavailable")+" cost for "+account.name,account.currency||"GBP"));
      $("result-announcement").textContent="Native currency comparison: "+model.accounts.map(account=>account.name+" "+(account.currency||"unavailable")).join("; ")+". No combined total.";
    }

    function currentEnrollmentDetail() {
      return enrollmentDetail(data,state.spend,state.detailEnrollment);
    }

    function renderEnrollment() {
      const detail=currentEnrollmentDetail(),metrics=detail.currentMetrics;
      const account=detail.account,currency=account.current.currency||detail.currency||"GBP";
      const value=amount=>metrics.available?amount:null;
      reportHeading(detail.enrollment+" | "+(detail.currency||"native currency unavailable")+" details",
        monthLabel(detail.previousMonth)+" to "+monthLabel(detail.asOf)+". One enrollment, original source currency, no FX conversion.");
      $("page-eyebrow").textContent="Enrollment workspace - native source currency";
      $("currency-badge").textContent=detail.enrollment+" | native "+(detail.currency||"currency unavailable");
      scopeChips(detail.filters);
      $("detail-enrollment").value=state.detailEnrollment;
      $("detail-kpis").innerHTML=
        kpi("Recorded cost | "+currency,formatCompact(account.current.cost,currency),account.current.reason||monthLabel(detail.asOf)+" original native cost",
          "Signed original cost for this enrollment and selected source month. No FX applied.",formatMoney(account.current.cost,2,currency),true)
        +kpi("Change vs "+monthLabel(detail.previousMonth),formatMoney(account.change,2,currency),account.percentage+" period change; same currency only",
          "Current native cost minus the comparison month's native cost. Missing periods and currency changes are unavailable.")
        +kpi("Reservation + savings-plan cost",formatCompact(value(metrics.benefit),currency),"Recorded cost, not achieved savings",
          DEFINITIONS[2][1],formatMoney(value(metrics.benefit),2,currency))
        +kpi("Unused benefit cost",formatMoney(value(metrics.unused),2,currency),"Original unused allocations included in cost",DEFINITIONS[3][1])
        +kpi("Credit-eligible cost",formatCompact(metrics.available&&!metrics.unknownEligibility?metrics.eligible:null,currency),
          metrics.unknownEligibility?count(metrics.unknownEligibility)+" rows lack eligibility":metrics.available?formatRatio(metrics.eligible,metrics.total)+" of selected cost; not drawdown":"not supplied",
          DEFINITIONS[4][1],formatMoney(metrics.available&&!metrics.unknownEligibility?metrics.eligible:null,2,currency))
        +kpi("Cost-weighted benefit utilization",metrics.available&&metrics.used+metrics.unused>0n?formatRatio(metrics.used,metrics.used+metrics.unused):"n/a",
          "Used / (used + unused) benefit cost; not hours",DEFINITIONS[10][1])
        +kpi("Subscriptions",count(metrics.subscriptions),"Selected current-month subscriptions","Distinct subscription names in the selected current source records.")
        +kpi("Original usage rows",count(metrics.sourceRows),monthLabel(detail.asOf)+" records represented by these groups","Original usage-row counts preserved through grouping; not the number of table groups.");
      $("detail-history-note").textContent="Original "+(detail.currency||"unavailable")+" cost. Missing periods break the line. "+(account.current.reason||"Source-month availability does not certify partition or invoice completeness.");
      lineChart("detail-history-chart",detail.months,[{name:detail.enrollment,values:account.series.map(period=>period.currency===detail.currency?period.cost:null)}],"Native history for "+detail.enrollment,detail.currency||"GBP");
      for(const [id,dimension] of [["service","service"],["pricing","pricing"],["region","region"],["publisher","publisher"],["portfolio","portfolio"],["charge","charge_type"]]){
        const groups=enrollmentBreakdown(detail,dimension).filter(row=>row.count>0&&row.current!==null).map(row=>({name:row.name,cost:row.current,count:row.count}));
        renderBars("detail-"+id+"-chart",groups,account.current.cost??0n,dimension==="charge_type"?null:dimension,currency,detail.enrollment);
      }
      renderEnrollmentTable(detail);
      $("detail-sources").innerHTML=detail.sources.map(source=>'<article><strong>'+escape(source.source_name)+"</strong>"
        +'<p class="note">'+escape(source.basis+"; "+(source.raw_usage?.native_currency||detail.currency||"currency unavailable")+" source. Extracted "+source.extracted_utc)+
        '</p><p class="mono note">'+escape(source.source_sha256)+"</p>"
        +(source.raw_usage?'<p class="note">File total before analytical filters: '+escape(formatMoney(decimalToUnits(source.raw_usage.native_total),2,source.raw_usage.native_currency))
          +"; "+count(source.raw_usage.raw_rows)+" original rows; "+count(source.raw_usage.power_query_groups)+" price-grain groups.</p>":"")
        +(source.independent_check?.status==="different"?'<p class="note">This supplied source differs from the main dataset; the report follows the supplied source. See Data & definitions.</p>':"")
        +"</article>").join("")||'<p class="note">No supplied source file for this enrollment and period scope.</p>';
      $("export-enrollment-data").disabled=detail.currentRows.length===0;
      $("result-announcement").textContent=detail.enrollment+" native "+(detail.currency||"unavailable")+" details; "+count(metrics.sourceRows)+" selected original rows.";
    }

    function renderEnrollmentTable(detail=currentEnrollmentDetail()) {
      const dimension=$("detail-dimension").value;
      const sort=$("detail-sort").value;
      const groups=enrollmentBreakdown(detail,dimension,sort);
      const pages=Math.ceil(groups.length/PAGE_SIZE);
      state.detailPage=Math.max(0,Math.min(state.detailPage,pages-1));
      const first=state.detailPage*PAGE_SIZE,selected=groups.slice(first,first+PAGE_SIZE);
      $("detail-group-label").textContent=dimension==="charge_type"?"Original charge type":dimension==="publisher"?"Publisher":LABELS[dimension];
      $("detail-current-label").textContent=monthLabel(detail.asOf)+" ("+(detail.account.current.currency||"currency unavailable")+")";
      $("detail-previous-label").textContent=monthLabel(detail.previousMonth)+" ("+(detail.account.previous.currency||"currency unavailable")+")";
      $("detail-table-caption").textContent=count(groups.length)+" groups. Change uses the named source periods; new/removed groups are not missing enrollment months. All amounts stay signed.";
      $("detail-table").innerHTML=selected.length?selected.map(row=>"<tr><td>"+escape(row.name)
        +'</td><td class="num">'+escape(formatMoney(row.current,2,detail.account.current.currency||"GBP"))
        +'</td><td class="num">'+escape(formatMoney(row.previous,2,detail.account.previous.currency||"GBP"))
        +'</td><td class="num">'+escape(formatMoney(row.change,2,detail.currency||"GBP"))
        +'</td><td class="num">'+escape(row.percentage)+'</td><td class="num">'+escape(row.share)
        +'</td><td class="num">'+count(row.count)+"</td></tr>").join("")
        :'<tr><td colspan="7" class="empty">No native detail groups match the enrollment and filters. Missing data is not zero.</td></tr>';
      $("detail-page-label").textContent=groups.length?count(first+1)+"\u2013"+count(first+selected.length)+" of "+count(groups.length)+" groups":"0 groups";
      $("detail-previous").disabled=state.detailPage===0;
      $("detail-next").disabled=state.detailPage+1>=pages;
      $("detail-print-note").textContent="Detail table page: "+$("detail-page-label").textContent+". Cards and charts cover the full selected scope. Export view CSV includes every group; scoped native data CSV includes every selected current reporting row.";
    }

    function renderSummary() {
      const model = workbookModel(data, state.spend);
      const current = model.current, scenario = forecastModel(model);
      reportHeading(monthLabel(model.asOf) + " | Executive summary", "Comparison: " + monthLabel(model.previousMonth) + " to " + monthLabel(model.asOf)
        + (model.adjacentComparison ? ". Consecutive billing months." : ". Not consecutive: period change, not month-on-month.") + " Missing enrollment months remain unavailable.");
      $("summary-kpis").innerHTML =
        kpi("Recorded monthly spend", formatCompact(current.available ? current.total : null), model.completeCurrentScope ? "Selected source periods; " + currency() : "Partial scope: an enrollment month is missing", DEFINITIONS[0][1], formatMoney(current.available ? current.total : null), true)
        + kpi("Recorded-to-date eligible cost", formatCompact(model.history.available && !model.history.unknownEligibility ? model.history.eligible : null), model.history.unknownEligibility ? "Eligibility missing on " + count(model.history.unknownEligibility) + " rows" : "Supplied calendar-year history; not credit drawdown", DEFINITIONS[8][1], formatMoney(model.history.available && !model.history.unknownEligibility ? model.history.eligible : null))
        + kpi("Known net savings estimate", formatCompact(current.available ? current.savings : null), current.estimateMissing ? count(current.estimateMissing) + " unpriced rows: partial estimate" : "Source-price baseline; not realized savings", DEFINITIONS[7][1], formatMoney(current.available ? current.savings : null))
        + kpi("Remaining-year spend scenario", formatCompact(scenario.remaining.cost), "Current daily pace; missing past months excluded", DEFINITIONS[9][1], formatMoney(scenario.remaining.cost));
      $("summary-caption").textContent = "Recomputed from retained source rows. Period change compares " + monthLabel(model.previousMonth) + " with " + monthLabel(model.asOf)
        + (model.adjacentComparison ? "." : "; gaps mean this is not MoM.") + " YTD uses separate supplied calendar-year history; monthly comparisons remain two-month only.";
      $("summary-head").innerHTML = "<tr><th>Measure</th>" + model.names.map(name => "<th class=\"num\">" + escape(name)
        + " (" + currency() + ")</th><th class=\"num\">Change vs " + escape(monthLabel(model.previousMonth)) + " / context " + info("Period change is (current minus named comparison month) / comparison cost, unavailable when that enrollment period is missing or zero. Nonconsecutive periods are not MoM. Credit eligibility is not verified credit drawdown.") + "</th>").join("") + "</tr>";
      $("summary-table").innerHTML = summaryMeasures(model, state.advisorSource).map(measure => "<tr><td>" + escape(measure.label) + "</td>"
        + measure.cells.map(cell => moneyCell(cell.amount) + '<td class="num">' + escape(cell.context) + "</td>").join("") + "</tr>").join("");
      $("summary-estimate-note").textContent = "Publisher splits correct the workbook's Usage Type misclassification. Price savings and their scenarios include only known components; " + count(current.estimateMissing)
        + " current-period rows are unpriced. Advisor values use production-only, five-category annual estimates from " + data.sources[Number(state.advisorSource)].source_name
        + " (updated " + (data.sources[Number(state.advisorSource)].advisor_date_end || "date unavailable") + "), not the spend month. None are verified realized savings.";
      $("summary-enrollments").innerHTML = model.accounts.map(account => '<article class="card"><h2>' + escape(account.name)
        + '</h2><p class="note" style="margin-top:8px">Missing earlier calendar-year months: ' + escape(account.missingMonths.map(monthLabel).join(", ") || "none")
        + "</p><p class=\"note\">Current period: " + (account.current.available ? "supplied; export completeness not certified" : "not supplied")
        + "</p><p class=\"note\">Source-price estimate inputs missing: " + count(account.current.estimateMissing) + "</p></article>").join("");
    }

    function renderTrend() {
      const model = workbookModel(data, state.spend);
      reportHeading("Recorded spend trend", "History through " + monthLabel(model.asOf) + "; missing months are gaps, not zero.");
      lineChart("trend-chart", model.chartMonths, model.names.map(name => ({ name, values: model.chartMonths.map(month => { const cell = model.cell(month, name); return cell.available ? cell.total : null; }) })), "Recorded monthly spend by enrollment");
      const aggregate = (month, predicate) => {
        const cells = model.names.map(name => model.cell(month, name));
        if (!cells.every(cell => cell.available)) return null;
        return cells.flatMap(cell => cell.rows).filter(predicate).reduce((sum, row) => sum + row.cost, 0n);
      };
      lineChart("usage-trend", model.chartMonths, ["Azure", "Marketplace"].map(name => ({ name, values: model.chartMonths.map(month => aggregate(month, row => row.publisher === name)) })), "Azure and Marketplace publisher cost by month");
      lineChart("portfolio-trend", model.chartMonths, ["Greenfield", "Brownfield"].map(name => ({ name, values: model.chartMonths.map(month => aggregate(month, row => data.dimensions.portfolio[row.ids[6]] === name)) })), "Greenfield and Brownfield cost by month");
      lineChart("pricing-trend", model.chartMonths, ["OnDemand", "Reservation", "SavingsPlan", "Spot"].map(name => ({ name, values: model.chartMonths.map(month => aggregate(month, row => pricingModel(data.dimensions.pricing[row.ids[5]]) === name)) })), "Recorded pricing-model cost by month");
      $("trend-table").innerHTML = model.chartMonths.flatMap(month => model.names.map(name => {
        const cell = model.cell(month, name);
        return "<tr><td>" + escape(monthLabel(month)) + "</td><td>" + escape(name) + "</td>" + moneyCell(cell.available ? cell.total : null)
          + moneyCell(cell.available && !cell.unknownEligibility ? cell.eligible : null) + "<td>" + (cell.available ? "Supplied; completeness not certified" : "Not supplied") + "</td></tr>";
      })).join("");
    }

    function driverColumnChart(id, groups, model, dimension) {
      const values=groups.flatMap(row=>[row.previous,row.current]).filter(value=>value!==null);
      if(!values.length){$(id).innerHTML='<p class="empty">No supplied values match this scope.</p>';return;}
      let minimum=values.reduce((a,b)=>a<b?a:b,0n),maximum=values.reduce((a,b)=>a>b?a:b,0n);
      if(minimum===maximum)maximum=minimum+FACTOR;
      const y=value=>235-Number((value-minimum)*1000000n/(maximum-minimum))/1000000*210;
      const step=800/groups.length,zero=y(0n);
      const color=enrollmentColor(model.filters.enrollment);
      const series=[{field:"previous",name:monthLabel(model.previousMonth),color:color||"var(--cp-border-strong)",opacity:color?".45":"1"},
        {field:"current",name:monthLabel(model.asOf),color:color||"var(--cp-text-muted)",opacity:"1"}];
      const title=LABELS[dimension]+" cost: "+monthLabel(model.previousMonth)+" and "+monthLabel(model.asOf)+" ("+currency()+")";
      let svg='<svg class="column-chart" viewBox="0 0 920 300" role="img" aria-label="'+escape(title)+'"><title>'+escape(title)+"</title>";
      for(let index=0;index<=4;index++){
        const value=minimum+(maximum-minimum)*BigInt(index)/4n;
        svg+='<line x1="80" x2="880" y1="'+y(value)+'" y2="'+y(value)+'" stroke="var(--cp-border)"/>'
          +'<text x="72" y="'+(y(value)+4)+'" text-anchor="end" font-size="11" fill="var(--cp-text-muted)">'+escape(formatCompact(value))+"</text>";
      }
      svg+='<line x1="80" x2="880" y1="'+zero+'" y2="'+zero+'" stroke="var(--cp-text-soft)"/>';
      groups.forEach((group,index)=>{
        const center=80+(index+.5)*step,width=Math.min(24,step*.3);
        series.forEach((item,seriesIndex)=>{
          const value=group[item.field];
          if(value===null)return;
          const description=group.name+" | "+item.name+": "+formatMoney(value);
          svg+='<rect x="'+(center+(seriesIndex-.5)*width-width/2)+'" y="'+Math.min(zero,y(value))+'" width="'+width
            +'" height="'+Math.max(1,Math.abs(zero-y(value)))+'" fill="'+item.color+'" fill-opacity="'+item.opacity+'" tabindex="0" role="img" aria-label="'+escape(description)+'"><title>'+escape(description)+"</title></rect>";
        });
        const label=group.name.length>19?group.name.slice(0,18)+"\u2026":group.name;
        svg+='<text x="'+center+'" y="256" text-anchor="middle" font-size="10" fill="var(--cp-text-muted)"><title>'+escape(group.name)+"</title>"+escape(label)+"</text>";
      });
      $(id).innerHTML='<div class="chart-scroll">'+svg+"</svg></div>"
        +'<div class="chart-legend">'+series.map(item=>'<span><span class="chart-key" style="border-color:'+item.color+';opacity:'+item.opacity+'"></span>'+escape(item.name)+"</span>").join("")+"</div>"
        +'<div class="table-wrap"><table class="chart-data"><caption class="sr-only">'+escape(title)+" - chart values</caption><thead><tr><th>Month</th>"
        +groups.map(group=>'<th scope="col">'+(group.other?escape(group.name):'<button class="bar-label" data-dimension="'+dimension+'" data-value="'+escape(group.name)+'">'+escape(group.name)+"</button>")+"</th>").join("")
        +"</tr></thead><tbody>"+series.map(item=>'<tr><th scope="row">'+escape(item.name)+"</th>"+groups.map(group=>'<td title="'+escape(formatMoney(group[item.field]))+'">'+escape(formatCompact(group[item.field]))+"</td>").join("")+"</tr>").join("")
        +"</tbody></table></div>";
    }

    function renderDriverPivot(id, rows, dimension, drivers) {
      const model=drivers.model;
      const percentage=drivers.current.available&&drivers.previous.available&&drivers.previous.total>0n
        ?formatRatio(drivers.current.total-drivers.previous.total,drivers.previous.total):"n/a";
      $(id).innerHTML='<table class="data-table pivot-table"><thead><tr><th scope="col">'+escape(LABELS[dimension])
        +'</th><th class="num" scope="col">'+escape(monthLabel(model.asOf)+" ("+currency()+")")
        +'</th><th class="num" scope="col">Change % '+info(drivers.definition)+"</th></tr></thead><tbody>"
        +rows.map(row=>'<tr><td><button class="bar-label" data-dimension="'+dimension+'" data-value="'+escape(row.name)+'">'+escape(row.name)+"</button></td>"
          +moneyCell(row.current)+'<td class="num" title="'+escape(row.status)+'">'+escape(row.percentage)+"</td></tr>").join("")
        +'</tbody><tfoot><tr><th scope="row">Grand total</th><th class="num">'+escape(formatMoney(drivers.current.available?drivers.current.total:null))
        +'</th><th class="num">'+escape(percentage)+"</th></tr></tfoot></table>";
    }

    function renderDrivers() {
      const model=workbookModel(data,activeSpendFilters()),drivers=driverModel(model);
      state.drivers=drivers;
      reportHeading("Cost drivers | tables & charts",monthLabel(model.previousMonth)+" to "+monthLabel(model.asOf)
        +(model.adjacentComparison?". Consecutive billing-month comparison.":". Period comparison, not month-on-month.")
        +" Pivots, charts and rankings use the same signed source costs.");
      $("drivers-scope-note").textContent=(model.filters.publisher==="Azure"?"Azure publisher only. Marketplace, including its purchases, is excluded."
        :model.filters.publisher==="Marketplace"?"Marketplace publisher only.":"All selected publishers are included.")
        +" Use Publisher type to change this scope. Region and service pivots reconcile to the selected total; chart Other includes every remaining group. Percent rankings exclude non-positive comparison bases.";
      for(const [dimension,rows,limit] of [["region",drivers.regions,5],["service",drivers.services,10]]){
        $("drivers-"+dimension+"-note").textContent=count(rows.length)+" comparison groups; "+monthLabel(model.asOf)+" cost and change vs "+monthLabel(model.previousMonth)+". Scroll for every row.";
        $("drivers-"+dimension+"-title").textContent=monthLabel(model.asOf)+" | "+(dimension==="region"?"Regional":"Service")+" cost ("+currency()+")";
        renderDriverPivot("drivers-"+dimension+"-pivot",rows,dimension,drivers);
        driverColumnChart("drivers-"+dimension+"-chart",driverChartRows(rows,limit),model,dimension);
      }
      $("drivers-movers").innerHTML=[
        ["Top decreasing services - "+currency(),"amountDecrease"],["Top decreasing services - %","percentageDecrease"],
        ["Top increasing services - "+currency(),"amountIncrease"],["Top increasing services - %","percentageIncrease"],
      ].map(([label,key])=>'<article class="card"><h2>'+escape(label)+info(drivers.definition)+'</h2><table class="data-table mover-table"><thead><tr><th>Service</th><th class="num">Change</th><th class="num">Change %</th></tr></thead><tbody>'
        +(drivers.movers[key].map(row=>'<tr><td><button class="bar-label" data-dimension="service" data-value="'+escape(row.name)+'">'+escape(row.name)
          +'</button></td><td class="num '+(row.change>0n?"change-positive":"change-negative")+'">'+(row.change>0n?"\u2191 ":"\u2193 ")+escape(formatMoney(row.change))
          +'</td><td class="num">'+escape(row.percentage)+"</td></tr>").join("")||'<tr><td colspan="3" class="empty">No comparable '+(key.endsWith("Increase")?"increases":"decreases")+" in this scope.</td></tr>")
        +"</tbody></table></article>").join("");
      renderDriversTable();
    }

    function renderDriversTable() {
      const drivers=state.drivers,options=workbookOptions(),model=drivers.model;
      const rows=sortComparisonRows(options.dimension==="region"?drivers.regions:drivers.services,options.sort);
      const pages=Math.max(1,Math.ceil(rows.length/PAGE_SIZE));
      state.driversPage=Math.max(0,Math.min(state.driversPage,pages-1));
      const first=state.driversPage*PAGE_SIZE,selected=rows.slice(first,first+PAGE_SIZE);
      $("drivers-detail-head").innerHTML='<tr><th>'+escape(LABELS[options.dimension])+'</th><th class="num">'+escape(monthLabel(model.previousMonth)+" ("+currency()+")")
        +'</th><th class="num">'+escape(monthLabel(model.asOf)+" ("+currency()+")")+'</th><th class="num">Signed change</th><th class="num">Change %</th><th class="num">Change rank'
        +info(drivers.definition)+'</th><th class="num">Rate rank</th><th>Status</th></tr>';
      $("drivers-detail-table").innerHTML=selected.length?selected.map(row=>'<tr><td><button class="bar-label" data-dimension="'+options.dimension+'" data-value="'+escape(row.name)+'">'+escape(row.name)+"</button></td>"
        +moneyCell(row.previous)+moneyCell(row.current)+moneyCell(row.change)+'<td class="num">'+escape(row.percentage)+'</td><td class="num">'+(row.amountRank??"n/a")
        +'</td><td class="num">'+(row.percentageRank??"n/a")+"</td><td>"+escape(row.status)+"</td></tr>").join("")
        :'<tr><td colspan="8" class="empty">No grouped activity matches this scope.</td></tr>';
      $("drivers-table-caption").textContent=drivers.definition+" Export view CSV includes all region and service groups, not only this page.";
      $("drivers-page-label").textContent=rows.length?count(first+1)+"\u2013"+count(first+selected.length)+" of "+count(rows.length)+" groups":"0 groups";
      $("drivers-previous").disabled=state.driversPage===0;
      $("drivers-next").disabled=state.driversPage+1>=pages;
      $("drivers-print-note").textContent="Detail comparison page: "+$("drivers-page-label").textContent+". Pivots include every group; charts include top groups plus Other; movers use the complete comparison. Export view CSV includes every service and region.";
      $("result-announcement").textContent=count(rows.length)+" cost driver comparison groups in "+currency()+".";
    }

    function renderSubscriptions() {
      state.subscriptions=workbookModel(data,state.spend);
      const model=state.subscriptions;
      reportHeading("Subscription changes",monthLabel(model.previousMonth)+" to "+monthLabel(model.asOf)
        +(model.adjacentComparison?". Month-on-month cost comparison.":". Period comparison, not month-on-month.")
        +" "+currency()+" amounts; each enrollment stays separate.");
      $("subscriptions-definition").textContent=SUBSCRIPTION_CHANGE_DEFINITION;
      $("subscriptions-comparison-label").textContent=monthLabel(model.previousMonth)+" ("+currency()+")";
      $("subscriptions-current-label").textContent=monthLabel(model.asOf)+" ("+currency()+")";
      $("subscriptions-change-label").textContent="Cost change ("+currency()+")";
      renderSubscriptionsTable();
    }

    function renderSubscriptionsTable() {
      const model=state.subscriptions,rows=subscriptionChanges(model,$("subscriptions-sort").value);
      const pages=Math.max(1,Math.ceil(rows.length/PAGE_SIZE));
      state.subscriptionsPage=Math.max(0,Math.min(state.subscriptionsPage,pages-1));
      const first=state.subscriptionsPage*PAGE_SIZE,selected=rows.slice(first,first+PAGE_SIZE);
      const percentages=rows.filter(row=>row.change!==null&&row.previous>0n).length;
      $("subscriptions-caption").textContent=count(rows.length)+" subscription/enrollment groups; "+count(percentages)
        +" have a comparable percentage. Use Subscription contains or the other report filters to narrow the list. CSV and text exports include every group in the selected order.";
      $("subscriptions-table").innerHTML=selected.length?selected.map(row=>"<tr><td>"+escape(row.name)+"</td><td>"+escape(row.enrollment)+"</td>"
        +moneyCell(row.previous)+moneyCell(row.current)+moneyCell(row.change)
        +'<td class="num">'+savingsMovement(row.percentage)+"</td><td>"+escape(row.status)+"</td></tr>").join("")
        :'<tr><td colspan="7" class="empty">No subscription activity matches the selected periods and filters.</td></tr>';
      $("subscriptions-page-label").textContent=rows.length?count(first+1)+"\u2013"+count(first+selected.length)+" of "+count(rows.length)+" groups":"0 groups";
      $("subscriptions-previous").disabled=state.subscriptionsPage===0;
      $("subscriptions-next").disabled=state.subscriptionsPage+1>=pages;
      $("subscriptions-print-note").textContent="Subscription comparison page: "+$("subscriptions-page-label").textContent
        +". Export view CSV or Download report includes every selected subscription/enrollment group; no annual-history rows are included.";
      $("result-announcement").textContent=count(rows.length)+" subscription comparison groups; "+count(percentages)+" comparable percentages.";
    }

    function renderCreditChart(presentation) {
      const amounts=presentation.totals.map(cell=>cell.cumulative).filter(value=>value!==null);
      if(!amounts.length){$("credits-chart").innerHTML='<p class="empty">No complete cumulative eligible-cost scope is supplied.</p>';return;}
      let minimum=amounts.reduce((a,b)=>a<b?a:b,0n),maximum=amounts.reduce((a,b)=>a>b?a:b,0n);
      if(minimum===maximum)maximum=minimum+FACTOR;
      const coverageValues=presentation.accounts.flatMap(account=>account.timeline.map(cell=>cell.coverage)).filter(value=>value!==null);
      const coverageMinimum=coverageValues.reduce((a,b)=>a<b?a:b,0n),coverageMaximum=coverageValues.reduce((a,b)=>a>b?a:b,FACTOR);
      const y=value=>250-Number((value-minimum)*1000000n/(maximum-minimum))/1000000*220;
      const coverageY=value=>250-Number((value-coverageMinimum)*1000000n/(coverageMaximum-coverageMinimum))/1000000*220;
      const x=index=>88+(index+.5)*64,zero=y(0n);
      const totalColor=presentation.accounts.length===1?enrollmentColor(presentation.accounts[0].name)||"var(--cp-text-soft)":"var(--cp-text-soft)";
      const title="Calendar "+presentation.model.year+" cumulative eligible cost ("+currency()+") and same-currency pool coverage";
      let svg='<svg class="column-chart credit-combo" viewBox="0 0 960 320" role="img" aria-label="'+escape(title)+'"><title>'+escape(title)+"</title>";
      for(let index=0;index<=4;index++){
        const value=minimum+(maximum-minimum)*BigInt(index)/4n,position=250-index*55;
        svg+='<line x1="88" x2="856" y1="'+position+'" y2="'+position+'" stroke="var(--cp-border)"/>'
          +'<text x="80" y="'+(position+4)+'" text-anchor="end" font-size="11" fill="var(--cp-text-muted)">'+escape(formatCompact(value))+"</text>";
        if(coverageValues.length){
          const ratio=coverageMinimum+(coverageMaximum-coverageMinimum)*BigInt(index)/4n;
          svg+='<text x="864" y="'+(position+4)+'" font-size="11" fill="var(--cp-text-muted)">'+escape(formatRatio(ratio,FACTOR,0))+"</text>";
        }
      }
      svg+='<line x1="88" x2="856" y1="'+zero+'" y2="'+zero+'" stroke="var(--cp-text-soft)"/>';
      presentation.totals.forEach((cell,index)=>{
        if(cell.cumulative!==null){
          const description=monthLabel(cell.month)+" recorded cumulative eligible cost: "+formatMoney(cell.cumulative);
          svg+='<rect x="'+(x(index)-13)+'" y="'+Math.min(zero,y(cell.cumulative))+'" width="26" height="'+Math.max(1,Math.abs(zero-y(cell.cumulative)))
            +'" fill="'+totalColor+'" tabindex="0" role="img" aria-label="'+escape(description)+'"><title>'+escape(description)+"</title></rect>";
        }
        svg+='<text x="'+x(index)+'" y="278" text-anchor="middle" font-size="11" fill="var(--cp-text-muted)">'+escape(monthLabel(cell.month).slice(0,3))+"</text>";
      });
      presentation.accounts.forEach((account,index)=>{
        let drawing=false,path="";
        account.timeline.forEach((cell,point)=>{
          if(cell.coverage===null){drawing=false;return;}
          path+=(drawing?" L ":" M ")+x(point)+" "+coverageY(cell.coverage);drawing=true;
        });
        const color=seriesColor(account,index);
        svg+='<path d="'+path+'" stroke="'+color+'" stroke-width="2.5" fill="none"'+(index%2?' stroke-dasharray="6 4"':"")+"/>";
        account.timeline.forEach((cell,point)=>{
          if(cell.coverage===null)return;
          const description=account.name+" "+monthLabel(cell.month)+": "+formatRatio(cell.cumulative,account.pool.amount)+" of the source pool (eligible-cost proxy)";
          svg+='<circle cx="'+x(point)+'" cy="'+coverageY(cell.coverage)+'" r="4" fill="'+color+'" tabindex="0" role="img" aria-label="'+escape(description)+'"><title>'+escape(description)+"</title></circle>";
        });
      });
      $("credits-chart").innerHTML='<div class="chart-scroll">'+svg+"</svg></div>"
        +'<div class="chart-legend"><span><span class="chart-key" style="border-color:'+totalColor+'"></span>Cumulative eligible cost ('+currency()+")</span>"
        +presentation.accounts.map((account,index)=>'<span><span class="chart-key" style="border-color:'+seriesColor(account,index)+'"></span>'+escape(account.name)
          +(account.pool.amount!==null&&account.pool.amount>0n?" pool coverage (%)":" coverage unavailable")+"</span>").join("")+"</div>"
        +(!coverageValues.length?'<p class="note">Percentage lines are unavailable: no positive same-currency source pool and complete eligibility are available. Original pool references remain below.</p>':"");
    }

    function renderCreditPresentation(model) {
      const presentation=creditPresentation(model);
      $("credits-matrix-note").textContent="Calendar "+model.year+" | "+currency()+". Missing/expired and future actual months are n/a, not zero. Hover shortened amounts for full values. Coverage is against a same-currency source pool, not proof of credits consumed.";
      $("credits-matrix-head").innerHTML="<tr><th>Measure / unit "+info(presentation.definition)+"</th>"
        +presentation.months.map(month=>'<th class="num" scope="col">'+escape(monthLabel(month))+"</th>").join("")+"</tr>";
      $("credits-matrix-table").innerHTML=creditMatrixRows(presentation).map(row=>'<tr'+(row.label.endsWith(" - total")?' class="matrix-total"':"")+'><th scope="row">'
        +escape(row.label+" ("+row.unit+")")+"</th>"+row.values.map((value,index)=>{
          const display=value===null?"n/a":row.kind==="days"?count(value):row.kind==="coverage"?formatRatio(row.numerators[index],row.denominator):formatCompact(value);
          const full=value===null?(presentation.months[index]>model.asOf?"Future: no actual supplied":"Unavailable source period, eligibility or same-currency pool")
            :row.kind==="money"?formatMoney(value):row.kind==="days"?value+" calendar days remaining after month end":display;
          return '<td class="num" title="'+escape(full)+'">'+escape(display)+"</td>";
        }).join("")+"</tr>").join("");
      renderCreditChart(presentation);
      $("credits-yearend").innerHTML=presentation.accounts.map(account=>{
        const coverage=account.recordedCoverage===null?"n/a":formatRatio(account.recorded,account.pool.amount);
        const fullCoverage=account.fullYearCoverage===null?"n/a":formatRatio(account.fullYear,account.pool.amount);
        return '<div class="explanation"><h3>'+escape(account.name+" | "+currency())+'</h3><p class="note">Known recorded eligible cost</p><p class="yearend-value" title="'+escape(formatMoney(account.recorded))+'">'
          +escape(formatCompact(account.recorded))+'</p><p class="note">Recorded pool coverage: '+escape(coverage)+'</p><p class="note" style="margin-top:12px">Full-year baseline</p><p class="yearend-value" title="'+escape(formatMoney(account.fullYear))+'">'
          +escape(formatCompact(account.fullYear))+'</p><p class="note">Full-year pool coverage: '+escape(fullCoverage)+'</p><p class="note" style="margin-top:12px">Recorded + future, missing past months excluded</p><p class="yearend-value" title="'+escape(formatMoney(account.covered))+'">'
          +escape(formatCompact(account.covered))+'</p><p class="note">Missing earlier months: '+escape(account.missingMonths.map(monthLabel).join(", ")||"none")+"</p></div>";
      }).join("");
      const remaining=monthRange(addMonth(model.asOf),model.year+"-12").reduce((total,month)=>total+daysInMonth(month),0);
      $("credits-baseline-note").textContent=remaining+" calendar days remain after "+monthLabel(model.asOf)+". Baseline: recorded eligible cost + selected-month daily pace \u00d7 remaining calendar days. Full-year totals remain unavailable when earlier source months are missing. This is a scenario, not a financial credit ledger.";
    }

    function renderCredits() {
      const model = workbookModel(data, state.spend), scenario = forecastModel(model);
      reportHeading("Credits | eligibility proxy", "Calendar " + model.year + " through " + monthLabel(model.asOf) + ". Missing historical months are excluded.");
      const pools = model.accounts.map(account => account.pool.amount);
      const totalPool = pools.every(value => value !== null) ? pools.reduce((sum, value) => sum + value, 0n) : null;
      $("credits-kpis").innerHTML =
        kpi("Recorded eligible cost to date", formatCompact(model.history.available && !model.history.unknownEligibility ? model.history.eligible : null), model.history.unknownEligibility ? "Eligibility values missing: not zero" : "Supplied calendar-year history; not certified credits consumed", DEFINITIONS[8][1], formatMoney(model.history.available && !model.history.unknownEligibility ? model.history.eligible : null), true)
        + kpi("Selected-month eligible cost", formatCompact(model.current.available && !model.current.unknownEligibility ? model.current.eligible : null), "Not proof of actual credit drawdown", DEFINITIONS[4][1], formatMoney(model.current.available && !model.current.unknownEligibility ? model.current.eligible : null))
        + kpi("Source pool in selected currency", formatCompact(totalPool), "Different-currency pools are separate references, not balances", DEFINITIONS[8][1], formatMoney(totalPool))
        + kpi("Recorded + future eligible proxy", formatCompact(scenario.covered.eligible), "Missing past months excluded; not a full-year forecast", DEFINITIONS[9][1], formatMoney(scenario.covered.eligible));
      const timeline = creditTimeline(model);
      $("credits-table").innerHTML = timeline.map(cell => {
        return "<tr><td>" + escape(monthLabel(cell.month)) + "</td><td>" + escape(cell.enrollment) + "</td>" + moneyCell(cell.available && !cell.unknownEligibility ? cell.eligible : null)
          + moneyCell(cell.cumulative)
          + "<td>" + (cell.available ? cell.unknownEligibility ? "Supplied; eligibility values missing" : "Recorded source month" : "Missing or expired month; excluded") + "</td></tr>";
      }).join("");
      renderCreditPresentation(model);
      $("credits-pools").innerHTML = model.accounts.map(account => {
        const pool = account.pool.amount, eligible = account.history.available && !account.history.unknownEligibility ? account.history.eligible : null;
        const ratio = pool !== null && pool > 0n && eligible !== null ? formatRatio(eligible, pool) : "n/a";
        const width = pool !== null && pool > 0n && eligible !== null ? Number((eligible < 0n ? 0n : eligible > pool ? pool : eligible) * 10000n / pool) / 100 : 0;
        return '<div class="explanation"><strong>' + escape(account.name) + "</strong><p>" + escape(ratio) + ' of source pool represented by recorded eligible cost</p><div class="progress" aria-hidden="true"><span style="width:' + width + '%"></span></div><p class="note">Pool: '
          + escape(formatMoney(pool)) + "; pool less recorded proxy: " + escape(formatMoney(pool !== null && eligible !== null ? pool - eligible : null))
          + ". Not a financial remaining-credit balance.</p><p class=\"note\">" + escape(account.pool.reason || "Missing prior months are not included in this proxy.")
          + "</p>" + account.pool.references.filter(row => row.currency !== currency()).map(row =>
            '<p class="note">Separate source reference only: ' + escape(row.currency + " " + formatMoney(row.amount, 2, row.currency))
            +(data.reportingMode==="converted"?". Converted to the USD pool above at the selected reporting month's saved rate; no cross-currency ratio.":". Not used in ratios or subtraction.")+"</p>").join("") + "</div>";
      }).join("");
    }

    function savingsText(amount, missing=0, compact=false) {
      return (compact?formatCompact(amount):formatMoney(amount))+(amount!==null&&missing?"*":"");
    }

    function savingsMovement(label) {
      if(label==="n/a")return escape(label);
      const zero=/^0(?:\.0+)?(?:%| pp)$/.test(label),negative=label.startsWith("-");
      return '<span class="'+(zero?"":negative?"change-negative":"change-positive")+'">'
        +(zero?"= ":negative?"\u2193 ":"\u2191 ")+escape(label)+"</span>";
    }

    function renderSavingsColumns(presentation) {
      const values=presentation.accounts.flatMap(account=>account.total.periods.map(cell=>cell.amount)).filter(value=>value!==null);
      if(!values.length){$("savings-chart").innerHTML='<p class="empty">No known savings components are supplied for this scope.</p>';return;}
      let minimum=values.reduce((a,b)=>a<b?a:b,0n),maximum=values.reduce((a,b)=>a>b?a:b,0n);
      if(minimum===maximum)maximum=minimum+FACTOR;
      const y=value=>250-Number((value-minimum)*1000000n/(maximum-minimum))/1000000*220;
      const x=index=>88+(index+.5)*64,zero=y(0n);
      const width=Math.min(26,44/presentation.accounts.length);
      const title=presentation.model.year+" monthly known source-price savings estimates ("+currency()+")";
      let svg='<svg class="column-chart credit-combo" viewBox="0 0 960 320" role="img" aria-label="'+escape(title)+'"><title>'+escape(title)+"</title>";
      for(let index=0;index<=4;index++){
        const value=minimum+(maximum-minimum)*BigInt(index)/4n,position=250-index*55;
        svg+='<line x1="88" x2="856" y1="'+position+'" y2="'+position+'" stroke="var(--cp-border)"/>'
          +'<text x="80" y="'+(position+4)+'" text-anchor="end" font-size="11" fill="var(--cp-text-muted)">'+escape(formatCompact(value))+"</text>";
      }
      svg+='<line x1="88" x2="856" y1="'+zero+'" y2="'+zero+'" stroke="var(--cp-text-soft)"/>';
      presentation.months.forEach((month,index)=>{
        svg+='<text x="'+x(index)+'" y="280" text-anchor="middle" font-size="11" fill="var(--cp-text-muted)">'+escape(monthLabel(month).slice(0,3))+"</text>";
      });
      presentation.accounts.forEach((account,series)=>{
        const color=seriesColor(account,series);
        account.total.periods.forEach((cell,point)=>{
          if(cell.amount===null)return;
          const center=x(point)+(series-(presentation.accounts.length-1)/2)*(width+3);
          const description=account.name+" "+monthLabel(cell.month)+": "+savingsText(cell.amount,cell.missing)
            +(cell.missing?" | known partial estimate; "+count(cell.missing)+" unpriced source rows":" | known source-price estimate");
          svg+='<rect x="'+(center-width/2)+'" y="'+Math.min(zero,y(cell.amount))+'" width="'+width+'" height="'+Math.max(1,Math.abs(zero-y(cell.amount)))
            +'" fill="'+color+'" stroke="'+color+'"'+(cell.missing?' fill-opacity=".65" stroke-dasharray="4 2"':"")
            +' data-partial="'+String(cell.missing>0)+'" tabindex="0" role="img" aria-label="'+escape(description)+'"><title>'+escape(description)+"</title></rect>";
          if(presentation.accounts.length===1)svg+='<text x="'+center+'" y="'+(y(cell.amount)+(cell.amount<0n?14:-7))+'" text-anchor="middle" font-size="10" fill="var(--cp-text)">'
            +escape(savingsText(cell.amount,cell.missing,true))+"</text>";
        });
      });
      $("savings-chart").innerHTML='<div class="chart-scroll">'+svg+"</svg></div>"
        +'<div class="chart-legend">'+presentation.accounts.map((account,index)=>'<span><span class="chart-key" style="border-color:'+seriesColor(account,index)+'"></span>'+escape(account.name+" ("+currency()+")")+"</span>").join("")
        +"<span>* Known partial estimate; not a full saving</span></div>"
        +'<div class="table-wrap"><table class="chart-data savings-chart-data"><caption class="sr-only">'+escape(title)+" - chart values</caption><thead><tr><th>Enrollment</th>"
        +presentation.months.map(month=>'<th scope="col">'+escape(monthLabel(month))+"</th>").join("")+"</tr></thead><tbody>"
        +presentation.accounts.map(account=>'<tr><th scope="row">'+escape(account.name)+"</th>"+account.total.periods.map(cell=>'<td title="'+escape(savingsText(cell.amount,cell.missing))+'">'
          +escape(savingsText(cell.amount,cell.missing,true))+"</td>").join("")+"</tr>").join("")+"</tbody></table></div>";
    }

    function renderSavingsPresentation(model) {
      const presentation=savingsPresentation(model);
      $("savings-comparison-note").textContent=monthLabel(model.asOf)+" vs "+monthLabel(model.previousMonth)
        +(model.adjacentComparison?". Consecutive billing months.":". Period comparison, not month-on-month.")
        +" * Known partial savings; changes require complete pricing. Utilization is cost-weighted; its movement is percentage points (pp), not hours or percent-relative change. Service rows overlap model totals.";
      $("savings-comparison-head").innerHTML='<tr><th rowspan="2" scope="col">Model / service '+info(presentation.definition)+"</th>"
        +presentation.accounts.map(account=>'<th class="enrollment-heading" colspan="4" scope="colgroup">'+escape(account.name+" | "+currency())+"</th>").join("")
        +"</tr><tr>"+presentation.accounts.map(()=>'<th class="num">Known savings</th><th class="num">Savings change %</th><th class="num">Cost-weighted utilization</th><th class="num">Util. change (pp)</th>').join("")+"</tr>";
      const indexed=new Map(presentation.comparison.map(cell=>[JSON.stringify([cell.name,cell.enrollment]),cell]));
      $("savings-comparison-table").innerHTML=[...new Set(presentation.comparison.map(cell=>cell.name))].map(name=>'<tr'+(["Savings plan","Reservation","Commitment tiers"].includes(name)?' class="model-total"':"")+'><th scope="row">'+escape(name)+"</th>"
        +presentation.accounts.map(account=>{
          const cell=indexed.get(JSON.stringify([name,account.name]));
          return '<td class="num" title="'+escape(savingsText(cell.available?cell.current.savings:null,cell.current.estimateMissing)
            +(cell.current.estimateMissing?" | "+count(cell.current.estimateMissing)+" source rows lack full savings inputs":" | known source-price estimate"))+'">'
            +escape(savingsText(cell.available?cell.current.savings:null,cell.current.estimateMissing,true))+'</td><td class="num">'+savingsMovement(cell.change)
            +'</td><td class="num" title="'+escape(count(cell.utilizationCurrent.unclassifiedRows)+" unclassified allocation rows; non-zero unclassified cost blocks this ratio")+'">'+escape(cell.utilization)
            +'</td><td class="num">'+savingsMovement(cell.utilizationChange)+"</td>";
        }).join("")+"</tr>").join("");
      $("savings-matrix-note").textContent="Calendar "+model.year+" | "+currency()+". Annual history supplies this matrix; top comparisons remain two-month only. SavingsPlan, Reservation and explicit tiers are non-overlapping. * Partial known estimate; missing/future actuals are n/a, not zero. Shares use the known net total.";
      $("savings-matrix-head").innerHTML='<tr><th scope="col">Enrollment / component</th>'+presentation.months.map(month=>'<th class="num" scope="col">'+escape(monthLabel(month))+"</th>").join("")
        +'<th class="num">Recorded known estimate</th><th class="num">Full-year baseline</th><th class="num">Share of known total</th></tr>';
      $("savings-matrix-table").innerHTML=presentation.accounts.flatMap(account=>[...account.components,account.total].map(row=>'<tr'+(row.total?' class="matrix-total"':"")+'><th scope="row">'
        +escape(account.name+" / "+row.name)+"</th>"+row.periods.map(cell=>'<td class="num" title="'+escape(cell.amount===null?"Unavailable source period or pricing":savingsText(cell.amount,cell.missing)+(cell.missing?" | "+count(cell.missing)+" unpriced rows":""))+'">'
          +escape(savingsText(cell.amount,cell.missing,true))+"</td>").join("")
        +'<td class="num" title="'+escape(savingsText(row.recorded,row.missing))+'">'+escape(savingsText(row.recorded,row.missing,true))
        +'</td><td class="num" title="'+escape(row.fullYear===null?"Requires complete earlier history and pricing":formatMoney(row.fullYear))+'">'+escape(formatCompact(row.fullYear))
        +'</td><td class="num">'+(row.recorded!==null&&account.recorded!==null&&account.recorded!==0n?formatRatio(row.recorded,account.recorded):"n/a")+"</td></tr>")).join("");
      $("savings-chart-title").textContent=model.year+" | Monthly known savings estimates ("+currency()+")";
      renderSavingsColumns(presentation);
      $("savings-yearend").innerHTML=presentation.accounts.map(account=>'<div class="explanation"><h3>'+escape(account.name+" | "+currency())
        +'</h3><p class="note">Recorded-to-date known estimate</p><p class="yearend-value" title="'+escape(savingsText(account.recorded,account.history.estimateMissing))+'">'
        +escape(savingsText(account.recorded,account.history.estimateMissing,true))+'</p><p class="note" style="margin-top:12px">Complete full-year baseline</p><p class="yearend-value" title="'+escape(formatMoney(account.fullYear))+'">'
        +escape(formatCompact(account.fullYear))+'</p><p class="note" style="margin-top:12px">Known components + future, missing past months excluded</p><p class="yearend-value" title="'+escape(savingsText(account.knownCovered,account.history.estimateMissing))+'">'
        +escape(savingsText(account.knownCovered,account.history.estimateMissing,true))+'</p><p class="note">Missing earlier months: '+escape(account.missingMonths.map(monthLabel).join(", ")||"none")
        +". Unpriced retained/current source rows: "+count(account.history.estimateMissing)+" / "+count(account.current.estimateMissing)+".</p></div>").join("");
      $("savings-baseline-note").textContent="Baseline: recorded known savings + selected-month daily pace for future calendar months. An incomplete history or any unpriced component blocks a complete full-year baseline. Known partial scenarios are separate and do not imply realized or guaranteed savings.";
    }

    function renderSavings() {
      const model = workbookModel(data, state.spend), current = model.current;
      reportHeading("Savings & utilization", "Source-price estimates and explicitly cost-weighted utilization, through " + monthLabel(model.asOf) + ".");
      $("savings-kpis").innerHTML =
        kpi("Known net source-price estimate", formatCompact(current.available ? current.savings : null), current.estimateMissing ? "Partial: " + count(current.estimateMissing) + " unpriced rows" : "Not certified realized savings", DEFINITIONS[7][1], formatMoney(current.available ? current.savings : null), true)
        + kpi("Used benefit gross estimate", formatCompact(current.available ? current.grossSavings : null), "Source baseline value less observed covered cost", DEFINITIONS[7][1], formatMoney(current.available ? current.grossSavings : null))
        + kpi("Unused benefit cost", formatMoney(current.available ? current.unused : null), "Subtracted in the same reporting currency: " + currency(), DEFINITIONS[3][1])
        + kpi("Commitment-tier estimate", formatCompact(current.available ? current.tierSavings : null), "Explicit source discounts only", DEFINITIONS[7][1], formatMoney(current.available ? current.tierSavings : null));
      renderSavingsPresentation(model);
      $("savings-input-note").textContent = count(current.estimateRows) + " priced/unused eligible rows; " + count(current.estimateMissing) + " rows without complete estimate inputs. Service rows overlap model totals and are not additive.";
      $("savings-expiry-note").textContent = "Expiring commitments: no inventory-based expiry calculation is available. Source statements only: "
        + data.sources.filter((_source, index) => !state.spend.source || state.spend.source === String(index)).map(source => source.source_name + ": " + (source.commitment_expiry_note || "not supplied")).join("; ");
      $("savings-table").innerHTML = savingsTable(model).map(row => "<tr><td>" + escape(row.name) + "</td><td>" + escape(row.enrollment) + "</td>"
        + moneyCell(row.available ? row.current.savings : null, row.current.estimateMissing ? "Partial priced-row estimate" : "")
        + '<td class="num">' + escape(row.change) + '</td><td class="num">' + escape(row.available ? row.utilization : "n/a")
        + '</td><td class="num">'+escape(row.utilizationChange)+'</td><td class="num">' + count(row.current.estimateMissing) + "</td></tr>").join("");
      $("savings-change-label").textContent = "Change vs " + monthLabel(model.previousMonth);
    }

    function scenarioKey() { return JSON.stringify([state.reportScope, state.spend]); }
    function scenarioInputs() {
      const key = scenarioKey();
      if (!state.scenarios.has(key)) state.scenarios.set(key, { change: "0", actuals: {} });
      return state.scenarios.get(key);
    }

    function renderForecastResults(model) {
      try {
        const scenario = forecastModel(model, scenarioInputs());
        state.forecast = scenario;
        $("forecast-error").hidden = true;
        $("forecast-results").hidden = false;
        for (const id of ["export-view", "download-report", "print-report"]) $(id).disabled = false;
        $("forecast-kpis").innerHTML =
          kpi("Remaining-year spend scenario", formatCompact(scenario.remaining.cost), "Projected after " + monthLabel(model.asOf), DEFINITIONS[9][1], formatMoney(scenario.remaining.cost), true)
          + kpi("Full-year spend scenario", formatCompact(scenario.full.cost), "Needs all past months or explicit earlier inputs", DEFINITIONS[9][1], formatMoney(scenario.full.cost))
          + kpi("Remaining eligible-cost scenario", formatCompact(scenario.remaining.eligible), "Not actual credit consumption", DEFINITIONS[8][1], formatMoney(scenario.remaining.eligible))
          + kpi("Known-priced savings scenario", formatCompact(scenario.knownRemainingSavings), model.current.estimateMissing ? "Partial: unpriced components remain unknown" : "Known components; not verified realized savings", DEFINITIONS[7][1], formatMoney(scenario.knownRemainingSavings));
        const months = monthRange(model.year + "-01", model.year + "-12");
        const recorded = model.names.map((name, index) => ({ name: name + " recorded", enrollment: name, colorIndex: index, values: months.map(month => {
          if (month > model.asOf) return null;
          const cell = model.annualCell(month, name);
          return cell.available ? cell.total : null;
        }) }));
        const projected = scenario.accounts.map((account, index) => ({ name: account.name + " scenario", enrollment: account.name, projected: true, colorIndex: index,
          values: months.map(month => account.projected.cost?.find(row => row.month === month)?.amount ?? null) }));
        lineChart("forecast-chart", months, [...recorded, ...projected], "Recorded spend and separately dashed future monthly run-rate scenarios");
        $("forecast-table").innerHTML = scenario.accounts.flatMap(account => ["cost", "eligible", "savings"].map(name =>
          "<tr><td>" + escape(account.name + " / " + ({ cost: "recorded cost", eligible: "eligible-cost proxy", savings: "source-price estimate" })[name]) + "</td>"
          + moneyCell(account.history.available ? account.observed[name] : null) + moneyCell(account.extra[name]) + moneyCell(account.remaining[name])
          + moneyCell(account.covered[name], "Missing past months excluded") + moneyCell(account.full[name])
          + moneyCell(name === "savings" ? account.knownRemainingSavings : null, name === "savings" && account.current.estimateMissing ? "Partial known components only" : "") + "</tr>")).join("");
        $("forecast-assumptions-print").textContent = "Daily pace change: " + fixedInteger(scenario.pace, 2, false) + "%. Supplied billing months assumed complete for this scenario only. "
          + scenario.accounts.map(account => account.name + " missing past months: " + (account.missingMonths.map(monthLabel).join(", ") || "none")).join("; ")
          + ". Earlier-period inputs, if supplied, are user assumptions and not independently reconciled.";
      } catch (error) {
        if (!(error instanceof ScenarioInputError)) throw error;
        state.forecast = null;
        $("forecast-error").hidden = false;
        $("forecast-error").textContent = error.message;
        $("forecast-results").hidden = true;
        for (const id of ["export-view", "download-report", "print-report"]) $(id).disabled = true;
      }
    }

    function renderForecast() {
      const model = workbookModel(data, state.spend), inputs = scenarioInputs();
      reportHeading("Calendar-year projections", "Explicit what-if assumptions for " + model.year + ", as of " + monthLabel(model.asOf) + "; no missing past month is assumed zero.");
      $("forecast-change").value = inputs.change;
      $("forecast-actuals").innerHTML = model.accounts.map(account => '<fieldset><legend>' + escape(account.name) + '</legend><p class="note">Missing past months: '
        + escape(account.missingMonths.map(monthLabel).join(", ") || "none") + "</p>"
        + [["cost", "Earlier-period cost actuals"], ["eligible", "Earlier eligible-cost proxy"], ["savings", "Earlier source-price estimate"]].map(([name, label]) =>
          '<label class="field">' + label + " (" + currency() + ")" + '<input type="text" inputmode="decimal" autocomplete="off" data-scenario-name="' + escape(account.name) + '" data-scenario-field="' + name
          + '" value="' + escape(inputs.actuals[account.name]?.[name] || "") + '"' + (!account.missingMonths.length ? " disabled" : "") + "></label>").join("") + "</fieldset>").join("");
      renderForecastResults(model);
    }

    function renderPotential() {
      const comparison = potentialComparison(data, state.advisorSource, state.advisor);
      const { current, previous, priorSource } = comparison, source = data.sources[current.source];
      scopeChips({ ...state.advisor, production: "yes" }, true);
      $("page-eyebrow").textContent = "Workbook Advisor Potential Savings";
      $("page-title").textContent = "Advisor annual potential";
      $("page-subtitle").textContent = source.source_name + " | updated " + (source.advisor_date_start || "not supplied") + " to " + (source.advisor_date_end || "not supplied") + ".";
      $("potential-kpis").innerHTML =
        kpi("Listed annual " + currency() + " potential", formatCompact(current.total), "Five source categories; production only; may overlap", DEFINITIONS[6][1], formatMoney(current.total), true)
        + kpi("Valued recommendations", count(current.groups.reduce((sum, group) => sum + group.valued, 0)), "Within this workbook summary scope", DEFINITIONS[6][1])
        + kpi("Unavailable " + currency() + " estimates", count(current.missing), "Other source currencies excluded, not zero", DEFINITIONS[6][1])
        + kpi("Prior snapshot annual potential", formatCompact(previous?.total ?? null), priorSource !== null ? "Not a month-on-month actual saving" : "No prior comparable snapshot supplied", DEFINITIONS[6][1], formatMoney(previous?.total ?? null));
      $("potential-caption").textContent = (data.reportingMode === "native" ? "Original " + currency() + " estimates only; no FX. " : "USD reporting estimates use saved FX for each source update month, not the spend month. ")
        + "Comparison is against the prior provided Advisor snapshot, not calendar MoM: "
        + (priorSource !== null ? data.sources[priorSource].source_name + " / " + data.sources[priorSource].advisor_date_end : "none supplied")
        + ". Only explicitly mapped production/service recommendations enter these totals. Unassigned records remain available in Combined Advisor detail, not valued at zero.";
      $("potential-table").innerHTML = comparison.rows.map(group => {
        return "<tr><td>" + escape(group.service) + "</td><td>" + escape(group.enrollment) + "</td>" + moneyCell(group.total) + moneyCell(group.before)
          + moneyCell(group.change) + '<td class="num">' + count(group.valued) + "</td></tr>";
      }).join("");
    }

    function renderBars(id, groups, total, dimension, unit=currency(), enrollment=data.reportingEnrollment) {
      if (!groups.length) {
        $(id).innerHTML = '<p class="empty">No populated spend matches this scope.</p>';
        return;
      }
      const visible = groups.slice(0, 8);
      if (groups.length > visible.length) visible.push({
        name: "Other (" + (groups.length - visible.length) + " groups)",
        cost: groups.slice(8).reduce((sum, group) => sum + group.cost, 0n), other: true,
      });
      const maximum = visible.reduce((max, group) => {
        const absolute = group.cost < 0n ? -group.cost : group.cost;
        return absolute > max ? absolute : max;
      }, 0n);
      $(id).innerHTML = '<table class="chart-table"><thead class="sr-only"><tr><th scope="col">Group</th><th scope="col">Cost ' + escape(unit) + '</th><th scope="col">Share of selected cost</th></tr></thead><tbody>'
        + visible.map(group => {
          const color=enrollmentColor(dimension==="enrollment"?group.name:enrollment)
            || (data.reportingMode==="converted"?"var(--cp-text-muted)":null);
          const absolute = group.cost < 0n ? -group.cost : group.cost;
          const width = maximum ? Number(absolute * 10000n / maximum) / 100 : 0;
          const label = group.other||dimension===null ? escape(group.name) : '<button class="bar-label" data-dimension="' + dimension + '" data-value="' + escape(group.name)
            + '" aria-label="Filter spend to ' + escape(group.name) + '">' + escape(dimension === "month" ? monthLabel(group.name) : group.name) + "</button>";
          return "<tr><td>" + label + '<div class="track" aria-hidden="true"><div class="bar' + (group.cost < 0n ? " negative" : "")
            + '" style="width:' + width + '%'+(color?';--cp-chart-color:'+color:"")+'"></div></div></td><td class="num">' + escape(formatMoney(group.cost,2,unit))
            + '</td><td class="num">' + formatRatio(group.cost, total) + "</td></tr>";
        }).join("") + "</tbody></table>";
    }

    function scopeChips(filters, advisor = false) {
      $("scope").innerHTML = '<span class="scope-label">' + (advisor ? "Advisor scope" : "Spend scope") + "</span>"
        + Object.entries(filters).filter(([, value]) => value).map(([name, value]) =>
          '<span class="scope-chip">' + escape((LABELS[name] || name) + ": " + (name === "month" ? monthLabel(value)
            : name === "source" ? data.sources[Number(value)].source_name : value)) + "</span>").join("")
        + '<span class="scope-chip">' + (advisor ? "Annual estimates in " + currency()
          : escape(snapshot.meta.basis + (["native","enrollment"].includes(state.view)?" | native currencies, not combined"
            : data.reportingMode === "native" ? " | native " + currency() + ", no FX" : " | Combined USD, converted native amounts"))) + "</span>";
    }

    function coverage() {
      const parts = [];
      const model = workbookModel(data, state.view==="enrollment"?{...state.spend,enrollment:state.detailEnrollment}:state.spend);
      parts.push("Supplied billing months: " + data.dimensions.month.map(monthLabel).join(", ") + ".");
      if(data.annualData){
        parts.push("Detailed comparisons remain "+data.dimensions.month.map(monthLabel).join(" and ")
          +"; annual history is used only in YTD, credit/savings matrices and projections.");
        if(model.history.nativeMissing)parts.push(count(model.history.nativeMissing)+" annual source rows lack this native currency/amount; annual money is unavailable, not reverse-converted.");
      }
      const missing = model.accounts.filter(account => !account.current.available).map(account => account.name);
      if (missing.length) parts.push("No " + monthLabel(model.asOf) + " source data for " + missing.join(", ") + ".");
      if (initialMonth !== lastMonth && state.spend.month === initialMonth) parts.push("Default: latest month shared by supplied enrollments.");
      const nativeView = ["native","enrollment"].includes(state.view) || data.reportingMode === "native";
      parts.push(nativeView?"Native source currencies are separate; no FX is applied in this view. Credits are eligibility proxies; savings and scenarios are estimates.":"Combined USD reporting from original native amounts. Credits are eligibility proxies; price savings and projections are estimates.");
      const annualView=["summary","credits","savings","forecast"].includes(state.view);
      const fxData=annualView?model.historyData:data;
      const dollarRates=data.reportingMode==="converted"
        ?[...(fxData.conversionDetails||[]),...(["advisor","potential"].includes(state.view)?state.recommendations.map(row=>row.estimate_fx).filter(Boolean):[])]:[...originalData.usdRates.values()];
      const estimated = [...new Map(dollarRates.filter(row=>row.estimated)
        .map(row=>[JSON.stringify([row.month,row.from,row.to,row.rate_month,row.rate]),row])).values()];
      if (estimated.length) parts.push((nativeView?"Optional Combined USD uses estimated FX: ":"Estimated USD FX: ")+estimated.map(row=>row.from+" "+row.month+" uses saved "+row.rate_month+" rate "+row.rate+" USD/"+row.from).join("; ")+". Not invoice rates.");
      const excluded=filterSpend({...fxData,rows:fxData.fxExcluded||[]},annualView?model.historyFilters:activeSpendFilters())
        .filter(row=>!annualView||(fxData.dimensions.month[row.ids[0]].startsWith(model.year)&&fxData.dimensions.month[row.ids[0]]<=model.asOf));
      if(excluded.length)parts.push("Excluded from USD figures: "+[...new Set(excluded.map(row=>fxData.dimensions.enrollment[row.ids[1]]+" "+fxData.dimensions.month[row.ids[0]]+": "+row.fxReason))].join("; ")+". Original values remain in source evidence; these are not zero.");
      $("coverage-text").textContent = parts.join(" ");
    }

    function renderSpend() {
      state.rows = filterSpend(data, state.spend);
      const metrics = { ...summarizeSpend(data, state.rows),
        available: sourceScopes(data, state.spend).some(scope => !state.spend.month || scope.month === state.spend.month) };
      scopeChips(state.spend);
      $("page-eyebrow").textContent = "Monthly business review";
      $("page-title").textContent = metrics.months.length === 1 ? monthLabel(metrics.months[0]) + " | Azure spend" : "Azure spend overview";
      $("page-subtitle").textContent = metrics.available
        ? metrics.enrollments.join(" + ") + " \u00b7 " + count(metrics.sourceRows) + " populated source rows \u00b7 " + count(metrics.subscriptions) + " subscriptions"
        : "No populated spend matches this scope. Missing data is not zero.";
      const available = amount => metrics.available ? amount : null;
      $("pricing-note").textContent = "Recorded " + snapshot.meta.basis + " cost, not retail value";
      $("spend-kpis").innerHTML =
        kpi("Recorded " + snapshot.meta.basis + " spend", formatCompact(available(metrics.total)), metrics.available ? formatMoney(metrics.total) + " \u00b7 selected billing months" : "No populated rows for this scope",
          DEFINITIONS[0][1], formatMoney(available(metrics.total)), true)
        + kpi("Reservation + savings-plan cost", formatCompact(available(metrics.benefit)), metrics.available ? formatRatio(metrics.benefit, metrics.total) + " of selected cost" : "Not available",
          DEFINITIONS[2][1], formatMoney(available(metrics.benefit)))
        + kpi("Unused benefit cost", formatMoney(available(metrics.unused)), "Recorded unused allocations; not a savings estimate",
          DEFINITIONS[3][1])
        + kpi("Credit-eligible spend", formatCompact(metrics.unknownEligibility ? null : available(metrics.eligible)), metrics.unknownEligibility ? count(metrics.unknownEligibility) + " rows lack eligibility values" : metrics.available ? formatRatio(metrics.eligible, metrics.total) + " of cost; not credits consumed" : "Not available",
          DEFINITIONS[4][1] + " Unknown flags: " + metrics.unknownEligibility, formatMoney(metrics.unknownEligibility ? null : available(metrics.eligible)));
      renderBars("service-chart", groupSpend(data, state.rows, "service"), metrics.total, "service");
      renderBars("pricing-chart", groupSpend(data, state.rows, "pricing"), metrics.total, "pricing");
      renderBars("portfolio-chart", groupSpend(data, state.rows, "portfolio"), metrics.total, "portfolio");
      renderBars("month-chart", groupSpend(data, state.rows, "month").sort((a, b) => a.name.localeCompare(b.name)), metrics.total, "month");
      $("history-note").textContent = "The Trend and Executive summary views compare supplied adjacent months. Missing dates and enrollments are explicit; export completeness is not certified.";
      renderSpendTable(metrics);
      $("export-spend").disabled = !metrics.available;
      $("result-announcement").textContent = count(metrics.sourceRows) + " source rows in the selected spend scope.";
    }

    function renderSpendTable(metrics = summarizeSpend(data, state.rows)) {
      const dimension = $("table-dimension").value;
      const groups = groupSpend(data, state.rows, dimension);
      if ($("table-sort").value === "ascending") groups.reverse();
      if ($("table-sort").value === "name") groups.sort((a, b) => a.name.localeCompare(b.name));
      const pages = Math.ceil(groups.length / PAGE_SIZE);
      state.spendPage = Math.max(0, Math.min(state.spendPage, pages - 1));
      const first = state.spendPage * PAGE_SIZE;
      const selected = groups.slice(first, first + PAGE_SIZE);
      $("group-label").textContent = LABELS[dimension];
      $("table-caption").textContent = count(groups.length) + " " + LABELS[dimension].toLowerCase() + " groups. All displayed amounts include signed cost.";
      $("spend-table").innerHTML = selected.length ? selected.map(group => "<tr><td>" + escape(group.name)
        + '</td><td class="num" title="' + escape(decimalString(group.cost) + " " + currency()) + '">' + escape(formatMoney(group.cost))
        + '</td><td class="num">' + formatRatio(group.cost, metrics.total) + '</td><td class="num">' + count(group.count) + "</td></tr>").join("")
        : '<tr><td colspan="4" class="empty">No populated spend for this scope. Broaden the filters or refresh from a more complete workbook.</td></tr>';
      $("spend-page-label").textContent = groups.length ? count(first + 1) + "\u2013" + count(first + selected.length) + " of " + count(groups.length) + " groups" : "0 groups";
      $("spend-previous").disabled = state.spendPage === 0;
      $("spend-next").disabled = state.spendPage + 1 >= pages;
      $("spend-print-note").textContent = "Detail table: " + $("spend-page-label").textContent + ". The charts and headline figures cover the full selected scope. Export filtered data CSV for every report group.";
    }

    function renderAdvisor() {
      const source = data.sources[Number(state.advisorSource)];
      const advisorSnapshot = data.advisorSnapshots.find(item => item.source === Number(state.advisorSource));
      state.recommendations = filterAdvisor(advisorSnapshot.rows, state.advisor);
      const metrics = summarizeAdvisor(state.recommendations);
      scopeChips(state.advisor, true);
      $("page-eyebrow").textContent = "Separate evidence, separate time period";
      $("page-title").textContent = "Advisor opportunity register";
      $("page-subtitle").textContent = source.source_name + " | source update dates: " + (source.advisor_date_start || "not supplied") + " to " + (source.advisor_date_end || "not supplied") + ". Annual estimates, not the spend reporting period.";
      $("advisor-kpis").innerHTML =
        kpi("Recommendations in scope", count(metrics.count), "Includes rows with incomplete mappings", "Count of source recommendation rows; potential duplicates and overlap are not removed.", "", true)
        + kpi("Listed annual " + currency() + " estimates", formatCompact(metrics.total), metrics.valued ? count(metrics.valued) + " valued rows; may overlap" : "No " + currency() + " estimates in scope",
          DEFINITIONS[6][1], formatMoney(metrics.total))
        + kpi("Unavailable " + currency() + " estimates", count(metrics.missing), "Other currencies and missing source amounts excluded", DEFINITIONS[6][1])
        + kpi("Unmapped enrollment", count(metrics.unmapped), "Not assigned to an enrollment by inference", "Recommendations without a populated Enrollment mapping.");
      $("advisor-caption").textContent = count(metrics.count) + " recommendations \u00b7 sorted by populated " + currency() + " annual estimate; unavailable estimates last. "
        + (data.reportingMode === "native" ? "Only original " + currency() + " values enter the total; no FX. " : "USD estimates use each source update month's saved FX; missing rates/dates are excluded, not zero. ")
        + count(advisorSnapshot.rows.filter(row=>row.enrollment===MISSING).length)
        + " recommendations in this source lack enrollment mapping; view them in Combined Advisor detail. No realized saving is implied.";
      const pages = Math.ceil(metrics.count / PAGE_SIZE);
      state.advisorPage = Math.max(0, Math.min(state.advisorPage, pages - 1));
      const first = state.advisorPage * PAGE_SIZE;
      const selected = state.recommendations.slice(first, first + PAGE_SIZE);
      $("advisor-table").innerHTML = selected.length ? selected.map(row => "<tr><td class=\"advisor-description\"><strong>" + escape(row.service) + "</strong><br>"
        + escape(row.recommendation) + '<br><small class="note">Source: Advisor Data Dump, row ' + row.row + "</small></td><td class=\"advisor-subscription\"><strong>"
        + escape(row.enrollment) + "</strong><br>" + escape(row.subscription) + "<small>" + escape(row.resource) + "</small></td><td>" + escape(row.impact)
        + "<br><span class=\"note\">Production: " + escape(row.production) + '</span></td><td class="num">'
        + (advisorAmount(row) === null ? '<span class="unavailable">' + escape("No " + currency() + " source estimate") + "</span>" : escape(formatMoney(parseUnits(advisorAmount(row)))))
        + "</td><td>" + (row.native_annual === null ? row.estimate_basis?.startsWith("Explicit source GBP")&&row.annual_gbp!==null
          ? "Source GBP estimate: "+escape(formatMoney(parseUnits(row.annual_gbp),2,"GBP")) : "not supplied"
          : escape(formatMoney(decimalToUnits(row.native_annual), 2, row.native_currency)))
        + '<br><span class="note">' + escape(row.updated || "date not supplied") + (row.estimate_note?"<br>"+escape(row.estimate_note):"") + "</span></td></tr>").join("")
        : '<tr><td colspan="5" class="empty">No Advisor recommendations match these filters.</td></tr>';
      $("advisor-page-label").textContent = metrics.count ? count(first + 1) + "\u2013" + count(first + selected.length) + " of " + count(metrics.count) + " recommendations" : "0 recommendations";
      $("advisor-previous").disabled = state.advisorPage === 0;
      $("advisor-next").disabled = state.advisorPage + 1 >= pages;
      $("export-advisor").disabled = metrics.count === 0;
      $("advisor-print-note").textContent = "Recommendation table: " + $("advisor-page-label").textContent + ". Cards cover every selected recommendation; export Advisor CSV for the complete selection.";
      $("result-announcement").textContent = count(metrics.count) + " recommendations; " + count(metrics.missing) + " without a " + currency() + " estimate.";
    }

    function renderQuality() {
      $("page-eyebrow").textContent = "Traceability before interpretation";
      $("page-title").textContent = "Data & definitions";
      $("page-subtitle").textContent = "What was read, what reconciles and what must not be inferred.";
      $("scope").innerHTML = '<span class="scope-chip">Whole source snapshot \u00b7 not narrowed by filters</span>';
      const history=originalData.annualData;
      const warnings=[...new Map([...snapshot.audit.warnings,...(history?.snapshot.audit.warnings||[])].map(warning=>[
        JSON.stringify([warning.code,warning.source_name,warning.detail]),warning,
      ])).values()];
      $("quality-list").innerHTML = warnings.map(warning => "<li><strong>" + escape((warning.source_name ? warning.source_name + ": " : "") + warning.title)
        + '</strong><p class="note">' + escape(warning.detail) + "</p></li>").join("");
      const sources = [
        ["Workbook", snapshot.meta.source_name], ["Populated billing months", data.dimensions.month.map(monthLabel).join(", ")],
        ["Populated source rows", count(snapshot.meta.source_rows)], ["Blank template rows excluded", count(snapshot.meta.template_rows)],
        ["Report aggregation groups", count(snapshot.meta.grouped_rows)], ["Cost basis", snapshot.meta.basis + " (workbook label)"],
        ["Annual history months",history?history.dimensions.month.map(monthLabel).join(", "):"Not enabled; comparison months only"],
        ["Annual grouped source records",history?count(history.snapshot.meta.source_rows)+" (includes comparison months once; never add to detail counts)":"Not separately retained"],
        ["Reporting currency policy", "Per-enrollment native currencies; explicit Combined USD ($) from original amounts with saved FX."],
        ["Stored GBP source reference", snapshot.meta.currency_label], ["Source modified (UTC)", snapshot.meta.source_modified_utc || "not supplied"],
        ["Extracted (UTC)", snapshot.meta.extracted_utc], ["Dashboard built (UTC)", snapshot.meta.generated_utc],
        [data.sources.length > 1 ? "Source-set SHA-256" : "Source SHA-256", snapshot.meta.source_sha256],
        ["Original handling", data.sources.some(source=>source.raw_usage)
          ? "Original CSVs read-only; exact grouping in an isolated temporary DuckDB, never the main database. Source size/time checked and content SHA-256 recorded. No workbook refreshed."
          : "Read-only Excel access. Macros and link updates disabled. Source hash verified unchanged. No main DuckDB or application changes."],
        ["Confidentiality", "This HTML contains client financial data. Keep it local and handle it with the same access restrictions as the workbook. The HTML does not inherit Excel encryption or sensitivity protection."],
      ];
      $("source-details").innerHTML = sources.map(([label, value]) => "<dt>" + escape(label) + "</dt><dd" + (label.includes("SHA") ? ' class="mono"' : "") + ">" + escape(value) + "</dd>").join("");
      const evidenceSources=[...new Map([...data.sources,...(history?.sources||[])].map(source=>[source.source_sha256,source])).values()];
      $("source-details").innerHTML += "<dt>Comparison and annual source lineage</dt><dd><div class=\"source-list\">" + evidenceSources.map(source => "<article><strong>" + escape(source.source_name)
        + "</strong><p>" + escape(source.currency_label) + '</p><p class="mono">' + escape(source.source_sha256)
        + "</p><p>Supplied scopes: " + escape((source.available_scopes || []).map(scope => scope.month + " " + scope.enrollment).join("; "))
        + (source.raw_usage ? "</p><p>Usage grouping: "+escape(count(source.raw_usage.raw_rows)+" original rows; "+count(source.raw_usage.power_query_groups)+" price-grain groups. Native total: "
          +formatMoney(decimalToUnits(source.raw_usage.native_total),2,source.raw_usage.native_currency)+". "+source.raw_usage.reconciliation) : "")
        + "</p></article>").join("") + "</div></dd>";
      $("reconciliation-table").innerHTML = snapshot.audit.reconciliations.map(row => "<tr><td>" + escape(monthLabel(row.month) + " / " + row.enrollment)
        + '</td><td class="num" title="' + escape(row.source_gbp) + '">' + escape(formatMoney(decimalToUnits(row.source_gbp)))
        + '</td><td class="num">' + escape(formatMoney(decimalToUnits(row.dashboard_gbp))) + '</td><td class="mono">' + escape(row.normalization_delta_gbp) + "</td></tr>").join("");
      $("excel-errors").textContent = snapshot.audit.excel_errors.length
        ? "Excluded summary errors: " + snapshot.audit.excel_errors.map(row => (row.source_name ? row.source_name + " / " : "") + row.sheet + ": " + row.count + " " + row.error).join("; ") + "."
        : "No Excel error codes were found in the inspected summary cells.";
      $("definitions").innerHTML = DEFINITIONS.map(([label, definition]) => '<div class="explanation"><strong>' + escape(label)
        + '</strong><p class="note">' + escape(definition) + "</p></div>").join("");
      $("credit-inputs").innerHTML = snapshot.meta.credit_inputs.length
        ? snapshot.meta.credit_inputs.map(row => '<div class="explanation" style="margin-bottom:16px"><strong>' + escape(row.enrollment)
          + "</strong><p>" + escape(formatMoney(parseUnits(row.amount),2,row.currency||"GBP")) + '</p><p class="note">Original '+escape(row.currency||"GBP")+' workbook input \u00b7 ' + escape(row.source) + "</p></div>").join("")
        : '<p class="note">No supported Credits GBP inputs were found. No balance is inferred.</p>';
      $("credit-inputs").innerHTML += '<h3 style="margin-top:16px">Source commitment-tier assumptions</h3>' + data.sources.flatMap(source =>
        (source.discount_inputs || []).map(row => '<p class="note">' + escape(source.source_name + " | " + row.product + " | discount: " + (row.discount ?? "unavailable")) + "</p>")).join("");
    }

    function renderUploads() {
      $("page-eyebrow").textContent = "Reusable report, local inputs";
      $("page-title").textContent = "Upload annual history & compare two months";
      $("page-subtitle").textContent = canUpload
        ? "Validate a workbook or CSV, review changes, then save them to this report on your PC."
        : "Open the local reporting service to enable uploads; this file is an offline snapshot.";
      $("scope").innerHTML = '<span class="scope-chip">Two-month detailed comparison</span><span class="scope-chip">Separate annual/YTD history</span>';
      $("upload-local-only").hidden = canUpload;
      $("upload-retention").textContent = snapshot.retention
        ? "Detailed comparison months: " + snapshot.retention.months.map(monthLabel).join(" and ") + ". Older grouped data is separate annual history, not detail comparison data. Advisor snapshots remain separate and capped at two."
        : "Two-month retention is applied when this report is opened through the local upload service. The main FinOps database is not affected.";
      $("upload-current-table").innerHTML = data.dimensions.month.flatMap(month => data.dimensions.enrollment.map(name => {
        const rows = filterSpend(data, { month, enrollment: name }), metrics = summarizeSpend(data, rows);
        const original=nativePeriod(data,{},month,name);
        return "<tr><td>" + escape(monthLabel(month)) + "</td><td>" + escape(name)
          +'</td><td>'+escape(original.currency||"not supplied")+'</td><td class="num">'
          +escape(formatMoney(original.cost,2,original.currency||"GBP"))+"</td>"+moneyCell(rows.length ? metrics.total : null)
          + '<td class="num">' + (rows.length ? count(metrics.sourceRows) : "not supplied") + "</td></tr>";
      })).join("");
      const history=originalData.annualData;
      $("upload-annual-note").textContent=history
        ?history.dimensions.month.map(monthLabel).join(", ")+". "+count(history.snapshot.meta.source_rows)+" source records in grouped history. Used only for annual/YTD sections; comparison months are included once, not added twice. Supply complete earlier enrollment/month files to fill gaps."
        :"Annual history will be enabled by the updated local service. Missing earlier months remain unavailable.";
      $("upload-annual-table").innerHTML=history?[...new Map(history.scopes.map(scope=>[
        scope.month+"|"+scope.enrollment,scope,
      ])).values()].sort((a,b)=>a.month.localeCompare(b.month)||a.enrollment.localeCompare(b.enrollment)).map(scope=>{
        const period=nativePeriod(history,{},scope.month,scope.enrollment);
        return "<tr><td>"+escape(monthLabel(scope.month))+"</td><td>"+escape(scope.enrollment)+"</td><td>"+escape(period.currency||"not supplied")
          +'</td><td class="num">'+escape(formatMoney(period.cost,2,period.currency||"GBP"))+'</td><td class="num">'+count(period.sourceRows)
          +"</td><td>"+(data.dimensions.month.includes(scope.month)?"Annual + current comparison":"Annual/YTD only")+"</td></tr>";
      }).join(""):'<tr><td colspan="6">No separate annual history is stored yet.</td></tr>';
      $("upload-basis").value = snapshot.meta.basis;
      uploadControls();
    }

    function uploadControls() {
      const disabled = !canUpload || state.uploading;
      for (const id of ["upload-file", "upload-kind", "upload-basis", "upload-currency", "upload-rate", "upload-validate"]) $(id).disabled = disabled;
      $("upload-apply").disabled = disabled || !state.uploadPreview || (state.uploadPreview.requires_replacement && !$("upload-replace").checked);
      $("upload-currency-options").hidden = $("upload-kind").value !== "cost";
    }

    function clearUploadPreview() {
      state.uploadPreview = null;
      $("upload-preview").hidden = true;
      $("upload-replace").checked = false;
      $("upload-status").textContent = "";
      $("upload-error").hidden = true;
      uploadControls();
    }

    async function uploadRequest(path, body, headers = {}) {
      const response = await fetch(path, {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "X-AzFinOps-Report": host.token, ...headers }, body,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Report server returned HTTP " + response.status);
      return result;
    }

    function uploadFailure(error) {
      console.error("Report upload failed", error);
      $("upload-error").hidden = false;
      $("upload-error").textContent = error.message + " The report has not been successfully updated. Check the local server is running if the request could not connect.";
      $("upload-status").textContent = "Upload not applied.";
    }

    function setView(view) {
      const views = ["enrollment", "native", ...WORKBOOK_VIEWS, "potential", "spend", "advisor", "uploads", "quality"];
      if (!views.includes(view)) throw new Error("Unknown report view");
      if (view === "native" && state.view !== "native") state.spend.enrollment = "";
      state.view = view;
      const sourceView = ["native", "enrollment", "uploads", "quality"].includes(view);
      const reportSource=["summary","credits","savings","forecast"].includes(view)?originalData:{...originalData,annualData:null};
      data = sourceView ? originalData : reportingData(reportSource, state.reportScope === "combined-usd" ? null : state.detailEnrollment);
      if (!sourceView) {
        state.spend.enrollment = data.reportingEnrollment || "";
        state.advisor.enrollment = data.reportingEnrollment || "";
      }
      $("filter-enrollment").value = state.spend.enrollment || "";
      $("filter-publisher").value = activeSpendFilters().publisher || "";
      $("advisor-enrollment").value = state.advisor.enrollment || "";
      $("report-scope").value = state.reportScope;
      $("detail-enrollment").value = state.detailEnrollment;
      $("report-scope-control").hidden = ["native", "uploads", "quality"].includes(view);
      document.querySelectorAll("[data-report-currency]").forEach(element => { element.textContent = currency(); });
      document.querySelectorAll("[data-view]").forEach(element => element.setAttribute("aria-pressed", String(element.dataset.view === view
        &&(view!=="enrollment"||element.dataset.enrollment===state.detailEnrollment))));
      for (const name of views) $(name + "-panel").hidden = view !== name;
      $("spend-filters").hidden = !WORKBOOK_VIEWS.includes(view) && !["spend","native","enrollment"].includes(view);
      $("filter-enrollment-field").hidden=view!=="native";
      $("advisor-enrollment").closest("label").hidden = true;
      $("advisor-filters").hidden = !["advisor", "potential"].includes(view);
      $("advisor-production-field").hidden = view === "potential";
      $("scope-options").hidden = ["quality", "uploads"].includes(view);
      for (const id of ["export-view", "download-report", "print-report"]) $(id).disabled = false;
      $("export-view").hidden = ["quality", "uploads"].includes(view);
      $("download-report").hidden = view === "uploads";
      $("print-report").hidden = view === "uploads";
      $("currency-badge").textContent=view==="native"?"Native "+[...new Set(data.rows.map(row=>row.nativeCurrency).filter(value=>/^[A-Z]{3}$/.test(value)))].sort().join(" / ")+" - not combined":view==="uploads"?"Native currencies + separate GBP column"
        : data.reportingMode === "native" ? state.detailEnrollment + " | native " + currency()
          : view === "quality" ? "Original source currencies + USD reporting evidence" : "Combined USD ($) | converted native amounts";
      if (view === "enrollment") renderEnrollment();
      if (view === "native") renderNative();
      if (view === "summary") renderSummary();
      if (view === "trend") renderTrend();
      if (view === "drivers") renderDrivers();
      if (view === "subscriptions") renderSubscriptions();
      if (view === "credits") renderCredits();
      if (view === "savings") renderSavings();
      if (view === "forecast") renderForecast();
      if (view === "potential") renderPotential();
      if (view === "spend") renderSpend();
      if (view === "advisor") renderAdvisor();
      if (view === "uploads") renderUploads();
      if (view === "quality") renderQuality();
      coverage();
      const hash=view==="enrollment"?"enrollment="+encodeURIComponent(state.detailEnrollment):view+"?scope="+encodeURIComponent(state.reportScope);
      window.history.replaceState(null,"","#"+hash);
    }

    function download(name, content, type) {
      const url = URL.createObjectURL(new Blob([content], { type }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = name;
      document.body.appendChild(anchor);
      try { anchor.click(); }
      finally {
        anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    }

    function changeReportScope(scope, view = state.view) {
      if(scope==="combined-gbp")scope="combined-usd";
      if (scope !== "combined-usd" && !originalData.dimensions.enrollment.includes(scope)) throw new Error("Unknown reporting enrollment");
      state.reportScope = scope;
      if (scope !== "combined-usd") state.detailEnrollment = scope;
      state.spend = { month: state.spend.month || initialMonth, ...(scope === "combined-usd" ? {} : { enrollment: scope }) };
      state.advisor = scope === "combined-usd" ? {} : { enrollment: scope };
      state.spendPage = state.advisorPage = state.detailPage = state.driversPage = state.subscriptionsPage = 0;
      document.querySelectorAll("[data-filter]").forEach(element=>{element.value=state.spend[element.dataset.filter]||"";});
      document.querySelectorAll("[data-advisor-filter]").forEach(element=>{element.value=state.advisor[element.dataset.advisorFilter]||"";});
      setView(view === "enrollment" && scope === "combined-usd" ? "summary" : view);
    }

    function openEnrollment(name) {
      state.spend.month = defaultReportingMonth(originalData, {enrollment:name}) || initialMonth;
      changeReportScope(name, "enrollment");
    }

    $("report-scope").addEventListener("change", () => changeReportScope($("report-scope").value));
    document.querySelectorAll("[data-view]").forEach(element => element.addEventListener("click", () => {
      if(element.dataset.view==="enrollment"){openEnrollment(element.dataset.enrollment);return;}
      if(element.dataset.view==="native"&&state.view==="enrollment"){
        state.spend.enrollment="";
        $("filter-enrollment").value="";
      }
      setView(element.dataset.view);
    }));
    $("detail-enrollment").addEventListener("change",()=>openEnrollment($("detail-enrollment").value));
    $("detail-reset").addEventListener("click",()=>openEnrollment(state.detailEnrollment));
    $("detail-dimension").addEventListener("change",()=>{state.detailPage=0;renderEnrollmentTable();});
    $("detail-sort").addEventListener("change",()=>{state.detailPage=0;renderEnrollmentTable();});
    $("detail-previous").addEventListener("click",()=>{state.detailPage-=1;renderEnrollmentTable();});
    $("detail-next").addEventListener("click",()=>{state.detailPage+=1;renderEnrollmentTable();});
    $("export-enrollment-data").addEventListener("click",()=>{
      const detail=currentEnrollmentDetail();
      download("azure-mbr-"+detail.enrollment.replace(/[^A-Za-z0-9_-]/g,"-")+"-"+detail.asOf+"-native-data.csv","\ufeff"+enrollmentRowsCsv(detail),"text/csv;charset=utf-8");
    });
    for (const id of ["upload-file", "upload-kind", "upload-basis", "upload-currency", "upload-rate"]) {
      $(id).addEventListener("change", clearUploadPreview);
    }
    $("upload-replace").addEventListener("change", uploadControls);
    $("upload-validate").addEventListener("click", async () => {
      if (!canUpload || state.uploading) return;
      clearUploadPreview();
      state.uploading = true;
      uploadControls();
      try {
        const file = $("upload-file").files[0];
        if (!file || file.size === 0) throw new Error("Select a non-empty local file first.");
        if (file.size > 128 * 1024 * 1024) throw new Error("The upload limit is 128 MiB. Use a smaller complete report-level CSV or workbook.");
        $("upload-status").textContent = "Reading and validating locally. Excel workbooks can take a minute. No report change has been applied.";
        const params = new URLSearchParams({
          kind: $("upload-kind").value, basis: $("upload-basis").value,
          currency: $("upload-currency").value.trim().toUpperCase(), rate: $("upload-rate").value.trim(),
        });
        const preview = await uploadRequest("/api/report/validate?" + params, file, {
          "Content-Type": "application/octet-stream", "X-Report-Filename": encodeURIComponent(file.name),
        });
        state.uploadPreview = preview;
        $("upload-preview").hidden = false;
        $("upload-replace-label").hidden = !preview.requires_replacement;
        $("upload-preview-summary").textContent = preview.filename + " | Retain " + preview.retained_months.map(monthLabel).join(" and ")
          + (preview.removed_months.length ? " | Remove older active months: " + preview.removed_months.map(monthLabel).join(", ") : "")
          + " | " + count(preview.retained_rows) + " comparison source rows. Annual history: "+(preview.annual_months||[]).map(monthLabel).join(", ")
          +". "+(preview.comparison_changed?"Comparison data also changes.":"Only annual history/Advisor changes; comparison data is unchanged.")
          +" "+count(preview.unpriced_rows) + " unpriced comparison savings rows; "
          + count(preview.unknown_eligibility_rows) + " rows lack eligibility values. Nothing has been applied yet.";
        $("upload-preview-table").innerHTML = preview.changes.length ? preview.changes.map(change => "<tr><td>" + escape(monthLabel(change.month) + " / " + change.enrollment)
          + "</td><td>" + escape(change.action) + "</td>" + moneyCell(change.previous_gbp === null ? null : decimalToUnits(change.previous_gbp))
          + moneyCell(decimalToUnits(change.incoming_gbp)) + '<td class="num">' + count(change.incoming_rows) + "</td></tr>").join("")
          : '<tr><td colspan="5">Advisor snapshot only. Retained cost months and amounts are unchanged.</td></tr>';
        $("upload-status").textContent = "Validation passed. Review the preview before applying.";
      } catch (error) {
        uploadFailure(error);
      } finally {
        state.uploading = false;
        uploadControls();
      }
    });
    $("upload-apply").addEventListener("click", async () => {
      if (!canUpload || state.uploading || !state.uploadPreview) return;
      state.uploading = true;
      uploadControls();
      try {
        $("upload-status").textContent = "Saving the validated report. Do not close this tab.";
        await uploadRequest("/api/report/apply", JSON.stringify({
          id: state.uploadPreview.id, replace: $("upload-replace").checked,
        }), { "Content-Type": "application/json" });
        window.location.hash = "uploads";
        window.location.reload();
      } catch (error) {
        uploadFailure(error);
        state.uploading = false;
        uploadControls();
      }
    });
    $("main-content").addEventListener("click", event => {
      const enrollment=event.target.closest("button[data-open-enrollment]");
      if(enrollment){openEnrollment(enrollment.dataset.openEnrollment);return;}
      const element = event.target.closest("button[data-dimension]");
      if (!element) return;
      state.spend[element.dataset.dimension] = element.dataset.value;
      $("filter-" + element.dataset.dimension).value = element.dataset.value;
      state.spendPage = 0;
      state.detailPage = 0;
      state.driversPage = 0;
      state.subscriptionsPage = 0;
      setView(state.view);
    });
    $("reset-spend").addEventListener("click", () => {
      state.spend = { month: initialMonth,...(state.view==="enrollment"?{enrollment:state.detailEnrollment}:{}) };
      state.spendPage = 0;
      state.detailPage = 0;
      state.driversPage = 0;
      state.subscriptionsPage = 0;
      if(state.view==="drivers")state.driversPublisher="Azure";
      document.querySelectorAll("[data-filter]").forEach(element => { element.value = state.spend[element.dataset.filter] || ""; });
      setView(state.view);
    });
    $("reset-advisor").addEventListener("click", () => {
      state.advisor = {};
      state.advisorPage = 0;
      document.querySelectorAll("[data-advisor-filter]").forEach(element => { element.value = ""; });
      setView(state.view);
    });
    $("trend-workbook-scope").addEventListener("click", () => {
      state.spend.publisher = "Azure";
      $("filter-publisher").value = "Azure";
      renderTrend();
      coverage();
    });
    $("table-dimension").addEventListener("change", () => { state.spendPage = 0; renderSpendTable(); });
    $("table-sort").addEventListener("change", () => { state.spendPage = 0; renderSpendTable(); });
    for(const id of ["drivers-dimension","drivers-sort"])$(id).addEventListener("change",()=>{state.driversPage=0;renderDriversTable();});
    $("drivers-previous").addEventListener("click",()=>{state.driversPage-=1;renderDriversTable();});
    $("drivers-next").addEventListener("click",()=>{state.driversPage+=1;renderDriversTable();});
    $("subscriptions-sort").addEventListener("change",()=>{state.subscriptionsPage=0;renderSubscriptionsTable();});
    $("subscriptions-previous").addEventListener("click",()=>{state.subscriptionsPage-=1;renderSubscriptionsTable();});
    $("subscriptions-next").addEventListener("click",()=>{state.subscriptionsPage+=1;renderSubscriptionsTable();});
    $("export-credit-matrix").addEventListener("click",()=>{
      const model=workbookModel(data,state.spend);
      download("azure-mbr-credit-eligibility-"+model.asOf+"-"+currency()+"-matrix.csv",
        "\ufeff"+creditMatrixCsv(model),"text/csv;charset=utf-8");
    });
    $("export-savings-matrix").addEventListener("click",()=>{
      const model=workbookModel(data,state.spend);
      download("azure-mbr-savings-"+model.asOf+"-"+currency()+"-matrix.csv",
        "\ufeff"+savingsMatrixCsv(model),"text/csv;charset=utf-8");
    });
    for (const [name, render] of [["spend", renderSpendTable], ["advisor", renderAdvisor]]) {
      $(name + "-previous").addEventListener("click", () => { state[name + "Page"] -= 1; render(); });
      $(name + "-next").addEventListener("click", () => { state[name + "Page"] += 1; render(); });
    }
    $("export-spend").addEventListener("click", () => download("azure-mbr-spend-" + (state.spend.month || "all-months") + ".csv", "\ufeff" + spendCsv(data, state.rows), "text/csv;charset=utf-8"));
    $("export-advisor").addEventListener("click", () => download("azure-mbr-advisor-estimates.csv", "\ufeff" + advisorCsv(state.recommendations, data.sources[Number(state.advisorSource)].source_name, currency()), "text/csv;charset=utf-8"));
    $("forecast-change").addEventListener("input", () => {
      scenarioInputs().change = $("forecast-change").value;
      renderForecastResults(workbookModel(data, state.spend));
    });
    $("forecast-actuals").addEventListener("input", event => {
      const element = event.target;
      if (!element.dataset.scenarioName) return;
      const inputs = scenarioInputs().actuals;
      if (!inputs[element.dataset.scenarioName]) inputs[element.dataset.scenarioName] = {};
      inputs[element.dataset.scenarioName][element.dataset.scenarioField] = element.value;
      renderForecastResults(workbookModel(data, state.spend));
    });
    function potentialCsv() {
      const comparison = potentialComparison(data, state.advisorSource, state.advisor), source = data.sources[Number(state.advisorSource)];
      const previous = comparison.priorSource === null ? null : data.sources[comparison.priorSource];
      return toCsv(["Service", "Enrollment", "Listed annual " + currency() + " estimate", "Prior snapshot " + currency() + " estimate", "Snapshot change " + currency(), "Valued rows", "Unavailable " + currency() + " rows", "Snapshot source", "Updated start", "Updated end", "Prior source", "Prior as of", "Scope"],
        comparison.rows.map(group => [group.service, group.enrollment, group.total === null ? null : decimalString(group.total),
          group.before === null ? null : decimalString(group.before), group.change === null ? null : decimalString(group.change), group.valued, group.missing,
          source.source_name, source.advisor_date_start, source.advisor_date_end, previous?.source_name ?? null, previous?.advisor_date_end ?? null,
          "Production only; five categories; estimates may overlap | " + data.reportingMode + " " + currency() + " | " + JSON.stringify(state.advisor)]), [2, 3, 4, 5, 6]);
    }
    $("export-view").addEventListener("click", () => {
      const content = state.view==="enrollment"?enrollmentCsv(currentEnrollmentDetail(),$("detail-dimension").value,$("detail-sort").value):state.view==="native"?nativeComparisonCsv(data,state.spend):WORKBOOK_VIEWS.includes(state.view) ? workbookCsv(workbookModel(data, activeSpendFilters()), state.view, state.forecast, state.advisorSource,workbookOptions())
        : state.view === "potential" ? potentialCsv() : state.view === "advisor" ? advisorCsv(state.recommendations, data.sources[Number(state.advisorSource)].source_name, currency()) : spendCsv(data, state.rows);
      download("azure-mbr-" + state.view + ".csv", "\ufeff" + content, "text/csv;charset=utf-8");
    });
    $("download-report").addEventListener("click", () => {
      if(state.view==="enrollment"){
        const detail=currentEnrollmentDetail();
        download("azure-mbr-"+detail.enrollment.replace(/[^A-Za-z0-9_-]/g,"-")+"-native-detail-report.txt",enrollmentReportText(detail,$("detail-dimension").value,$("report-notes").value,$("detail-sort").value),"text/plain;charset=utf-8");
        return;
      }
      if(state.view==="native"){
        download("azure-mbr-native-report.txt",nativeComparisonText(data,state.spend,$("report-notes").value),"text/plain;charset=utf-8");
        return;
      }
      if (WORKBOOK_VIEWS.includes(state.view)) {
        download("azure-mbr-" + state.view + "-report.txt", workbookReportText(workbookModel(data, activeSpendFilters()), state.view, $("report-notes").value, state.forecast, state.advisorSource,workbookOptions()), "text/plain;charset=utf-8");
        return;
      }
      if (state.view === "potential") {
        download("azure-mbr-advisor-potential-report.txt", "ADVISOR ANNUAL POTENTIAL\r\nProduction-only source summary; estimates may overlap and are not realized savings.\r\n"
          + potentialCsv() + "\r\nCOMMENTARY\r\n" + $("report-notes").value, "text/plain;charset=utf-8");
        return;
      }
      const advisor = state.view === "advisor";
      const rows = advisor ? state.recommendations : state.rows;
      const metrics = advisor ? summarizeAdvisor(rows) : summarizeSpend(data, rows);
      const groups = advisor ? rows : groupSpend(data, rows, "service");
      const reportSnapshot = advisor ? { ...snapshot, meta: { ...snapshot.meta, ...data.sources[Number(state.advisorSource)], sources: [data.sources[Number(state.advisorSource)]] } } : snapshot;
      download("azure-mbr-" + state.view + "-report.txt", reportText(reportSnapshot, state.view, state.view === "quality" ? {} : advisor ? state.advisor : state.spend, metrics, groups, $("report-notes").value, currency()), "text/plain;charset=utf-8");
    });
    $("report-notes").addEventListener("input", () => { $("notes-print").textContent = $("report-notes").value; });
    $("print-report").addEventListener("click", () => {
      document.documentElement.dataset.beforePrintTheme = document.documentElement.dataset.theme;
      document.documentElement.dataset.theme = "light";
      window.print();
    });
    window.addEventListener("afterprint", () => {
      if (document.documentElement.dataset.beforePrintTheme) {
        document.documentElement.dataset.theme = document.documentElement.dataset.beforePrintTheme;
        delete document.documentElement.dataset.beforePrintTheme;
      }
    });
    window.addEventListener("error", event => fatal(event.error || new Error(event.message)));
    $("report-footnote").textContent = "Sources: " + data.sources.map(source => source.source_name).join("; ") + " \u00b7 extracted " + snapshot.meta.extracted_utc
      + " \u00b7 offline snapshot, not a live Azure connection. Client financial data: this HTML does not inherit workbook encryption. Keep it local.";
    function routeFromHash() {
      const requested=window.location.hash.slice(1);
      if(requested.startsWith("enrollment="))openEnrollment(decodeURIComponent(requested.slice("enrollment=".length)));
      else if(requested) {
        const [view, query] = requested.split("?");
        const scope = new URLSearchParams(query || "").get("scope");
        if (scope) changeReportScope(scope, view);
        else setView(view);
      }
      else openEnrollment(state.detailEnrollment);
    }
    window.addEventListener("hashchange",routeFromHash);
    routeFromHash();
  } catch (error) {
    fatal(error);
  }
})();
