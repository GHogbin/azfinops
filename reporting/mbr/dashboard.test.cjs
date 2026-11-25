const test = require("node:test");
const assert = require("node:assert/strict");
const mbr = require("./dashboard.js");

test("default enrollment comes from supplied native currencies, not a customer name", () => {
  const data = { dimensions: { enrollment: ["Alpha", "Beta"] },
    rows: [{ ids: [0, 0], nativeCurrency: "GBP" }, { ids: [0, 1], nativeCurrency: "USD" }] };
  assert.equal(mbr.defaultEnrollment(data), "Beta");
  data.rows[1].nativeCurrency = "EUR";
  assert.equal(mbr.defaultEnrollment(data), "Alpha");
  assert.equal(mbr.defaultEnrollment({ dimensions: { enrollment: [] }, rows: [] }), "");
});

test("enrollment colours stay tied to the full source list across filtered views", () => {
  assert.equal(mbr.enrollmentColor("Alpha", ["Beta", "Alpha"]), "var(--cp-enrollment-1)");
  assert.equal(mbr.enrollmentColor("Beta", ["Alpha", "Beta"]), "var(--cp-enrollment-2)");
  assert.equal(mbr.enrollmentColor("missing", ["Alpha", "Beta"]), null);
});

function snapshot() {
  const dimensions = {
    month: ["2026-07", "2026-08"], enrollment: ["ExampleUS"],
    service: ["Storage", "Virtual Machines"], subscription: ["sub-a", "sub-b"],
    region: ["uksouth"], pricing: ["OnDemand", "Reservation", "SavingsPlan"],
    portfolio: ["Brownfield", "Greenfield"], allocation: ["Unused", "Used"],
    eligible: ["No", "Yes"], usage: ["Azure"],
  };
  return {
    version: 1, scale: 18, dimension_order: Object.keys(dimensions), dimensions,
    rows: [
      [1, 0, 0, 0, 0, 0, 0, 1, 1, 0, "100000000000000000000", 2],
      [1, 0, 1, 1, 0, 1, 1, 1, 1, 0, "50000000000000000000", 1],
      [1, 0, 1, 1, 0, 1, 1, 0, 1, 0, "10000000000000000000", 1],
      [1, 0, 0, 0, 0, 0, 0, 1, 0, 0, "-5000000000000000000", 1],
      [0, 0, 0, 0, 0, 0, 0, 1, 1, 0, "999000000000000000000", 1],
    ],
    meta: { source_name: "test.xlsb", source_sha256: "abc", extracted_utc: "2026-11-10", basis: "amortized", currency_label: "Currency: GBP" },
    audit: { warnings: [{ title: "No savings claim", detail: "Not reportable" }] },
  };
}

test("money stays exact beyond JavaScript Number precision", () => {
  assert.equal(mbr.formatMoney(mbr.decimalToUnits("9007199254740993.25")), "\u00a39,007,199,254,740,993.25");
  assert.equal(mbr.decimalString(mbr.decimalToUnits("-0.000000000000000001")), "-0.000000000000000001");
});

test("money is rounded half-even, including negatives and tiny negative zero", () => {
  assert.equal(mbr.formatMoney(mbr.decimalToUnits("1.005")), "\u00a31.00");
  assert.equal(mbr.formatMoney(mbr.decimalToUnits("1.015")), "\u00a31.02");
  assert.equal(mbr.formatMoney(mbr.decimalToUnits("-1.015")), "-\u00a31.02");
  assert.equal(mbr.formatMoney(mbr.decimalToUnits("-0.001")), "\u00a30.00");
});

test("decimal conversion rounds to 18 places only once", () => {
  assert.equal(mbr.decimalToUnits("0.0000000000000000005"), 0n);
  assert.equal(mbr.decimalToUnits("0.0000000000000000015"), 2n);
  assert.equal(mbr.decimalToUnits("-0.0000000000000000015"), -2n);
  assert.throws(() => mbr.decimalToUnits("NaN"), /Invalid decimal/);
  assert.throws(() => mbr.parseUnits(12), /Invalid monetary/);
});

test("compact values and percentages use exact integer rounding", () => {
  assert.equal(mbr.formatCompact(mbr.decimalToUnits("10172475.6379")), "\u00a310.17M");
  assert.equal(mbr.formatRatio(1n, 8n), "12.5%");
  assert.equal(mbr.formatRatio(0n, 0n), "n/a");
  assert.equal(mbr.formatMoney(null), "n/a");
});

test("spend filters are conjunctive and monthly scope is real data, not a filename", () => {
  const data = mbr.prepare(snapshot());
  const rows = mbr.filterSpend(data, { month: "2026-08", service: "Storage", query: "SUB-A" });
  const metrics = mbr.summarizeSpend(data, rows);
  assert.equal(metrics.total, mbr.decimalToUnits("95"));
  assert.equal(metrics.sourceRows, 3);
  assert.equal(metrics.subscriptions, 1);
  assert.deepEqual(metrics.months, ["2026-08"]);
  assert.equal(mbr.filterSpend(data, { month: "2026-08", service: "Storage", pricing: "Reservation" }).length, 0);
});

test("missing ExampleUK data and genuine zero cost remain distinct", () => {
  const data = mbr.prepare(snapshot());
  const missing = mbr.summarizeSpend(data, mbr.filterSpend(data, { enrollment: "ExampleUK" }));
  assert.equal(missing.available, false);
  const raw = snapshot();
  raw.rows[0][10] = "0";
  const zeroData = mbr.prepare(raw);
  assert.equal(mbr.summarizeSpend(zeroData, [zeroData.rows[0]]).available, true);
  assert.equal(mbr.summarizeSpend(zeroData, [zeroData.rows[0]]).total, 0n);
});

test("benefit cost includes used and unused, eligibility does not hide signed credits", () => {
  const data = mbr.prepare(snapshot());
  const metrics = mbr.summarizeSpend(data, mbr.filterSpend(data, { month: "2026-08" }));
  assert.equal(metrics.total, mbr.decimalToUnits("155"));
  assert.equal(metrics.benefit, mbr.decimalToUnits("60"));
  assert.equal(metrics.unused, mbr.decimalToUnits("10"));
  assert.equal(metrics.eligible, mbr.decimalToUnits("160"));
  assert.equal(mbr.formatRatio(metrics.eligible, metrics.total), "103.2%");
});

test("every grouped breakdown reconciles to the selected signed cost", () => {
  const data = mbr.prepare(snapshot());
  const rows = mbr.filterSpend(data, { month: "2026-08" });
  for (const dimension of data.snapshot.dimension_order) {
    assert.equal(mbr.groupSpend(data, rows, dimension).reduce((sum, group) => sum + group.cost, 0n), mbr.decimalToUnits("155"));
  }
});

test("bad schemas, broken references and invalid amounts fail explicitly", () => {
  const schema = snapshot();
  schema.scale = 2;
  assert.throws(() => mbr.prepare(schema), /schema/);
  const bad = snapshot();
  bad.rows[0][0] = 20;
  assert.throws(() => mbr.prepare(bad), /reference/);
  assert.throws(() => mbr.roundDivide(1n, 0n), /zero/);
});

function recommendations() {
  return [
    { row: 2, enrollment: "ExampleUS", service: "Storage", impact: "High", production: "yes", subscription: "sub-a", subscription_id: "id-a", resource: "vm", recommendation: "Review reservation", annual_gbp: "100000000000000000000", native_annual: "100", native_currency: "GBP", updated: "2026-07-17" },
    { row: 3, enrollment: "(not supplied)", service: "(not supplied)", impact: "High", production: "(not supplied)", subscription: "sub-b", subscription_id: "id-b", resource: "db", recommendation: "Review database", annual_gbp: null, native_annual: "500", native_currency: "USD", updated: null },
    { row: 4, enrollment: "ExampleUK", service: "Storage", impact: "High", production: "no", subscription: "sub-c", subscription_id: "id-c", resource: "vm2", recommendation: "Review storage", annual_gbp: "0", native_annual: "0", native_currency: "GBP", updated: "2026-07-17" },
  ];
}

test("Advisor filters are independent, missing GBP is not zero and genuine zero is valued", () => {
  const rows = mbr.filterAdvisor(recommendations(), {});
  assert.equal(rows[2].annual_gbp, null);
  const metrics = mbr.summarizeAdvisor(rows);
  assert.deepEqual(metrics, { count: 3, valued: 2, missing: 1, unmapped: 1, total: mbr.decimalToUnits("100") });
  assert.equal(mbr.filterAdvisor(rows, { enrollment: "ExampleUK", production: "no" }).length, 1);
  assert.equal(mbr.filterAdvisor(rows, { query: "DATABASE" })[0].row, 3);
  assert.equal(mbr.summarizeAdvisor([rows[2]]).total, null);
});

test("CSV escaping neutralizes formula text but preserves signed exact amounts", () => {
  assert.equal(mbr.csvCell('say "hi", now'), '"say ""hi"", now"');
  assert.equal(mbr.csvCell("=SUM(A1)"), "'=SUM(A1)");
  assert.equal(mbr.csvCell("  @attack"), "'  @attack");
  assert.equal(mbr.csvCell("-12.345", true), "-12.345");
  assert.equal(mbr.csvCell(null, true), "");
  assert.throws(() => mbr.csvCell("=evil", true), /numeric CSV/);
  assert.throws(() => mbr.toCsv(["one"], [["one", "two"]]), /width/);
});

test("CSV exports contain all selected groups and preserve unknown estimates as empty", () => {
  const data = mbr.prepare(snapshot());
  const rows = mbr.filterSpend(data, { month: "2026-08" });
  const csv = mbr.spendCsv(data, rows);
  assert.equal(csv.trim().split("\r\n").length, 5);
  assert.ok(csv.includes("Cost GBP,Source rows"));
  assert.ok(csv.includes(",-5,1,"));
  assert.equal(csv.split("\r\n")[0].split(",").length, 17);
  const advisor = mbr.advisorCsv(recommendations());
  assert.ok(advisor.includes(",,500,USD,"));
  assert.ok(advisor.includes("Source sheet row"));
});

test("report output names actual scope, definitions, limitations and commentary", () => {
  const data = mbr.prepare(snapshot());
  const rows = mbr.filterSpend(data, { month: "2026-08" });
  const report = mbr.reportText(data.snapshot, "spend", { month: "2026-08" }, mbr.summarizeSpend(data, rows), mbr.groupSpend(data, rows, "service"), "Owner: review next week");
  assert.ok(report.includes("Billing months: 2026-08"));
  assert.ok(report.includes("Recorded spend (GBP): \u00a3155.00"));
  assert.ok(report.includes("Source SHA-256: abc"));
  assert.ok(report.includes("Credit eligibility is not credit consumption."));
  assert.ok(report.includes("Owner: review next week"));
  assert.ok(report.includes("SOURCE LIMITATIONS"));
  assert.ok(report.includes("DEFINITIONS"));
});

function workbookSnapshot() {
  const records = [
    ["2026-04", "ExampleUS", "Storage", "OnDemand", "Used", "3000", "Yes", "0", 0],
    ["2026-04", "ExampleUK", "Storage", "OnDemand", "Used", "600", "Yes", "0", 0],
    ["2026-05", "ExampleUS", "Storage", "OnDemand", "Used", "2990", "Yes", "0", 0],
    ["2026-05", "ExampleUS", "Virtual Machines", "Reservation", "Used", "100", "Yes", "30", 0],
    ["2026-05", "ExampleUS", "Virtual Machines", "Reservation", "Unused", "10", "No", "0", 0],
    ["2026-05", "ExampleUK", "Storage", "OnDemand", "Used", "620", "Yes", "0", 0],
    ["2026-08", "ExampleUS", "Storage", "OnDemand", "Used", "9300", "Yes", "0", 1],
  ];
  const order = ["month", "enrollment", "service", "subscription", "region", "pricing", "portfolio", "allocation", "eligible", "usage"];
  const unpacked = records.map(row => [row[0], row[1], row[2], "sub-a", "uksouth", row[3], "Greenfield", row[4], row[6], "Azure"]);
  const dimensions = Object.fromEntries(order.map((name, index) => [name, [...new Set(unpacked.map(row => row[index]))].sort()]));
  const pool = [{enrollment:"ExampleUS",amount:String(mbr.decimalToUnits("100000")),source:"Credits!C4"}, {enrollment:"ExampleUK",amount:String(mbr.decimalToUnits("20000")),source:"Credits!C5"}];
  const sources = [
    { source_name:"May workbook", source_sha256:"may", credit_inputs:pool, advisor_date_start:"2026-06-08", advisor_date_end:"2026-06-09" },
    { source_name:"August workbook", source_sha256:"august", credit_inputs:pool, advisor_date_start:"2026-07-17", advisor_date_end:"2026-07-17" },
  ];
  return {
    version:1, scale:18, dimension_order:order, dimensions,
    rows:unpacked.map((row,index) => [...row.map((value,column) => dimensions[order[column]].indexOf(value)), String(mbr.decimalToUnits(records[index][5])), 1]),
    row_sources:records.map(row => row[8]),
    row_publishers:records.map(()=> "Azure"),
    calculations:{version:1,rows:records.map(row => [String(mbr.decimalToUnits(row[7])),"0",row[3]==="Reservation"?1:0,0])},
    meta:{source_name:"Two sources",source_sha256:"bundle",basis:"amortized",currency_label:"Currency: GBP",sources,credit_inputs:pool,billing_accounts:{ExampleUS:["ref"],ExampleUK:["example_uk"]}},
    advisor_snapshots:[{source:0,rows:[]},{source:1,rows:[]}],
  };
}

test("latest shared month defaults to May rather than silently mixing August ExampleUS with May ExampleUK", () => {
  const data = mbr.prepare(workbookSnapshot());
  assert.equal(mbr.defaultReportingMonth(data), "2026-05");
  assert.equal(mbr.defaultReportingMonth(data,{source:"1"}), "2026-08");
  const model = mbr.workbookModel(data,{month:"2026-05"});
  assert.equal(model.current.total,mbr.decimalToUnits("3720"));
  assert.equal(model.current.eligible,mbr.decimalToUnits("3710"));
  assert.equal(model.current.savings,mbr.decimalToUnits("20"));
  assert.equal(model.completeCurrentScope,true);
});

test("source filters keep histories separate and August's missing ExampleUK is not zero", () => {
  const data = mbr.prepare(workbookSnapshot());
  const rows = mbr.filterSpend(data,{source:"1"});
  assert.equal(rows.length,1);
  const model = mbr.workbookModel(data,{month:"2026-08"});
  const example_uk = model.accounts.find(account => account.name==="ExampleUK");
  assert.equal(example_uk.current.available,false);
  assert.equal(model.completeCurrentScope,false);
  assert.ok(example_uk.missingMonths.includes("2026-08"));
  const csv = mbr.workbookCsv(model,"trend");
  assert.ok(csv.includes("2026-08,ExampleUK,no,,,"));
});

test("no matching activity in a supplied period is distinguished from absent source data", () => {
  const data = mbr.prepare(workbookSnapshot());
  const model = mbr.workbookModel(data,{month:"2026-05",service:"not observed"});
  assert.equal(model.current.available,true);
  assert.equal(model.current.total,0n);
  assert.equal(model.accounts[0].current.available,true);
});

test("recorded-to-date excludes missing months and does not look ahead to August", () => {
  const data = mbr.prepare(workbookSnapshot());
  const model = mbr.workbookModel(data,{month:"2026-05"});
  assert.equal(model.history.total,mbr.decimalToUnits("7320"));
  assert.deepEqual(model.accounts[0].missingMonths,["2026-01","2026-02","2026-03"]);
  assert.equal(model.rows.some(row => data.dimensions.month[row.ids[0]]==="2026-08"),false);
  assert.deepEqual(model.chartMonths,["2026-04","2026-05"]);
});

test("source-price savings subtract unused GBP costs exactly and utilization is explicitly cost-weighted", () => {
  const model = mbr.workbookModel(mbr.prepare(workbookSnapshot()),{month:"2026-05"});
  assert.equal(model.current.grossSavings,mbr.decimalToUnits("30"));
  assert.equal(model.current.unused,mbr.decimalToUnits("10"));
  assert.equal(model.current.savings,mbr.decimalToUnits("20"));
  const ri = mbr.savingsTable(model).find(row => row.name==="Reservation"&&row.enrollment==="ExampleUS");
  assert.equal(ri.utilization,"90.9%");
  assert.equal(ri.change,"n/a");
});

test("remaining-year scenarios use calendar days while full-year figures remain unknown without past data", () => {
  const model = mbr.workbookModel(mbr.prepare(workbookSnapshot()),{month:"2026-05"});
  const scenario = mbr.forecastModel(model);
  assert.equal(scenario.futureMonths.length,7);
  assert.equal(scenario.remaining.cost,mbr.decimalToUnits("25680"));
  assert.equal(scenario.accounts.find(row=>row.name==="ExampleUS").remaining.cost,mbr.decimalToUnits("21400"));
  assert.equal(scenario.full.cost,null);
  assert.equal(scenario.covered.cost,mbr.decimalToUnits("33000"));
  for (const account of scenario.accounts) assert.equal(account.projected.cost.reduce((sum,row)=>sum+row.amount,0n),account.remaining.cost);
});

test("explicit earlier amounts complete a hypothetical year without changing recorded spend", () => {
  const data = mbr.prepare(workbookSnapshot());
  const model = mbr.workbookModel(data,{month:"2026-05"});
  const scenario = mbr.forecastModel(model,{change:"0",actuals:{ExampleUS:{cost:"9000"},ExampleUK:{cost:"1800"}}});
  assert.equal(scenario.full.cost,mbr.decimalToUnits("43800"));
  assert.equal(scenario.accounts.find(row=>row.name==="ExampleUS").full.cost,mbr.decimalToUnits("36500"));
  assert.equal(model.history.total,mbr.decimalToUnits("7320"));
  assert.equal(scenario.full.eligible,null);
  const reduced = mbr.forecastModel(model,{change:"-10",actuals:{}});
  assert.equal(reduced.remaining.cost,mbr.decimalToUnits("23112"));
});

test("a missing selected month is never projected using an older enrollment month", () => {
  const scenario = mbr.forecastModel(mbr.workbookModel(mbr.prepare(workbookSnapshot()),{month:"2026-08"}));
  assert.equal(scenario.accounts.find(row=>row.name==="ExampleUK").remaining.cost,null);
  assert.equal(scenario.remaining.cost,null);
});

test("incomplete pricing blocks full savings projections while known estimates remain explicitly partial", () => {
  const raw = workbookSnapshot();
  raw.calculations.rows[3][2]=0;
  raw.calculations.rows[3][3]=1;
  const model = mbr.workbookModel(mbr.prepare(raw),{month:"2026-05"});
  assert.equal(model.current.estimateMissing,1);
  const scenario = mbr.forecastModel(model);
  assert.equal(scenario.remaining.savings,null);
  assert.notEqual(scenario.knownRemainingSavings,null);
  assert.equal(scenario.remaining.cost,mbr.decimalToUnits("25680"));
});

test("empty, zero and invalid scenario amounts are different, signed actuals stay signed", () => {
  assert.equal(mbr.assumptionMoney("","test"),null);
  assert.equal(mbr.assumptionMoney("0","test"),0n);
  assert.equal(mbr.assumptionMoney("-12.50","test"),mbr.decimalToUnits("-12.50"));
  assert.throws(()=>mbr.assumptionMoney("not money","test"),/decimal digits/);
  assert.throws(()=>mbr.assumptionMoney("1e6","test"),/decimal digits/);
  assert.throws(()=>mbr.paceBasisPoints("-100.01"),/between/);
  assert.equal(mbr.paceBasisPoints("-100"),-10000n);
  assert.throws(()=>mbr.paceBasisPoints("abc"),/percentage/);
});

test("calendar calculations handle leap years and year boundaries", () => {
  assert.equal(mbr.daysInMonth("2028-02"),29);
  assert.equal(mbr.daysInMonth("2026-02"),28);
  assert.equal(mbr.addMonth("2026-01",-1),"2025-12");
  assert.deepEqual(mbr.monthRange("2026-12","2027-02"),["2026-12","2027-01","2027-02"]);
});

test("conflicting credit pool inputs are not guessed or added twice", () => {
  const raw = workbookSnapshot();
  raw.meta.sources[1].credit_inputs=[{enrollment:"ExampleUS",amount:String(mbr.decimalToUnits("99999"))}];
  const data = mbr.prepare(raw);
  assert.equal(mbr.workbookModel(data,{month:"2026-05"}).accounts.find(row=>row.name==="ExampleUS").pool.amount,null);
  assert.equal(mbr.workbookModel(data,{month:"2026-05",source:"0"}).accounts.find(row=>row.name==="ExampleUS").pool.amount,mbr.decimalToUnits("100000"));
});

test("Advisor summary keeps snapshots separate, fixes production scope and excludes categories outside the workbook five", () => {
  const raw = workbookSnapshot();
  const rec = (row,enrollment,service,value,production="yes") => ({row,enrollment,service,annual_gbp:value===null?null:String(mbr.decimalToUnits(value)),production,impact:"High",recommendation:"Review",subscription:"sub",subscription_id:"id",resource:"resource"});
  raw.advisor_snapshots[0].rows=[rec(2,"ExampleUS","virtual machine","100"),rec(3,"ExampleUK","SQL PaaS DB","50"),rec(4,"ExampleUS","virtual machine","10","no")];
  raw.advisor_snapshots[1].rows=[rec(2,"ExampleUS","virtual machine","200"),rec(3,"ExampleUS","Database for MySQL","300"),rec(4,"ExampleUS","virtual machine",null)];
  const data = mbr.prepare(raw);
  assert.equal(mbr.advisorPotential(data,"0").total,mbr.decimalToUnits("150"));
  assert.equal(mbr.advisorPotential(data,"1").total,mbr.decimalToUnits("200"));
  assert.equal(mbr.advisorPotential(data,"1").missing,1);
  assert.equal(mbr.advisorPotential(data,"1").groups.find(row=>row.service==="Microsoft Fabric").total,null);
});

test("every workbook view has full-scope CSV output with unknown amounts empty", () => {
  const model = mbr.workbookModel(mbr.prepare(workbookSnapshot()),{month:"2026-05"});
  assert.equal(mbr.workbookCsv(model,"summary").trim().split("\r\n").length,23);
  assert.equal(mbr.workbookCsv(model,"trend").trim().split("\r\n").length,5);
  const credits = mbr.workbookCsv(model,"credits");
  assert.equal(credits.trim().split("\r\n").length,11);
  assert.ok(credits.includes("2026-01,ExampleUK,no,,,"));
  assert.ok(mbr.workbookCsv(model,"savings").includes("Cost-weighted utilization"));
  const csv = mbr.workbookCsv(model,"forecast",mbr.forecastModel(model));
  assert.equal(csv.trim().split("\r\n").length,7);
  assert.ok(csv.includes("Missing past months"));
  assert.ok(csv.includes(",,,,")||csv.includes(",,"));
});

test("replicated report text contains source identities, data gaps, assumptions and commentary", () => {
  const model = mbr.workbookModel(mbr.prepare(workbookSnapshot()),{month:"2026-05"});
  const text = mbr.workbookReportText(model,"forecast","Action: review the unknown months",mbr.forecastModel(model));
  assert.ok(text.includes("May workbook"));
  assert.ok(text.includes("August workbook"));
  assert.ok(text.includes("missing earlier months 2026-01, 2026-02, 2026-03"));
  assert.ok(text.includes("Action: review the unknown months"));
  assert.ok(text.includes("not certified realized savings"));
});

test("publisher filtering uses the source PublisherType rather than the faulty Usage Type label", () => {
  const raw = workbookSnapshot();
  raw.row_publishers[2]="Marketplace";
  const data = mbr.prepare(raw);
  const model = mbr.workbookModel(data,{month:"2026-05",publisher:"Azure"});
  assert.equal(model.current.total,mbr.decimalToUnits("730"));
  const rows = mbr.filterSpend(data,{month:"2026-05",publisher:"Marketplace"});
  assert.equal(mbr.summarizeSpend(data,rows).total,mbr.decimalToUnits("2990"));
  const csv = mbr.spendCsv(data,rows);
  assert.ok(csv.includes("PublisherType,Source workbook"));
  assert.ok(csv.includes(",Marketplace,May workbook"));
});

test("view exports use the same complete summary, cumulative and snapshot-change measures as the screens", () => {
  const raw = workbookSnapshot();
  const rec = value => ({row:2,enrollment:"ExampleUS",service:"virtual machine",annual_gbp:String(mbr.decimalToUnits(value)),production:"yes",impact:"High",recommendation:"Review",subscription:"sub",subscription_id:"id",resource:"resource"});
  raw.advisor_snapshots[0].rows=[rec("100")];
  raw.advisor_snapshots[1].rows=[rec("200")];
  const data = mbr.prepare(raw), model = mbr.workbookModel(data,{month:"2026-05"});
  const measures = mbr.summaryMeasures(model,"0");
  assert.equal(measures.length,11);
  assert.equal(measures.find(row=>row.label.startsWith("Annual Advisor")).cells.find(cell=>cell.enrollment==="ExampleUS").amount,mbr.decimalToUnits("100"));
  const before = mbr.workbookCsv(model,"summary",null,"0");
  const after = mbr.workbookCsv(model,"summary",null,"1");
  assert.ok(before.includes("Annual Advisor potential (separate snapshot),ExampleUS,2026-05,100,"));
  assert.ok(after.includes("Annual Advisor potential (separate snapshot),ExampleUS,2026-05,200,"));
  assert.ok(before.includes("Marketplace publisher"));
  assert.ok(before.includes("Filter scope"));
  const credits = mbr.creditTimeline(model);
  assert.equal(credits.find(row=>row.month==="2026-01"&&row.enrollment==="ExampleUK").cumulative,null);
  assert.equal(credits.find(row=>row.month==="2026-05"&&row.enrollment==="ExampleUK").cumulative,mbr.decimalToUnits("1220"));
  const csv = mbr.workbookCsv(model,"credits");
  assert.ok(csv.includes("Recorded cumulative eligible cost GBP"));
  const comparison = mbr.potentialComparison(data,"1");
  assert.equal(comparison.rows.find(row=>row.service==="Virtual Machines"&&row.enrollment==="ExampleUS").change,mbr.decimalToUnits("100"));
});

function twoMonthSnapshot() {
  const raw = workbookSnapshot();
  const keep = raw.rows.map((row,index)=>row[0]>0?index:null).filter(index=>index!==null);
  raw.rows = keep.map(index=>{const row=[...raw.rows[index]];row[0]-=1;return row;});
  raw.row_sources=keep.map(index=>raw.row_sources[index]);
  raw.row_publishers=keep.map(index=>raw.row_publishers[index]);
  raw.calculations.rows=keep.map(index=>raw.calculations.rows[index]);
  raw.dimensions.month=["2026-05","2026-08"];
  raw.retention={limit:2,months:["2026-05","2026-08"],kind:"newest-available-billing-months"};
  return raw;
}

test("upload mode defaults to the newest month and names a nonconsecutive retained comparison", () => {
  const data=mbr.prepare(twoMonthSnapshot());
  assert.equal(mbr.defaultReportingMonth(data),"2026-08");
  const model=mbr.workbookModel(data,{month:"2026-08"});
  assert.equal(model.previousMonth,"2026-05");
  assert.equal(model.adjacentComparison,false);
  assert.equal(model.accounts.find(account=>account.name==="ExampleUS").change,mbr.decimalToUnits("6200"));
  assert.equal(model.accounts.find(account=>account.name==="ExampleUK").change,null);
  const csv=mbr.workbookCsv(model,"summary");
  assert.ok(csv.includes("Comparison month,Consecutive months"));
  assert.ok(csv.includes(",2026-05,no\r\n"));
});

test("retention schema fails closed instead of hiding a third stored month", () => {
  const raw=workbookSnapshot();
  raw.retention={limit:2,months:["2026-05","2026-08"]};
  assert.throws(()=>mbr.prepare(raw),/retention/);
});

test("unknown eligibility does not become zero in credit reports or run-rate scenarios", () => {
  const raw=twoMonthSnapshot();
  raw.dimensions.eligible.push("(not supplied)");
  raw.rows.at(-1)[8]=raw.dimensions.eligible.length-1;
  const data=mbr.prepare(raw),model=mbr.workbookModel(data,{month:"2026-08"});
  const ref=model.accounts.find(account=>account.name==="ExampleUS");
  assert.equal(ref.current.unknownEligibility,1);
  assert.equal(mbr.forecastModel(model).accounts.find(account=>account.name==="ExampleUS").remaining.eligible,null);
  assert.equal(mbr.summaryMeasures(model).find(measure=>measure.label==="Monthly credit-eligible cost proxy").cells.find(cell=>cell.enrollment==="ExampleUS").amount,null);
  assert.equal(mbr.creditTimeline(model).find(cell=>cell.month==="2026-08"&&cell.enrollment==="ExampleUS").cumulative,null);
});

test("a wholly unpriced estimate is unavailable, not a success-shaped zero", () => {
  const raw=twoMonthSnapshot();
  raw.rows.at(-1)[5]=raw.dimensions.pricing.indexOf("Reservation");
  raw.calculations.rows.at(-1)[2]=0;
  raw.calculations.rows.at(-1)[3]=1;
  const model=mbr.workbookModel(mbr.prepare(raw),{month:"2026-08",enrollment:"ExampleUS"});
  assert.equal(model.current.savings,null);
  assert.equal(mbr.forecastModel(model).knownRemainingSavings,null);
});

test("native money and original charge type survive grouped usage exports",()=>{
  const raw=twoMonthSnapshot();
  raw.row_charge_types=raw.rows.map(()=>"Usage");
  raw.row_native_currencies=raw.rows.map(()=>"USD");
  raw.row_native_costs=raw.rows.map(()=>String(mbr.decimalToUnits("-1.25")));
  const data=mbr.prepare(raw);
  const csv=mbr.spendCsv(data,data.rows);
  assert.ok(csv.includes("Original charge type,Native currency,Native cost"));
  assert.ok(csv.includes(",Usage,USD,-1.25\r\n"));
});

function nativeSnapshot(){
  const raw=workbookSnapshot();
  raw.row_native_currencies=raw.rows.map(row=>raw.dimensions.enrollment[row[1]]==="ExampleUS"?"USD":"GBP");
  raw.row_native_costs=raw.rows.map((row,index)=>String(BigInt(row[10])*(raw.row_native_currencies[index]==="USD"?2n:1n)));
  raw.usd_fx={version:1,target:"USD",rows:["2026-04","2026-05","2026-07","2026-08"].map(month=>({
    from:"GBP",to:"USD",month,rate:"1.5",rate_month:month,estimated:false,source:"Hand-computed fixture",method:"manual",
  }))};
  return raw;
}

function currencySnapshot() {
  const raw = nativeSnapshot();
  raw.audit = {warnings:[], reconciliations:[], excel_errors:[]};
  raw.native_calculations = {version:1, rows:raw.calculations.rows.map(row => [...row])};
  raw.native_calculations.rows[3][0] = String(mbr.decimalToUnits("90"));
  const recommendation = (row, currency, amount, gbp, enrollment = "ExampleUS") => ({
    row, enrollment, service:"virtual machine", production:"yes", impact:"High",
    subscription:"sub-a", subscription_id:"a", resource:"r", recommendation:"Review " + row,
    native_currency:currency, native_annual:amount, annual_gbp:gbp === null ? null : String(mbr.decimalToUnits(gbp)),
    updated:"2026-07-17",
  });
  raw.advisor_snapshots[0].rows = [recommendation(1,"USD","80","400")];
  raw.advisor_snapshots[1].rows = [
    recommendation(1,"USD","100","999"),
    recommendation(2,"EUR","200","150"),
    recommendation(3,"GBP","30","30"),
    recommendation(4,"USD","0","0"),
    recommendation(5,"USD",null,"5"),
    recommendation(6,"GBP","25","25","ExampleUK"),
  ];
  return raw;
}

test("shared reporting context uses native costs and independently retained savings without reverse FX", () => {
  const raw = currencySnapshot(), before = JSON.stringify(raw);
  const data = mbr.reportingData(mbr.prepare(raw),"ExampleUS");
  const model = mbr.workbookModel(data,{month:"2026-05"});
  assert.equal(data.reportingCurrency,"USD");
  assert.deepEqual(model.names,["ExampleUS"]);
  assert.equal(model.current.total,mbr.decimalToUnits("6200"));
  assert.equal(model.current.eligible,mbr.decimalToUnits("6180"));
  assert.equal(model.current.grossSavings,mbr.decimalToUnits("90"));
  assert.equal(model.current.unused,mbr.decimalToUnits("20"));
  assert.equal(model.current.savings,mbr.decimalToUnits("70"));
  assert.equal(mbr.savingsTable(model).find(row=>row.name==="Reservation").current.savings,mbr.decimalToUnits("70"));
  assert.equal(mbr.summaryMeasures(model)[0].cells[0].amount,mbr.decimalToUnits("6200"));
  assert.equal(mbr.forecastModel(model).remaining.cost,mbr.decimalToUnits("42800"));
  assert.equal(JSON.stringify(raw),before);
});

test("ExampleUK remains native GBP and combined conversion requires an explicit alternative", () => {
  const original = mbr.prepare(currencySnapshot());
  const example_uk = mbr.workbookModel(mbr.reportingData(original,"ExampleUK"),{month:"2026-05"});
  assert.equal(example_uk.data.reportingCurrency,"GBP");
  assert.equal(example_uk.current.total,mbr.decimalToUnits("620"));
  assert.deepEqual(example_uk.names,["ExampleUK"]);
  const combined = mbr.workbookModel(mbr.reportingData(original),{month:"2026-05"});
  assert.equal(combined.data.reportingMode,"converted");
  assert.equal(combined.data.reportingCurrency,"USD");
  assert.equal(combined.current.total,mbr.decimalToUnits("7130"));
  assert.deepEqual(combined.names,["ExampleUK","ExampleUS"]);
  assert.throws(()=>mbr.reportingData(example_uk.data),/original prepared source data/);
  assert.throws(()=>mbr.workbookModel(example_uk.data,{month:"2026-05",enrollment:"ExampleUS"}),/currency context/);
});

test("Combined USD keeps original dollars and converts native pounds and savings, not stored GBP totals",()=>{
  const raw=currencySnapshot(),before=JSON.stringify(raw);
  raw.rows[2][10]=String(mbr.decimalToUnits("99999"));
  const original=JSON.stringify(raw),model=mbr.workbookModel(mbr.reportingData(mbr.prepare(raw)),{month:"2026-05"});
  assert.equal(model.current.total,mbr.decimalToUnits("7130"));
  assert.equal(model.accounts.find(account=>account.name==="ExampleUS").current.total,mbr.decimalToUnits("6200"));
  assert.equal(model.accounts.find(account=>account.name==="ExampleUK").current.total,mbr.decimalToUnits("930"));
  assert.equal(model.current.savings,mbr.decimalToUnits("70"));
  assert.equal(model.current.grossSavings,mbr.decimalToUnits("90"));
  assert.equal(model.current.unused,mbr.decimalToUnits("20"));
  assert.equal(JSON.stringify(raw),original);
  assert.notEqual(original,before);
});

test("USD conversion rounds once half-even, preserves signed values, and bounds precision",()=>{
  const rate={rateUnits:mbr.decimalToUnits("0.5")};
  assert.equal(mbr.usdMoney(1n,rate),0n);
  assert.equal(mbr.usdMoney(3n,rate),2n);
  assert.equal(mbr.usdMoney(-3n,rate),-2n);
  assert.equal(mbr.usdMoney(mbr.decimalToUnits("9007199254740993.25"),rate),mbr.decimalToUnits("4503599627370496.625"));
  assert.equal(mbr.usdMoney(100n,null),null);
  assert.throws(()=>mbr.usdMoney(10n**38n,{rateUnits:mbr.decimalToUnits("2")}),/decimal precision/);
});

test("unconvertible currencies are excluded and named, never counted as zero or projected as complete",()=>{
  const raw=currencySnapshot();
  raw.usd_fx.rows=[];
  const data=mbr.reportingData(mbr.prepare(raw)),model=mbr.workbookModel(data,{month:"2026-05"});
  assert.equal(model.current.total,mbr.decimalToUnits("6200"));
  assert.equal(model.completeCurrentScope,false);
  assert.equal(model.accounts.find(account=>account.name==="ExampleUK").current.available,false);
  assert.equal(mbr.forecastModel(model).remaining.cost,null);
  assert.match(mbr.currencyEvidence(data),/ExampleUK 2026-05: No saved GBP-to-USD/);
  assert.match(mbr.spendCsv(data,model.currentRows),/Excluded, not zero/);
});

test("a missing original dollar amount never reverses GBP and makes that enrollment period unavailable",()=>{
  const raw=currencySnapshot();
  raw.row_native_costs[2]=null;
  const data=mbr.reportingData(mbr.prepare(raw)),model=mbr.workbookModel(data,{month:"2026-05"});
  assert.equal(model.accounts.find(account=>account.name==="ExampleUS").current.available,false);
  assert.equal(model.completeCurrentScope,false);
  assert.equal(data.fxExcluded.find(row=>row.ids[0]===1).fxReason,"Original native amount unavailable");
  assert.equal(mbr.forecastModel(model).full.cost,null);
});

test("Combined pools convert once at the reporting month while original GBP references remain explicit",()=>{
  const data=mbr.reportingData(mbr.prepare(currencySnapshot())),model=mbr.workbookModel(data,{month:"2026-05"});
  const pool=model.accounts.find(account=>account.name==="ExampleUS").pool;
  assert.equal(pool.currency,"USD");
  assert.equal(pool.amount,mbr.decimalToUnits("150000"));
  assert.deepEqual(pool.references,[{currency:"GBP",amount:mbr.decimalToUnits("100000")}]);
  assert.equal(pool.conversion.month,"2026-05");
  assert.match(pool.reason,/selected reporting month/);
  const raw=currencySnapshot();
  raw.usd_fx.rows=raw.usd_fx.rows.filter(row=>row.month!=="2026-05");
  const missing=mbr.workbookModel(mbr.reportingData(mbr.prepare(raw)),{month:"2026-05"});
  assert.equal(missing.accounts.find(account=>account.name==="ExampleUS").pool.amount,null);
});

test("Combined Advisor uses original estimates and source dates, with labelled GBP-only inputs",()=>{
  const raw=currencySnapshot(),data=mbr.reportingData(mbr.prepare(raw));
  const rows=mbr.filterAdvisor(data.advisorSnapshots[1].rows,{enrollment:"ExampleUS"});
  const metrics=mbr.summarizeAdvisor(rows);
  assert.equal(metrics.total,mbr.decimalToUnits("152.5"));
  assert.equal(metrics.valued,4);
  assert.equal(metrics.missing,1);
  assert.equal(mbr.advisorAmount(rows.find(row=>row.row===1)),String(mbr.decimalToUnits("100")));
  const pounds=rows.find(row=>row.row===3);
  assert.equal(mbr.advisorAmount(pounds),String(mbr.decimalToUnits("45")));
  assert.equal(pounds.estimate_fx.month,"2026-07");
  const reference=rows.find(row=>row.row===5);
  assert.equal(mbr.advisorAmount(reference),String(mbr.decimalToUnits("7.5")));
  assert.match(reference.estimate_basis,/Explicit source GBP/);
  raw.advisor_snapshots[1].rows[2].updated=null;
  const undated=mbr.reportingData(mbr.prepare(raw)).advisorSnapshots[1].rows.find(row=>row.row===3);
  assert.equal(mbr.advisorAmount(undated),null);
  assert.match(undated.estimate_note,/No dated saved GBP-to-USD/);
  raw.advisor_snapshots[1].rows.push({...raw.advisor_snapshots[1].rows[0],row:7,
    native_annual:null,native_currency:"(not supplied)",annual_gbp:null});
  const missing=mbr.reportingData(mbr.prepare(raw)).advisorSnapshots[1].rows.find(row=>row.row===7);
  assert.equal(mbr.advisorAmount(missing),null);
  assert.equal(missing.estimate_basis,"No supplied annual monetary estimate");
  assert.match(mbr.advisorCsv([missing],"snapshot","USD"),/No supplied annual monetary estimate/);
});

test("USD exports retain exact money, native currencies and FX/source lineage",()=>{
  const data=mbr.reportingData(mbr.prepare(currencySnapshot())),model=mbr.workbookModel(data,{month:"2026-05"});
  const spend=mbr.spendCsv(data,model.currentRows);
  assert.match(spend,/Cost USD,Source rows/);
  assert.match(spend,/USD FX rate,Saved FX month,Estimated FX,Conversion definition/);
  assert.match(spend,/,GBP,620,1.5,2026-05,no,/);
  assert.match(mbr.workbookCsv(model,"forecast",mbr.forecastModel(model)),/Combined USD from original native amounts/);
  const advisor=mbr.advisorCsv(data.advisorSnapshots[1].rows,"snapshot","USD");
  assert.match(advisor,/Annual estimate USD/);
  assert.match(advisor,/USD estimate basis,USD FX rate,Saved FX month,USD conversion note/);
  assert.match(advisor,/,45,30,GBP,/);
});

test("USD rate evidence is validated, including duplicate keys and malformed months/rates",()=>{
  const raw=currencySnapshot();
  raw.usd_fx.rows.push({...raw.usd_fx.rows[0]});
  assert.throws(()=>mbr.prepare(raw),/ambiguous USD FX/);
  raw.usd_fx.rows.pop();
  raw.usd_fx.rows[0].rate="0";
  assert.throws(()=>mbr.prepare(raw),/USD FX rate/);
  raw.usd_fx.rows[0].rate="1.5";
  raw.usd_fx.rows[0].month="2026-13";
  assert.throws(()=>mbr.prepare(raw),/USD FX evidence row/);
});

test("native credit proxies never divide USD eligible spend by GBP-only pools", () => {
  const model = mbr.workbookModel(mbr.reportingData(mbr.prepare(currencySnapshot()),"ExampleUS"),{month:"2026-05"});
  const pool = model.accounts[0].pool;
  assert.equal(pool.amount,null);
  assert.deepEqual(pool.references,[{currency:"GBP",amount:mbr.decimalToUnits("100000")}]);
  assert.match(pool.reason,/no USD pool/);
  const credit = mbr.workbookCsv(model,"credits");
  const current = credit.split("\r\n").find(line=>line.startsWith("2026-05,ExampleUS,yes"));
  assert.deepEqual(current.split(",").slice(0,6),["2026-05","ExampleUS","yes","6180","12180",""]);
  assert.match(credit,/Original pool currency,Original pool amount,Pool availability note/);
  assert.match(credit,/GBP,100000,Source pool is in GBP/);
});

test("Advisor native estimates respect currency, genuine zero, missing amounts and separate snapshot dates", () => {
  const data = mbr.reportingData(mbr.prepare(currencySnapshot()),"ExampleUS");
  const rows = mbr.filterAdvisor(data.advisorSnapshots[1].rows,{enrollment:"ExampleUS"});
  const metrics = mbr.summarizeAdvisor(rows);
  assert.equal(metrics.total,mbr.decimalToUnits("100"));
  assert.equal(metrics.valued,2);
  assert.equal(metrics.missing,3);
  const comparison = mbr.potentialComparison(data,"1",{enrollment:"ExampleUS"});
  assert.equal(comparison.current.total,mbr.decimalToUnits("100"));
  assert.equal(comparison.previous.total,mbr.decimalToUnits("80"));
  assert.equal(comparison.rows[0].change,mbr.decimalToUnits("20"));
  assert.equal(data.sources[1].advisor_date_end,"2026-07-17");
  assert.equal(rows.find(row=>row.row===1).annual_gbp,String(mbr.decimalToUnits("999")));
  const csv = mbr.advisorCsv(rows,"Advisor snapshot","USD");
  assert.match(csv,/Annual estimate USD,Original annual estimate,Original currency/);
  assert.match(csv,/,100,100,USD,/);
  assert.match(csv,/,,200,EUR,/);
  assert.match(csv,/,0,0,USD,/);
  assert.throws(()=>mbr.summarizeAdvisor([
    {...rows[0],annual_amount:"1",estimate_currency:"USD"},
    {...rows[0],annual_amount:"1",estimate_currency:"GBP"},
  ]),/different currencies/);
});

test("all reporting exports and text use the same selected native calculation model", () => {
  const data = mbr.reportingData(mbr.prepare(currencySnapshot()),"ExampleUS");
  const model = mbr.workbookModel(data,{month:"2026-05"});
  const forecast = mbr.forecastModel(model);
  for (const view of ["summary","trend","credits","savings","forecast"]) {
    const csv = mbr.workbookCsv(model,view,forecast);
    assert.match(csv.split("\r\n")[0],/USD/);
    assert.doesNotMatch(csv.split("\r\n")[0],/GBP/);
    const report = mbr.workbookReportText(model,view,"Notes",forecast);
    assert.match(report,/reporting currency: USD; native source amounts, no FX/);
  }
  const spend = mbr.spendCsv(data,model.currentRows);
  assert.match(spend,/Cost USD,Source rows/);
  assert.match(mbr.reportText(data.snapshot,"spend",model.filters,model.current,[],"","USD"),/Recorded spend \(USD\): \$6,200.00/);
  assert.equal(mbr.forecastModel(model,{change:"0",actuals:{ExampleUS:{cost:"1000"}}}).full.cost,mbr.decimalToUnits("56000"));
});

test("missing native calculation evidence never falls back to converted GBP savings", () => {
  const raw = currencySnapshot();
  delete raw.native_calculations;
  const model = mbr.workbookModel(mbr.reportingData(mbr.prepare(raw),"ExampleUS"),{month:"2026-05"});
  assert.equal(model.current.total,mbr.decimalToUnits("6200"));
  assert.equal(model.current.savings,null);
  assert.equal(model.current.estimateMissing,3);
  assert.equal(model.current.grossSavings,null);
  assert.equal(model.current.tierSavings,null);
  assert.equal(mbr.forecastModel(model).remaining.savings,null);
  raw.row_native_costs[0] = null;
  assert.throws(()=>mbr.reportingData(mbr.prepare(raw),"ExampleUS"),/no reverse FX/);
});

test("ambiguous native currencies and malformed calculation sidecars fail explicitly", () => {
  const raw = currencySnapshot();
  raw.row_native_currencies[0] = "GBP";
  assert.throws(()=>mbr.reportingData(mbr.prepare(raw),"ExampleUS"),/one explicit currency/);
  const bad = currencySnapshot();
  bad.native_calculations.rows[0] = ["0","0",2,0];
  assert.throws(()=>mbr.prepare(bad),/calculation row/);
  bad.native_calculations.rows = [];
  assert.throws(()=>mbr.prepare(bad),/native calculation schema/);
});

function driverSnapshot(extra=[]) {
  const raw=workbookSnapshot();
  const records=[
    ["2026-04","Up","100","rA"],["2026-05","Up","150","rA"],
    ["2026-04","Down","200","rB"],["2026-05","Down","100","rB"],
    ["2026-05","New","50","rA"],["2026-04","Removed","25","rB"],
    ["2026-04","Refund","-10","rC"],["2026-05","Refund","-20","rC"],
    ["2026-04","Zero","0","rC"],["2026-05","Zero","0","rC"],
    ["2026-04","Tie A","10","rC"],["2026-05","Tie A","20","rC"],
    ["2026-04","Tie B","20","rC"],["2026-05","Tie B","30","rC"],...extra,
  ];
  const unpacked=records.map(row=>[row[0],"ExampleUS",row[1],"sub",row[3],"OnDemand","Greenfield","Used","Yes","Azure"]);
  raw.dimensions=Object.fromEntries(raw.dimension_order.map((name,index)=>[name,[...new Set(unpacked.map(row=>row[index]))].sort()]));
  raw.rows=unpacked.map((row,index)=>[...row.map((value,column)=>raw.dimensions[raw.dimension_order[column]].indexOf(value)),String(mbr.decimalToUnits(records[index][2])/2n),1]);
  raw.row_sources=records.map(()=>0);
  raw.row_publishers=records.map(row=>row[4]||"Azure");
  raw.row_native_costs=records.map(row=>String(mbr.decimalToUnits(row[2])));
  raw.row_native_currencies=records.map(()=>"USD");
  raw.calculations={version:1,rows:records.map(()=>["0","0",0,0])};
  raw.native_calculations=structuredClone(raw.calculations);
  raw.audit={warnings:[],reconciliations:[],excel_errors:[]};
  return raw;
}

function drivers(raw=driverSnapshot(),filters={}) {
  return mbr.driverModel(mbr.workbookModel(mbr.reportingData(mbr.prepare(raw),"ExampleUS"),{month:"2026-05",...filters}));
}

test("workbook driver pivots and chart Other reconcile both signed source periods",()=>{
  const model=drivers();
  assert.equal(model.current.total,mbr.decimalToUnits("330"));
  assert.equal(model.previous.total,mbr.decimalToUnits("345"));
  for(const rows of [model.regions,model.services]){
    assert.equal(rows.reduce((sum,row)=>sum+row.current,0n),model.current.total);
    assert.equal(rows.reduce((sum,row)=>sum+row.previous,0n),model.previous.total);
    assert.equal(rows.reduce((sum,row)=>sum+row.change,0n),mbr.decimalToUnits("-15"));
    assert.equal(rows.reduce((sum,row)=>sum+row.count,0),7);
    for(const limit of [1,2,7]){
      const chart=mbr.driverChartRows(rows,limit);
      assert.equal(chart.reduce((sum,row)=>sum+row.current,0n),model.current.total);
      assert.equal(chart.reduce((sum,row)=>sum+row.previous,0n),model.previous.total);
    }
  }
  assert.equal(mbr.driverChartRows(model.services,7).at(-1).current,mbr.decimalToUnits("-20"));
  assert.throws(()=>mbr.driverChartRows(model.services,0),/chart limit/);
});

test("driver movers use the complete comparison and exact competition ranks, excluding invalid percent bases",()=>{
  const model=drivers(),byName=name=>model.services.find(row=>row.name===name);
  assert.deepEqual(model.movers.amountIncrease.map(row=>row.name),["New","Up","Tie A"]);
  assert.deepEqual(model.movers.amountDecrease.map(row=>row.name),["Down","Removed","Refund"]);
  assert.deepEqual(model.movers.percentageIncrease.map(row=>row.name),["Tie A","Tie B","Up"]);
  assert.deepEqual(model.movers.percentageDecrease.map(row=>row.name),["Removed","Down"]);
  assert.equal(byName("New").amountRank,1);
  assert.equal(byName("Up").amountRank,1);
  assert.equal(byName("Tie A").amountRank,3);
  assert.equal(byName("Tie B").percentageRank,2);
  for(const name of ["New","Refund","Zero"]){
    assert.equal(byName(name).percentageRank,null);
    assert.equal(byName(name).percentage,"n/a");
  }
  assert.equal(byName("Removed").current,0n);
  assert.equal(byName("Removed").percentage,"-100.0%");
  assert.equal(byName("Refund").change,mbr.decimalToUnits("-10"));
});

test("driver ranking compares exact ratios even when displayed percentages round to the same value",()=>{
  const model=drivers(driverSnapshot([
    ["2026-04","Near A","10000000000000000000","rC"],["2026-05","Near A","10000000000000000001","rC"],
    ["2026-04","Near B","9999999999999999999","rC"],["2026-05","Near B","10000000000000000000","rC"],
  ]));
  const a=model.services.find(row=>row.name==="Near A"),b=model.services.find(row=>row.name==="Near B");
  assert.equal(a.percentage,b.percentage);
  assert.equal(a.percentage,"0.0%");
  assert.ok(b.percentageRank<a.percentageRank);
});

test("missing driver periods are unavailable while genuine filtered zero activity stays zero",()=>{
  const raw=driverSnapshot();
  const selected=raw.rows.map((_row,index)=>index).filter(index=>raw.dimensions.month[raw.rows[index][0]]==="2026-05");
  for(const key of ["rows","row_sources","row_publishers","row_native_costs","row_native_currencies"])raw[key]=selected.map(index=>raw[key][index]);
  raw.calculations.rows=selected.map(index=>raw.calculations.rows[index]);
  raw.native_calculations.rows=selected.map(index=>raw.native_calculations.rows[index]);
  const missing=drivers(raw);
  assert.equal(missing.current.total,mbr.decimalToUnits("330"));
  assert.equal(missing.previous.available,false);
  assert.ok(missing.services.every(row=>row.previous===null&&row.change===null&&row.amountRank===null));
  assert.ok(Object.values(missing.movers).every(rows=>rows.length===0));
  const zero=drivers(driverSnapshot(),{service:"not observed"});
  assert.equal(zero.current.available,true);
  assert.equal(zero.current.total,0n);
  assert.equal(zero.previous.available,true);
  assert.equal(zero.services.length,0);
});

test("driver publishers and native/converted contexts do not leak into each other",()=>{
  const raw=driverSnapshot([["2026-04","Market","50","rA","Marketplace"],["2026-05","Market","200","rA","Marketplace"]]);
  assert.equal(drivers(raw,{publisher:"Azure"}).current.total,mbr.decimalToUnits("330"));
  assert.equal(drivers(raw,{publisher:"Marketplace"}).current.total,mbr.decimalToUnits("200"));
  const combined=mbr.driverModel(mbr.workbookModel(mbr.reportingData(mbr.prepare(raw)),{month:"2026-05",publisher:"Azure",enrollment:"ExampleUS"}));
  assert.equal(combined.current.total,mbr.decimalToUnits("330"));
  assert.equal(combined.model.data.reportingCurrency,"USD");
});

test("driver exports include every region/service, native amounts, signed numeric percentages and selected ordering",()=>{
  const model=drivers().model;
  const csv=mbr.driversCsv(model,{dimension:"region",sort:"decrease"});
  assert.equal(csv.trim().split("\r\n").length,12);
  assert.ok(csv.split("\r\n")[1].startsWith("region,rB,2026-04,2026-05,USD,225,100,-125,-55."));
  assert.match(csv,/service,Removed,2026-04,2026-05,USD,25,0,-25,-100,/);
  assert.match(csv,/service,New,2026-04,2026-05,USD,0,50,50,,/);
  assert.equal(mbr.workbookCsv(model,"drivers",null,undefined,{dimension:"region",sort:"decrease"}),csv);
  assert.match(mbr.workbookReportText(model,"drivers","Comment",null,undefined,{dimension:"region",sort:"decrease"}),/region,rB,2026-04/);
});

function subscriptionModel(extra=[],filters={}) {
  const raw=driverSnapshot(extra);
  raw.dimensions.subscription=[...raw.dimensions.service];
  raw.rows.forEach(row=>{row[3]=row[2];});
  return mbr.workbookModel(mbr.reportingData(mbr.prepare(raw),"ExampleUS"),{month:"2026-05",...filters});
}

test("subscription changes reconcile both source months and keep new, removed and invalid bases explicit",()=>{
  const model=subscriptionModel(),rows=mbr.subscriptionChanges(model),byName=name=>rows.find(row=>row.name===name);
  assert.equal(rows.reduce((sum,row)=>sum+row.current,0n),mbr.decimalToUnits("330"));
  assert.equal(rows.reduce((sum,row)=>sum+row.previous,0n),mbr.decimalToUnits("345"));
  assert.equal(rows.reduce((sum,row)=>sum+row.change,0n),mbr.decimalToUnits("-15"));
  assert.equal(rows.reduce((sum,row)=>sum+row.count,0),7);
  assert.equal(rows.reduce((sum,row)=>sum+row.previousCount,0),7);
  assert.equal(byName("Up").percentage,"50.0%");
  assert.equal(byName("Down").percentage,"-50.0%");
  assert.equal(byName("Removed").percentage,"-100.0%");
  assert.equal(byName("Removed").status,"No current activity");
  assert.equal(byName("New").status,"New subscription name");
  assert.equal(byName("Zero").status,"Zero comparison base");
  assert.equal(byName("Refund").status,"Negative comparison base");
  for(const name of ["New","Zero","Refund"])assert.equal(byName(name).percentage,"n/a");
  assert.equal(byName("Refund").change,mbr.decimalToUnits("-10"));
});

test("same subscription names in different enrollments stay separate in Combined USD and native views",()=>{
  const data=mbr.prepare(currencySnapshot());
  const model=mbr.workbookModel(mbr.reportingData(data),{month:"2026-05"});
  const rows=mbr.subscriptionChanges(model);
  assert.equal(rows.length,2);
  const ref=rows.find(row=>row.enrollment==="ExampleUS"),example_uk=rows.find(row=>row.enrollment==="ExampleUK");
  assert.equal(ref.name,example_uk.name);
  assert.equal(ref.current,mbr.decimalToUnits("6200"));
  assert.equal(ref.previous,mbr.decimalToUnits("6000"));
  assert.equal(example_uk.current,mbr.decimalToUnits("930"));
  assert.equal(example_uk.previous,mbr.decimalToUnits("900"));
  assert.equal(ref.percentage,"3.3%");
  assert.equal(example_uk.percentage,"3.3%");
  assert.equal(rows.reduce((sum,row)=>sum+row.current,0n),model.current.total);
  const native=mbr.workbookModel(mbr.reportingData(data,"ExampleUK"),{month:"2026-05"});
  assert.equal(mbr.subscriptionChanges(native)[0].current,mbr.decimalToUnits("620"));
  assert.equal(native.data.reportingCurrency,"GBP");
});

test("subscription percentage ordering uses exact ratios, with unavailable percentages last in either direction",()=>{
  const model=subscriptionModel([
    ["2026-04","Near A","10000000000000000000","rC"],["2026-05","Near A","10000000000000000001","rC"],
    ["2026-04","Near B","9999999999999999999","rC"],["2026-05","Near B","10000000000000000000","rC"],
  ]);
  const up=mbr.subscriptionChanges(model,"percentage-increase"),down=mbr.subscriptionChanges(model,"percentage-decrease");
  assert.equal(up.find(row=>row.name==="Near A").percentage,up.find(row=>row.name==="Near B").percentage);
  assert.ok(up.findIndex(row=>row.name==="Near B")<up.findIndex(row=>row.name==="Near A"));
  assert.ok(down.findIndex(row=>row.name==="Near A")<down.findIndex(row=>row.name==="Near B"));
  assert.equal(down[0].name,"Removed");
  assert.equal(up[0].name,"Tie A");
  assert.ok(up.slice(-3).every(row=>row.percentage==="n/a"));
  assert.ok(down.slice(-3).every(row=>row.percentage==="n/a"));
  assert.equal(mbr.subscriptionChanges(model,"decrease")[0].name,"Down");
  assert.throws(()=>mbr.subscriptionChanges(model,"unknown"),/Unsupported subscription/);
});

test("subscription filters apply to both periods and missing enrollment months never become a zero baseline",()=>{
  const filtered=mbr.subscriptionChanges(subscriptionModel([],{query:"up"}));
  assert.equal(filtered.length,1);
  assert.equal(filtered[0].name,"Up");
  assert.equal(filtered[0].percentage,"50.0%");
  assert.deepEqual(mbr.subscriptionChanges(subscriptionModel([],{query:"no matching subscription"})),[]);
  const missing=mbr.subscriptionChanges(subscriptionModel([],{month:"2026-04"}));
  assert.ok(missing.length>0);
  assert.ok(missing.every(row=>row.previous===null&&row.change===null&&row.percentage==="n/a"
    &&row.status==="Source period unavailable"));
});

test("subscription CSV and briefing export every sorted group, exact signed changes and safe source names",()=>{
  const extra=Array.from({length:30},(_value,index)=>["2026-05","extra-"+index,"1","rA"]);
  extra.push(["2026-05","=SUM(1+1)","1","rA"]);
  const model=subscriptionModel(extra),csv=mbr.subscriptionChangesCsv(model,"decrease");
  assert.equal(csv.trim().split("\r\n").length,40);
  assert.ok(csv.split("\r\n")[1].startsWith("Down,ExampleUS,2026-04,2026-05,USD,200,100,-100,-50,"));
  assert.match(csv,/Removed,ExampleUS,2026-04,2026-05,USD,25,0,-25,-100,/);
  assert.match(csv,/New,ExampleUS,2026-04,2026-05,USD,0,50,50,,/);
  assert.match(csv,/'=SUM\(1\+1\),ExampleUS/);
  assert.equal(mbr.workbookCsv(model,"subscriptions",null,undefined,{sort:"decrease"}),csv);
  assert.match(mbr.workbookReportText(model,"subscriptions","Action",null,undefined,{sort:"decrease"}),/Down,ExampleUS,2026-04,2026-05,USD,200,100,-100,-50,/);
});

test("subscription percentage changes never include separate annual-history rows or change the retained comparison",()=>{
  const raw=annualSnapshot(),before=structuredClone(raw);
  delete before.annual_history;
  const afterModel=mbr.workbookModel(mbr.reportingData(mbr.prepare(raw),"ExampleUS"),{month:"2026-08"});
  const beforeModel=mbr.workbookModel(mbr.reportingData(mbr.prepare(before),"ExampleUS"),{month:"2026-08"});
  assert.deepEqual(mbr.subscriptionChanges(afterModel),mbr.subscriptionChanges(beforeModel));
  assert.equal(afterModel.previousMonth,"2026-05");
  assert.equal(afterModel.adjacentComparison,false);
  assert.match(mbr.subscriptionChangesCsv(afterModel),/2026-05,2026-08,USD/);
});

test("credit presentation has twelve months, calendar days and honest missing/future native USD values",()=>{
  const model=mbr.workbookModel(mbr.reportingData(mbr.prepare(currencySnapshot()),"ExampleUS"),{month:"2026-05"});
  const presentation=mbr.creditPresentation(model),account=presentation.accounts[0];
  assert.equal(presentation.months.length,12);
  assert.equal(presentation.daysRemaining[0],334);
  assert.equal(presentation.daysRemaining[4],214);
  assert.equal(presentation.daysRemaining[11],0);
  assert.equal(account.timeline[0].eligible,null);
  assert.equal(account.timeline[3].cumulative,mbr.decimalToUnits("6000"));
  assert.equal(account.timeline[4].cumulative,mbr.decimalToUnits("12180"));
  assert.equal(account.timeline[7].eligible,null);
  assert.match(account.timeline[7].status,/Future/);
  assert.ok(account.timeline.every(cell=>cell.coverage===null));
  assert.equal(account.fullYear,null);
});

test("credit coverage uses matching GBP pools and keeps incomplete annual forecasts unavailable",()=>{
  const model=mbr.workbookModel(mbr.reportingData(mbr.prepare(currencySnapshot()),"ExampleUK"),{month:"2026-05"});
  const account=mbr.creditPresentation(model).accounts[0];
  assert.equal(account.recorded,mbr.decimalToUnits("1220"));
  assert.equal(account.recordedCoverage,mbr.decimalToUnits("0.061"));
  assert.equal(account.covered,mbr.decimalToUnits("5500"));
  assert.equal(account.fullYear,null);
  assert.equal(account.fullYearCoverage,null);
});

test("unknown eligibility blocks cumulative amounts and percentage lines without hiding a known earlier month",()=>{
  const raw=currencySnapshot();
  raw.dimensions.eligible.push("(not supplied)");
  raw.rows[5][8]=raw.dimensions.eligible.length-1;
  const presentation=mbr.creditPresentation(mbr.workbookModel(mbr.reportingData(mbr.prepare(raw),"ExampleUK"),{month:"2026-05"}));
  const account=presentation.accounts[0];
  assert.equal(account.timeline[3].cumulative,mbr.decimalToUnits("600"));
  assert.equal(account.timeline[4].eligible,null);
  assert.equal(account.timeline[4].cumulative,null);
  assert.equal(account.timeline[4].coverage,null);
  assert.equal(presentation.recorded,null);
});

test("credit matrix keeps signed credits and excludes unsupplied future cumulative values",()=>{
  const raw=currencySnapshot();
  raw.row_native_costs[1]=String(mbr.decimalToUnits("-600"));
  const model=mbr.workbookModel(mbr.reportingData(mbr.prepare(raw),"ExampleUK"),{month:"2026-05"});
  const account=mbr.creditPresentation(model).accounts[0];
  assert.equal(account.timeline[3].eligible,mbr.decimalToUnits("-600"));
  assert.equal(account.recorded,mbr.decimalToUnits("20"));
  const csv=mbr.creditMatrixCsv(model);
  assert.match(csv,/,-600,620,/);
  assert.doesNotMatch(csv,/'-600/);
  assert.equal(account.timeline[5].cumulative,null);
});

test("complete January history permits a hand-computed full-year baseline including leap days",()=>{
  for(const [year,days] of [["2026",365],["2024",366]]){
    const raw=currencySnapshot();
    raw.dimensions.month=[year+"-01",year+"-02",year+"-03"];
    raw.row_native_costs[1]=String(mbr.decimalToUnits("31"));
    const model=mbr.workbookModel(mbr.reportingData(mbr.prepare(raw),"ExampleUK"),{month:year+"-01"});
    const presentation=mbr.creditPresentation(model),account=presentation.accounts[0];
    assert.equal(presentation.daysRemaining[0],days-31);
    assert.deepEqual(account.missingMonths,[]);
    assert.equal(account.fullYear,mbr.decimalToUnits(String(days)));
    assert.equal(account.covered,account.fullYear);
  }
});

test("credit matrix CSV and text export the displayed full year with explicit units and original scope",()=>{
  const model=mbr.workbookModel(mbr.reportingData(mbr.prepare(currencySnapshot()),"ExampleUK"),{month:"2026-05"});
  const csv=mbr.creditMatrixCsv(model);
  assert.ok(csv.startsWith("Measure,Unit,2026-01,2026-02"));
  assert.ok(csv.includes("2026-12,As of,Definition,Scope,Sources"));
  assert.match(csv,/Monthly eligible cost - ExampleUK,GBP,,,,600,620,/);
  assert.match(csv,/Source pool coverage - ExampleUK,percent,,,,3,6.1,/);
  assert.match(mbr.workbookReportText(model,"credits","Notes"),/TWELVE-MONTH CREDIT-ELIGIBILITY MATRIX/);
});

function savingsSnapshot(extra=[]) {
  const raw=workbookSnapshot();
  const records=[
    ["2026-04","Virtual Machines","SavingsPlan","Used","80","20","0"],
    ["2026-04","Virtual Machines","SavingsPlan","Unused","20","0","0"],
    ["2026-05","Virtual Machines","SavingsPlan","Used","90","40","0"],
    ["2026-05","Virtual Machines","SavingsPlan","Unused","10","0","0"],
    ["2026-04","Storage","Reservation","Used","50","30","0"],
    ["2026-04","Storage","Reservation","Unused","10","0","0"],
    ["2026-05","Storage","Reservation","Used","40","20","0"],
    ["2026-05","Storage","Reservation","Unused","10","0","0"],
    ["2026-04","Log Analytics","OnDemand","Used","100","0","5"],
    ["2026-05","Log Analytics","OnDemand","Used","100","0","8"],
    ["2026-04","SQL Database","OnDemand","Used","100","0","2"],
    ["2026-05","SQL Database","OnDemand","Used","100","0","3"],...extra,
  ];
  const unpacked=records.map(row=>[row[0],"ExampleUS",row[1],"sub","uksouth",row[2],"Greenfield",row[3],"Yes","Azure"]);
  raw.dimensions=Object.fromEntries(raw.dimension_order.map((name,index)=>[name,[...new Set(unpacked.map(row=>row[index]))].sort()]));
  raw.rows=unpacked.map((row,index)=>[...row.map((value,column)=>raw.dimensions[raw.dimension_order[column]].indexOf(value)),String(mbr.decimalToUnits(records[index][4])/2n),1]);
  raw.row_sources=records.map(()=>0);
  raw.row_publishers=records.map(()=>"Azure");
  raw.row_native_costs=records.map(row=>String(mbr.decimalToUnits(row[4])));
  raw.row_native_currencies=records.map(()=>"USD");
  raw.native_calculations={version:1,rows:records.map(row=>[String(mbr.decimalToUnits(row[5])),String(mbr.decimalToUnits(row[6])),row[7]?0:1,row[7]?1:0])};
  raw.calculations={version:1,rows:raw.native_calculations.rows.map(row=>[String(BigInt(row[0])/2n),String(BigInt(row[1])/2n),row[2],row[3]])};
  raw.audit={warnings:[],reconciliations:[],excel_errors:[]};
  return raw;
}

function savingsModel(raw=savingsSnapshot(),month="2026-05") {
  return mbr.workbookModel(mbr.reportingData(mbr.prepare(raw),"ExampleUS"),{month});
}

test("savings comparison reuses native net estimates and reports utilization movement in percentage points",()=>{
  const model=savingsModel(),rows=mbr.savingsTable(model);
  const sp=rows.find(row=>row.name==="Savings plan"),ri=rows.find(row=>row.name==="Reservation");
  assert.equal(sp.current.savings,mbr.decimalToUnits("30"));
  assert.equal(sp.previous.savings,0n);
  assert.equal(sp.change,"n/a");
  assert.equal(sp.utilization,"90.0%");
  assert.equal(sp.previousUtilization,"80.0%");
  assert.equal(sp.utilizationChange,"10.0 pp");
  assert.equal(sp.utilizationChangePp,mbr.decimalToUnits("10"));
  assert.equal(ri.change,"-50.0%");
  assert.equal(ri.utilizationChange,"-3.3 pp");
  assert.equal(rows.find(row=>row.name==="Log Analytics / Monitor tiers").current.savings,mbr.decimalToUnits("8"));
  assert.equal(rows.find(row=>row.name==="Log Analytics / Monitor tiers").utilization,"n/a");
});

test("savings-plan utilization remains VM-only even when other services have unused allocations",()=>{
  const model=savingsModel(savingsSnapshot([
    ["2026-05","SQL Database","SavingsPlan","Used","100","10","0"],
    ["2026-05","SQL Database","SavingsPlan","Unused","300","0","0"],
  ]));
  const sp=mbr.savingsTable(model).find(row=>row.name==="Savings plan");
  assert.equal(sp.utilization,"90.0%");
  assert.equal(sp.current.savings,mbr.decimalToUnits("-260"));
  assert.equal(sp.utilizationChange,"10.0 pp");
});

test("non-zero unclassified covered cost blocks utilization and cannot cancel behind signed offsets",()=>{
  const raw=savingsSnapshot([
    ["2026-05","Virtual Machines","SavingsPlan","(not supplied)","5","0","0",true],
    ["2026-05","Virtual Machines","SavingsPlan","(not supplied)","-5","0","0",true],
  ]);
  const sp=mbr.savingsTable(savingsModel(raw)).find(row=>row.name==="Savings plan");
  assert.equal(sp.utilization,"n/a");
  assert.equal(sp.utilizationChange,"n/a");
  assert.equal(sp.utilizationCurrent.unknownCost,mbr.decimalToUnits("10"));
  assert.equal(sp.utilizationCurrent.unclassifiedRows,2);
  const zero=mbr.savingsTable(savingsModel(savingsSnapshot([
    ["2026-05","Virtual Machines","SavingsPlan","(not supplied)","0","0","0",true],
  ]))).find(row=>row.name==="Savings plan");
  assert.equal(zero.utilization,"90.0%");
  assert.equal(zero.utilizationCurrent.unclassifiedRows,1);
});

test("annual savings components partition every known estimate without double-counting service rows",()=>{
  const model=savingsModel(),account=mbr.savingsPresentation(model).accounts[0];
  assert.equal(account.recorded,mbr.decimalToUnits("78"));
  assert.deepEqual(account.components.map(row=>row.recorded),["30","30","13","5"].map(mbr.decimalToUnits));
  for(const [index,expected] of [[3,"27"],[4,"51"]]){
    assert.equal(account.components.reduce((sum,row)=>sum+row.periods[index].amount,0n),mbr.decimalToUnits(expected));
    assert.equal(account.total.periods[index].amount,mbr.decimalToUnits(expected));
  }
  assert.equal(account.components.reduce((sum,row)=>sum+row.recorded,0n),account.recorded);
  assert.equal(account.fullYear,null);
  assert.equal(account.knownCovered,account.recorded+mbr.forecastModel(model).accounts[0].knownRemainingSavings);
});

test("covered tier amounts stay in their pricing model rather than appearing twice in annual totals",()=>{
  const model=savingsModel(savingsSnapshot([
    ["2026-05","Virtual Machines","SavingsPlan","Used","0","0","7"],
  ]));
  const account=mbr.savingsPresentation(model).accounts[0];
  assert.equal(account.total.periods[4].amount,mbr.decimalToUnits("58"));
  assert.equal(account.components[0].periods[4].amount,mbr.decimalToUnits("37"));
  assert.equal(account.components.reduce((sum,row)=>sum+row.periods[4].amount,0n),account.total.periods[4].amount);
  assert.equal(mbr.savingsTable(model).find(row=>row.name==="Commitment tiers").current.savings,mbr.decimalToUnits("11"));
});

test("Log Analytics and Azure Monitor explicit tiers share a named group without including other services",()=>{
  const model=savingsModel(savingsSnapshot([
    ["2026-05","Azure Monitor","OnDemand","Used","100","0","4"],
  ]));
  const account=mbr.savingsPresentation(model).accounts[0];
  const log=account.components.find(row=>row.name==="Log Analytics / Monitor tiers");
  assert.equal(log.periods[4].amount,mbr.decimalToUnits("12"));
  assert.equal(account.components.find(row=>row.name==="Other explicit tiers").periods[4].amount,mbr.decimalToUnits("3"));
  assert.equal(account.total.periods[4].amount,mbr.decimalToUnits("55"));
  assert.equal(mbr.savingsTable(model).find(row=>row.name==="Log Analytics / Monitor tiers").current.savings,mbr.decimalToUnits("12"));
});

test("savings matrix never fills missing/future actuals with zero or peeks at supplied future data",()=>{
  const presentation=mbr.savingsPresentation(savingsModel(savingsSnapshot([
    ["2026-07","Virtual Machines","SavingsPlan","Used","10","99","0"],
  ])));
  const row=presentation.accounts[0].total;
  assert.equal(presentation.months.length,12);
  assert.equal(row.periods[0].amount,null);
  assert.equal(row.periods[6].amount,null);
  assert.equal(row.periods[6].available,false);
  const missing=mbr.savingsTable(savingsModel(savingsSnapshot(),"2026-04")).find(row=>row.name==="Savings plan");
  assert.equal(missing.change,"n/a");
  assert.equal(missing.previousUtilization,"n/a");
  assert.equal(missing.utilizationChange,"n/a");
});

test("unpriced savings remain known partial components and block complete annual baselines",()=>{
  const model=savingsModel(savingsSnapshot([
    ["2026-05","Other service","OnDemand","Used","10","0","0",true],
  ]));
  const account=mbr.savingsPresentation(model).accounts[0],other=account.components.find(row=>row.name==="Other explicit tiers");
  assert.equal(account.total.periods[4].amount,mbr.decimalToUnits("51"));
  assert.equal(account.total.periods[4].missing,1);
  assert.equal(other.periods[4].missing,1);
  assert.equal(other.periods[4].amount,mbr.decimalToUnits("3"));
  assert.equal(account.fullYear,null);
  assert.notEqual(account.knownCovered,null);
});

test("negative savings, zero known totals and large exact values remain signed rather than clamped",()=>{
  const model=savingsModel(savingsSnapshot([
    ["2026-05","Virtual Machines","SavingsPlan","Unused","78","0","0"],
  ]));
  const account=mbr.savingsPresentation(model).accounts[0];
  assert.equal(account.recorded,0n);
  assert.equal(account.total.periods[4].amount,mbr.decimalToUnits("-27"));
  const csv=mbr.savingsMatrixCsv(model);
  assert.match(csv,/,-27,/);
  assert.doesNotMatch(csv,/'-27/);
  const raw=savingsSnapshot();
  raw.native_calculations.rows[2][0]=String(mbr.decimalToUnits("9007199254740993.25"));
  const large=mbr.savingsPresentation(savingsModel(raw)).accounts[0].components[0].periods[4].amount;
  assert.equal(large,mbr.decimalToUnits("9007199254740983.25"));
});

test("complete January savings use the shared calendar-day scenario including leap years",()=>{
  for(const [year,days] of [["2026",365],["2024",366]]){
    const raw=savingsSnapshot();
    raw.dimensions.month=[year+"-01",year+"-02"];
    raw.native_calculations.rows.forEach(row=>{row[0]="0";row[1]="0";});
    raw.native_calculations.rows[0][0]=String(mbr.decimalToUnits("61"));
    const model=savingsModel(raw,year+"-01"),account=mbr.savingsPresentation(model).accounts[0];
    assert.equal(account.total.periods[0].amount,mbr.decimalToUnits("31"));
    assert.equal(account.fullYear,mbr.decimalToUnits(String(days)));
    assert.equal(account.knownCovered,account.fullYear);
  }
});

test("savings CSV and text include twelve month amounts, component shares and numeric utilization movements",()=>{
  const model=savingsModel(),csv=mbr.savingsMatrixCsv(model);
  assert.ok(csv.startsWith("Enrollment,Component,Currency,2026-01,2026-02"));
  assert.match(csv,/2026-12,Recorded known estimate,Full-year baseline,Known components plus future excluding gaps/);
  assert.match(csv,/ExampleUS,Savings plan,USD,,,,0,30,/);
  assert.equal(csv.trim().split("\r\n").length,6);
  const comparison=mbr.workbookCsv(model,"savings");
  assert.match(comparison,/Utilization change \(percentage points\)/);
  assert.match(comparison,/,80.0%,10,0,0/);
  assert.match(mbr.workbookReportText(model,"savings","Notes"),/TWELVE-MONTH KNOWN-SAVINGS MATRIX/);
});

function annualSnapshot() {
  const history=currencySnapshot(),raw=structuredClone(history);
  history.history_policy={version:1,kind:"grouped-annual-history",years:["2026"]};
  const selected=raw.rows.map((_row,index)=>index).filter(index=>raw.rows[index][0]>0);
  raw.dimensions.month=["2026-05","2026-08"];
  for(const name of ["rows","row_sources","row_publishers","row_native_currencies","row_native_costs"]){
    raw[name]=selected.map(index=>raw[name][index]);
  }
  raw.rows.forEach(row=>{row[0]-=1;});
  for(const name of ["calculations","native_calculations"])raw[name].rows=selected.map(index=>raw[name].rows[index]);
  raw.retention={limit:2,months:raw.dimensions.month};
  raw.annual_history=history;
  return raw;
}

test("annual backfill changes YTD only, never the active two-month totals or comparison groups",()=>{
  const raw=annualSnapshot(),before=structuredClone(raw);
  delete before.annual_history;
  const model=mbr.workbookModel(mbr.reportingData(mbr.prepare(raw),"ExampleUS"),{month:"2026-08"});
  const previous=mbr.workbookModel(mbr.reportingData(mbr.prepare(before),"ExampleUS"),{month:"2026-08"});
  assert.deepEqual(model.current,previous.current);
  assert.equal(model.previousMonth,"2026-05");
  assert.deepEqual(model.previousMonth,previous.previousMonth);
  assert.deepEqual(mbr.driverModel(model).services,mbr.driverModel(previous).services);
  assert.equal(model.history.total,mbr.decimalToUnits("30800"));
  assert.equal(previous.history.total,mbr.decimalToUnits("24800"));
  assert.equal(model.history.eligible,mbr.decimalToUnits("30780"));
  assert.ok(!model.accounts[0].missingMonths.includes("2026-04"));
  assert.deepEqual(model.data.dimensions.month,["2026-05","2026-08"]);
  assert.deepEqual(model.chartMonths,["2026-05","2026-06","2026-07","2026-08"]);
  const detail=mbr.enrollmentDetail(mbr.prepare(raw),{month:"2026-08"},"ExampleUS");
  assert.equal(detail.account.current.cost,mbr.decimalToUnits("18600"));
  assert.equal(detail.account.previous.cost,mbr.decimalToUnits("6200"));
});

test("annual credits, savings matrices and summary history use backfilled months exactly once",()=>{
  const raw=annualSnapshot();
  const model=mbr.workbookModel(mbr.reportingData(mbr.prepare(raw),"ExampleUS"),{month:"2026-08"});
  const credits=mbr.creditPresentation(model),savings=mbr.savingsPresentation(model);
  assert.equal(credits.accounts[0].timeline[3].eligible,mbr.decimalToUnits("6000"));
  assert.equal(credits.accounts[0].timeline[7].cumulative,mbr.decimalToUnits("30780"));
  assert.equal(savings.accounts[0].total.periods[3].amount,0n);
  assert.equal(savings.accounts[0].total.periods[4].amount,mbr.decimalToUnits("70"));
  assert.equal(mbr.summaryMeasures(model).find(row=>row.label==="Recorded-to-date credit-eligible proxy").cells[0].amount,mbr.decimalToUnits("30780"));
  assert.match(mbr.creditMatrixCsv(model),/2026-04/);
  assert.match(mbr.workbookReportText(model,"savings",""),/Detailed comparison months: 2026-05, 2026-08/);
  assert.equal(mbr.forecastModel(model).accounts[0].observed.cost,mbr.decimalToUnits("30800"));
});

test("annual filters map source identities rather than reusing unrelated numeric source indices",()=>{
  const raw=annualSnapshot(),history=raw.annual_history;
  history.meta.sources.reverse();
  history.row_sources=history.row_sources.map(index=>1-index);
  const model=mbr.workbookModel(mbr.reportingData(mbr.prepare(raw),"ExampleUS"),{month:"2026-08",source:"1"});
  assert.equal(model.history.total,mbr.decimalToUnits("18600"));
  assert.deepEqual(model.annualSources.map(source=>source.source_name),["August workbook"]);
  assert.equal(model.annualCell("2026-04","ExampleUS").available,false);
  const filtered=mbr.workbookModel(mbr.reportingData(mbr.prepare(raw),"ExampleUS"),{month:"2026-08",service:"Virtual Machines"});
  assert.equal(filtered.history.total,mbr.decimalToUnits("220"));
});

test("unknown historical native money blocks annual amounts without breaking current detail or inventing reverse FX",()=>{
  const raw=annualSnapshot();
  raw.annual_history.row_native_costs[0]=null;
  const model=mbr.workbookModel(mbr.reportingData(mbr.prepare(raw),"ExampleUS"),{month:"2026-08"});
  assert.equal(model.current.total,mbr.decimalToUnits("18600"));
  assert.equal(model.accounts[0].previous.total,mbr.decimalToUnits("6200"));
  assert.equal(model.history.available,false);
  assert.equal(model.history.total,null);
  assert.equal(model.accounts[0].history.savings,null);
  assert.equal(mbr.forecastModel(model).full.cost,null);
  assert.equal(model.annualCell("2026-04","ExampleUS").available,false);
  assert.ok(model.accounts[0].missingMonths.includes("2026-04"));
});

test("annual history cannot change the short-term comparison month when a historical source fills a calendar gap",()=>{
  const raw=annualSnapshot();
  raw.annual_history.dimensions.month[0]="2026-07";
  const model=mbr.workbookModel(mbr.reportingData(mbr.prepare(raw),"ExampleUS"),{month:"2026-08"});
  assert.equal(model.previousMonth,"2026-05");
  assert.equal(model.accounts[0].previous.total,mbr.decimalToUnits("6200"));
});

test("annual year totals exclude prior calendar years even when those months remain stored",()=>{
  const raw=annualSnapshot();
  raw.annual_history.dimensions.month[0]="2025-12";
  const model=mbr.workbookModel(mbr.reportingData(mbr.prepare(raw),"ExampleUS"),{month:"2026-08"});
  assert.equal(model.history.total,mbr.decimalToUnits("24800"));
  assert.equal(model.year,"2026");
  assert.ok(model.accounts[0].missingMonths.includes("2026-04"));
  assert.equal(mbr.creditPresentation(model).accounts[0].timeline[3].eligible,null);
});

test("annual history schemas fail explicitly for nested history, mismatched bases and truncation",()=>{
  const raw=annualSnapshot();
  raw.annual_history.meta.basis="actual";
  assert.throws(()=>mbr.prepare(raw),/annual history schema/);
  raw.annual_history.meta.basis="amortized";
  raw.annual_history.retention={limit:2,months:["2026-05","2026-08"]};
  assert.throws(()=>mbr.prepare(raw),/annual history schema/);
});

test("native comparison shows USD ExampleUS and GBP ExampleUK without a mixed-currency total",()=>{
  const raw=nativeSnapshot(),before=JSON.stringify(raw);
  const model=mbr.nativeComparison(mbr.prepare(raw),{month:"2026-05"});
  const ref=model.accounts.find(account=>account.name==="ExampleUS");
  const example_uk=model.accounts.find(account=>account.name==="ExampleUK");
  assert.equal(ref.currency,"USD");
  assert.equal(ref.current.cost,mbr.decimalToUnits("6200"));
  assert.equal(ref.previous.cost,mbr.decimalToUnits("6000"));
  assert.equal(ref.change,mbr.decimalToUnits("200"));
  assert.equal(example_uk.currency,"GBP");
  assert.equal(example_uk.current.cost,mbr.decimalToUnits("620"));
  assert.equal(example_uk.change,mbr.decimalToUnits("20"));
  assert.equal("total" in model,false);
  assert.equal(JSON.stringify(raw),before);
});

test("native view never reverses GBP conversion to invent unavailable original amounts",()=>{
  const data=mbr.prepare(workbookSnapshot());
  const model=mbr.nativeComparison(data,{month:"2026-05"});
  assert.ok(model.accounts.every(account=>account.current.cost===null));
  assert.ok(model.accounts.every(account=>account.change===null));
  assert.ok(model.accounts.every(account=>account.current.reason.includes("missing")));
});

test("native comparisons refuse mixed source currencies and cross-currency period changes",()=>{
  const raw=nativeSnapshot();
  raw.row_native_currencies[3]="GBP";
  let model=mbr.nativeComparison(mbr.prepare(raw),{month:"2026-05"});
  assert.equal(model.accounts.find(account=>account.name==="ExampleUS").current.cost,null);
  const changed=nativeSnapshot();
  changed.rows.forEach((row,index)=>{if(row[0]===1&&changed.dimensions.enrollment[row[1]]==="ExampleUS")changed.row_native_currencies[index]="EUR";});
  model=mbr.nativeComparison(mbr.prepare(changed),{month:"2026-05"});
  assert.equal(model.accounts.find(account=>account.name==="ExampleUS").change,null);
  assert.equal(model.accounts.find(account=>account.name==="ExampleUS").percentage,"n/a");
});

test("native view honors filters and preserves unknown month versus real zero activity",()=>{
  const data=mbr.prepare(nativeSnapshot());
  const filtered=mbr.nativeComparison(data,{month:"2026-05",service:"not observed"});
  assert.ok(filtered.accounts.every(account=>account.current.cost===0n));
  const missing=mbr.nativeComparison(data,{month:"2026-08"});
  assert.equal(missing.accounts.find(account=>account.name==="ExampleUK").current.cost,null);
  assert.equal(missing.accounts.find(account=>account.name==="ExampleUS").current.cost,mbr.decimalToUnits("18600"));
});

test("native CSV and text reports retain per-row currencies and original signed decimals",()=>{
  const data=mbr.prepare(nativeSnapshot());
  const csv=mbr.nativeComparisonCsv(data,{month:"2026-05"});
  assert.ok(csv.includes("Native currency,Native cost"));
  assert.ok(csv.includes("ExampleUS,2026-05,USD,6200,2026-04,USD,6000,200,"));
  assert.ok(csv.includes("ExampleUK,2026-05,GBP,620,2026-04,GBP,600,20,"));
  assert.ok(csv.includes("no cross-currency sum"));
  const report=mbr.nativeComparisonText(data,{month:"2026-05"},"Review native source costs");
  assert.ok(report.includes("No FX applied"));
  assert.ok(report.includes("SHA-256 may"));
  assert.ok(report.includes("Review native source costs"));
});

test("compact native formatting does not put a pound sign on USD costs",()=>{
  assert.equal(mbr.formatCompact(mbr.decimalToUnits("13464200.52"),"USD"),"$13.46M");
  assert.equal(mbr.formatMoney(mbr.decimalToUnits("13464200.52"),2,"USD"),"$13,464,200.52");
  assert.equal(mbr.formatMoney(mbr.decimalToUnits("-1.015"),2,"USD"),"-$1.02");
  assert.equal(mbr.formatMoney(mbr.decimalToUnits("-0.001"),2,"USD"),"$0.00");
  assert.equal(mbr.formatCompact(mbr.decimalToUnits("-1200"),"USD"),"-$1.20k");
  assert.equal(mbr.formatMoney(mbr.decimalToUnits("12"),2,"EUR"),"EUR 12.00");
  assert.equal(mbr.formatCompact(mbr.decimalToUnits("2918369.53"),"GBP"),"\u00a32.92M");
});

test("enrollment details are scoped to one native currency and reuse native cost metrics",()=>{
  const raw=nativeSnapshot(),before=JSON.stringify(raw);
  const data=mbr.prepare(raw);
  const detail=mbr.enrollmentDetail(data,{month:"2026-05"},"ExampleUS");
  assert.equal(detail.enrollment,"ExampleUS");
  assert.equal(detail.currency,"USD");
  assert.equal(detail.currentMetrics.total,mbr.decimalToUnits("6200"));
  assert.equal(detail.previousMetrics.total,mbr.decimalToUnits("6000"));
  assert.equal(detail.currentMetrics.benefit,mbr.decimalToUnits("220"));
  assert.equal(detail.currentMetrics.unused,mbr.decimalToUnits("20"));
  assert.equal(detail.currentMetrics.eligible,mbr.decimalToUnits("6180"));
  assert.equal(detail.currentMetrics.sourceRows,3);
  assert.equal(detail.currentMetrics.subscriptions,1);
  assert.equal(detail.currentMetrics.savings,null);
  assert.ok(detail.currentRows.every(row=>data.dimensions.enrollment[row.ids[1]]==="ExampleUS"));
  assert.equal(JSON.stringify(raw),before);
  assert.throws(()=>mbr.enrollmentDetail(data,{},"unknown"),/Unknown enrollment/);
});

test("every enrollment breakdown reconciles current and prior native totals",()=>{
  const detail=mbr.enrollmentDetail(mbr.prepare(nativeSnapshot()),{month:"2026-05"},"ExampleUS");
  for(const dimension of ["service","subscription","region","pricing","portfolio","publisher","charge_type"]){
    const groups=mbr.enrollmentBreakdown(detail,dimension);
    assert.equal(groups.reduce((sum,row)=>sum+row.current,0n),detail.account.current.cost,dimension);
    assert.equal(groups.reduce((sum,row)=>sum+row.previous,0n),detail.account.previous.cost,dimension);
    assert.equal(groups.reduce((sum,row)=>sum+row.change,0n),detail.account.change,dimension);
    assert.equal(groups.reduce((sum,row)=>sum+row.count,0),3);
  }
  const services=mbr.enrollmentBreakdown(detail,"service");
  assert.equal(services.find(row=>row.name==="Virtual Machines").current,mbr.decimalToUnits("220"));
  assert.equal(services.find(row=>row.name==="Virtual Machines").previous,0n);
  assert.equal(services.find(row=>row.name==="Virtual Machines").percentage,"n/a");
});

test("removed groups in a supplied month are zero, but a missing enrollment month is unavailable",()=>{
  const raw=nativeSnapshot();
  raw.dimensions.service.push("Prior only");
  raw.rows[0][2]=raw.dimensions.service.length-1;
  const data=mbr.prepare(raw);
  let detail=mbr.enrollmentDetail(data,{month:"2026-05"},"ExampleUS");
  const removed=mbr.enrollmentBreakdown(detail,"service").find(row=>row.name==="Prior only");
  assert.equal(removed.current,0n);
  assert.equal(removed.change,mbr.decimalToUnits("-6000"));
  assert.equal(removed.percentage,"-100.0%");
  detail=mbr.enrollmentDetail(data,{month:"2026-08"},"ExampleUK");
  assert.equal(detail.account.current.cost,null);
  assert.ok(mbr.enrollmentBreakdown(detail,"service").every(row=>row.current===null&&row.change===null));
});

test("enrollment filters honor pricing, subscription and zero activity without mixing enrollments",()=>{
  const data=mbr.prepare(nativeSnapshot());
  const ri=mbr.enrollmentDetail(data,{month:"2026-05",pricing:"Reservation",query:"SUB-A"},"ExampleUS");
  assert.equal(ri.currentMetrics.total,mbr.decimalToUnits("220"));
  assert.equal(ri.currentMetrics.unused,mbr.decimalToUnits("20"));
  const empty=mbr.enrollmentDetail(data,{month:"2026-05",service:"not observed"},"ExampleUS");
  assert.equal(empty.currentMetrics.available,true);
  assert.equal(empty.account.current.cost,0n);
  assert.equal(empty.sources.length,1);
});

test("unavailable native money never becomes GBP, but independent row counts remain accurate",()=>{
  const detail=mbr.enrollmentDetail(mbr.prepare(workbookSnapshot()),{month:"2026-05"},"ExampleUS");
  assert.equal(detail.currentMetrics.available,false);
  assert.equal(detail.account.current.cost,null);
  assert.equal(detail.currentRows.length,0);
  assert.equal(detail.currentMetrics.sourceRows,3);
  assert.equal(detail.currentMetrics.subscriptions,1);
});

test("enrollment exports include all scoped native groups/rows with signed money and source identity",()=>{
  const raw=nativeSnapshot();
  raw.rows[2][10]=String(mbr.decimalToUnits("-50"));
  raw.row_native_costs[2]=String(mbr.decimalToUnits("-100"));
  const detail=mbr.enrollmentDetail(mbr.prepare(raw),{month:"2026-05"},"ExampleUS");
  const groups=mbr.enrollmentCsv(detail,"service");
  assert.equal(groups.trim().split("\r\n").length,3);
  assert.ok(groups.includes("ExampleUS,USD,2026-05,USD,2026-04,"));
  assert.ok(groups.includes(",-100,"));
  const csv=mbr.enrollmentRowsCsv(detail);
  assert.equal(csv.trim().split("\r\n").length,4);
  assert.ok(csv.includes("Native currency,Native cost,Source rows"));
  assert.ok(csv.includes(",USD,-100,1,"));
  assert.ok(!csv.includes(",ExampleUK,"));
  assert.ok(csv.includes("May workbook"));
  const report=mbr.enrollmentReportText(detail,"service","Owner: verify change");
  assert.ok(report.includes("ExampleUS"));
  assert.ok(report.includes("$"));
  assert.ok(report.includes("Owner: verify change"));
  assert.ok(report.includes("DEFINITIONS"));
  assert.ok(report.includes("not hours"));
});

test("enrollment table, comparison CSV and briefing share the requested group ordering",()=>{
  const detail=mbr.enrollmentDetail(mbr.prepare(nativeSnapshot()),{month:"2026-05"},"ExampleUS");
  const groups=mbr.enrollmentBreakdown(detail,"service","decrease");
  assert.equal(groups[0].name,"Storage");
  const csv=mbr.enrollmentCsv(detail,"service","decrease");
  assert.ok(csv.split("\r\n")[1].includes(",service,Storage,"));
  const briefing=mbr.enrollmentReportText(detail,"service","review","decrease");
  assert.ok(briefing.indexOf("Storage |")<briefing.indexOf("Virtual Machines |"));
  assert.throws(()=>mbr.enrollmentBreakdown(detail,"service","bad"),/Unsupported enrollment sort/);
});
