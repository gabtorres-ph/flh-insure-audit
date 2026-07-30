# FastLane Hustle Rentals LLC
## Mileage Attribution System — Technical Specification

**Version 1.0 · July 2026**
**Purpose:** Produce a monthly, per-vehicle report splitting total fleet miles into insurance rating periods (P0/P1/P2 — three-period model) for HUB International / Y-Risk, and to bill renters for leisure (P0) usage.

---

## 1. The problem, stated precisely

Two systems each hold half the answer, and neither can produce the report alone.

| System | What it knows | What it does NOT know |
|---|---|---|
| **OneStep GPS** | Every mile the vehicle moved, with timestamps | Whether the driver was on an app |
| **Argyle** | When the renter was on an app, and what they were doing | How far the *vehicle* moved (only what the platform self-reports) |

Neither system knows which renter had which car on a given day. That comes from a third source: the **GHL rental agreement register**.

**The report is a three-way join, not a two-way one.** This is the single most common reason a build like this fails — it gets scoped as "Argyle + GPS" and the vehicle assignment table gets discovered late.

---

## 2. Governing principle

> **GPS is the sole source of truth for MILES.**
> **Argyle is the sole source of truth for CLASSIFICATION.**

Argyle's platform-reported trip distance is **never added** to GPS miles. It is captured only as a cross-check ratio.

The consequence is a hard control:

```
P0 + P1 + P2  =  Total GPS miles     (exactly, every vehicle, every month)
```

This is the first thing an underwriter will test. A report where the parts don't sum to the whole is a report they stop trusting. Any developer who proposes summing Argyle distances instead has misunderstood the assignment.

---

## 3. Rating period definitions

**Y-Risk rates on the three-period model. This is confirmed and is the production configuration.**

| Period | Meaning | Billing |
|---|---|---|
| **P0** | App off. Personal / leisure use. | **Billable to renter** (leisure-mile program) |
| **P1** | App on, **not engaged**. Online and available — before, between, and after assignments. | Commercial |
| **P2** | **Engaged.** Assignment accepted through drop-off. | Commercial |

The three-period model collapses what a four-period model would split into "en route to pickup" and "passenger on board." Everything from the moment the driver accepts until the moment they complete is a single engaged period.

**This is a real simplification, not just a cosmetic one.** See §4 — it removes the build's dependency on Argyle's least reliable timestamp.

> The engine still exposes `PERIOD_MODEL` as a configuration constant (3 or 4). If a future carrier splits the engaged period, it is a settings change rather than a rewrite. Do not let a developer hardcode three periods into the allocation logic.

---

## 4. Argyle: where the data actually lives

**It is in the API, not the Console UI.** The Console gives you connection status and per-user views. Bulk monthly extraction requires API calls.

**Authentication.** HTTP Basic auth. Username is `api_key_id`, password is `api_key_secret`. Keys are generated at `console.argyle.com/api-keys` — the secret is shown once at creation and obscured afterward, so it must be captured and stored at that moment.

**Base URLs.** Sandbox `https://api-sandbox.argyle.com/v2` · Production `https://api.argyle.com/v2`. Keys are environment-specific; a sandbox key against production returns 401.

**Endpoints that matter:**

| Endpoint | Use |
|---|---|
| `GET /v2/users` | Roster of connected renters |
| `GET /v2/accounts` | One per platform connection per renter. Carries `availability` — tells you whether gigs/shifts are synced and the date range available |
| `GET /v2/gigs` | **The core dataset.** Trip-level records |
| `GET /v2/shifts` | On-app envelope, including breaks |
| `GET /v2/vehicles` | Which car the renter registered with the platform — a useful independent cross-check |

**The gig object carries exactly the timestamps the period model needs:**

```json
"all_datetimes": {
  "request_at":  "...",   "accept_at":   "...",
  "pickup_at":   "...",   "dropoff_at":  "...",
  "cancel_at":   null,    "shift_start": null,
  "shift_end":   null,    "breaks":      null
}
```

Mapping:

| Interval | Period |
|---|---|
| `accept_at` → `dropoff_at` | **P2** (engaged) |
| `accept_at` → `cancel_at` (cancelled runs) | **P2** — driver was dispatched and moving under platform direction |
| No usable stamps | Fall back to `start_datetime` → `end_datetime`, classified P2, **flag the record** |

### P1 is constructed, not read

**This is the part that decides whether the report is right or expensive.** There is no single Argyle field that says "P1." It is assembled from four signals:

| # | Signal | Source |
|---|---|---|
| 1 | Shift records | `/shifts` — `shift_start` → `shift_end`, minus breaks. Best evidence, but **most platforms don't return it** |
| 2 | **Gaps between assignments** | Cluster a renter's engaged windows into sessions (assignments within 60 minutes are one session). The gap between consecutive assignments is P1 |
| 3 | **Lead and trail extension** | From the start of the GPS drive segment containing the first assignment, back to that assignment = P1. From the last completion forward to the end of that drive segment = P1 |
| 4 | **Duration refinement** | Where a platform reports both an assignment window and a shorter actual engaged `duration`, the difference is en-route time. Trim P2 from the front and reclassify the remainder to P1 |

**Signals 2 and 3 are not optional.** If the build only implements signal 1, every gap between assignments falls through to P0 and gets billed as leisure. On platforms with no shift data, that means an entire working evening bills as personal use. Signal 3 is the one most often missed — a driver who goes online at 6:00 PM and gets their first request at 6:30 was online and moving for thirty minutes, and no gig record contains that fact.

**Worked example (this is the acceptance test).** Driver does 50 miles across four GPS trips. Trips 1–3 have no app activity → 40 miles P0. Trip 4 runs 6:00–9:00 PM, 10 miles, with assignments at 6:30–6:58, 7:01–7:21 (18 min reported engaged), and 7:43–8:04.

The engine must produce five P1 windows: `6:00–6:30` (lead), `6:58–7:01` (gap), `7:01–7:03` (duration refinement), `7:21–7:43` (gap), `8:04–9:00` (trail). Result: **P0 40.0 · P1 6.5 · P2 3.5 · total 50.0.** If a candidate build doesn't return exactly this, it is wrong.

**Why the three-period model is the easier build.** `pickup_at` is the stamp most often missing or null, particularly on delivery platforms — a four-period model needs it to draw the P2/P3 boundary, and every missing value becomes a flagged record requiring a judgment call. The three-period model never reads that field. It needs only `accept_at` and `dropoff_at`, which platforms report reliably. Expect materially fewer exceptions and a cleaner report as a result.

**Mechanics to build around:** cursor pagination via the `next` URL (default 10 per page, max 200 — always set `limit=200`); rate limit 50 requests/second with 429 on breach; event data backfills from most-recent backward, so records arrive over hours to days after a connection is made.

**Webhooks are the better architecture than polling.** Subscribe to `gigs.added`, `gigs.fully_synced`, and the shift equivalents. Push into a database as they arrive; the monthly report then becomes a query against your own data rather than a fragile month-end scrape.

---

## 5. The allocation algorithm

This is the part that must be built correctly, and it is the part a mediocre developer will get wrong.

**Step 1 — Normalize the GPS export.**
Filter to `Status = Driving` (stops carry no mileage). Parse `Start Time` / `End Time` as **local wall-clock** and localize to `America/New_York`, then convert to UTC. Map the free-text `Device` label to a canonical vehicle key.

**Step 2 — Pull Argyle intervals per renter account.** Classify engaged windows as P2, then construct the P1 envelope from the four signals above. All Argyle timestamps are ISO 8601 UTC.

**Step 3 — Attach intervals to vehicles** via the GHL assignment register. An Argyle interval only counts against a vehicle if that renter was holding that vehicle at that moment. Intervals that fall in a gap between rentals are dropped and logged.

**Step 4 — Flatten the timeline per vehicle, priority `P2 > P1`.**
This is the multi-apping guard. A renter running Uber and DoorDash simultaneously generates overlapping intervals. You take the **union with priority**, never the sum. Skip this and a busy driver's classified time will exceed the hours in the day, which is an immediately visible error in an underwriting review.

**Step 5 — Allocate each GPS drive segment pro-rata by overlap seconds.**

```
For each GPS drive segment (start, end, miles):
    overlap_seconds[period] = seconds of intersection with the flattened timeline
    covered = sum(overlap_seconds)
    overlap_seconds[P0] = segment_duration - covered        # residual
    miles[period] = miles * overlap_seconds[period] / segment_duration
```

**Step 6 — P0 is the residual, by definition.** Compute P1 and P2, then derive P0 as `total − (P1+P2)`. Doing it this way instead of rounding three numbers independently is what makes the reconciliation tie to the penny every time.

---

## 6. Decision rules for the ugly cases

Every one of these needs a written, defensible answer before the report goes to a carrier. An underwriter will ask.

| Case | Rule | Why |
|---|---|---|
| Renter never connected Argyle | All miles → P0, tagged **unverified** | Conservative. Costs FastLane money rather than understating carrier exposure — which is what makes the report credible. Reported separately from verified P0 so the gap is visible and actionable. |
| Renter connected some apps, not all | Uncovered time → P0; report a **coverage %** per renter | The underwriter must be able to see what is verified vs. assumed |
| Platform reports no `accept_at` | Whole gig envelope → P2, flag it | Don't silently guess at a boundary |
| Assignment accepted then cancelled | `accept_at` → `cancel_at` → P2 | Driver was dispatched and moving under platform direction |
| Renter online but parked | Classified P1, contributes zero miles | App-on with no movement carries no mileage exposure |
| Multi-apping | Union with priority P2 > P1 | Never double count |
| Drive segment straddles a period boundary | Split pro-rata by seconds | The whole point of the method |
| Argyle data arrives after the report was cut | Restate in the following month's report as a labeled prior-period adjustment | Never silently rewrite an issued report |
| GPS device offline / gap | Log as a coverage exception; do not impute miles | Inventing mileage destroys the audit trail |
| Vehicle in maintenance, no renter assigned | Excluded from billing, shown separately as fleet-operational miles | Not the renter's usage |
| DST transitions (Mar / Nov) | Joins execute in UTC; display converts to local | Prevents the classic one-hour misclassification |

---

## 7. Output — the monthly deliverable

**Tab 1 — Methodology.** Sources, period definitions, allocation method, stated assumptions. This tab is what makes the number believable; do not let it be dropped.

**Tab 2 — Vehicle Summary.** One row per vehicle: unit ID, description, VIN, total GPS miles, **P0 verified / P0 unverified / total P0**, P1, P2, commercial miles, **reconciliation flag**, leisure %, **Argyle coverage %**.

**Tab 2b — Method Demonstration.** The worked example above, traced end to end. This is the tab that proves capability in an underwriting review — a fleet total shows scale, but a single day traced minute by minute shows method.

**Tab 2c — Coverage Exposure.** Verified vs unverified leisure, and the controls enforcing connection.

**Tab 3 — Assumptions.** Editable inputs (leisure rate, leisure-day threshold) referenced by formula, never hardcoded into the grid.

**Tab 4 — Data Quality.** Open exceptions with owner and status.

**Tab 5 — Daily Detail.** Per vehicle, per day. This is the backup an adjuster asks for after a claim, and the reason to keep raw segment-level data rather than only monthly rollups.

Machine-readable CSV of the same data ships alongside the workbook.

---

## 8. Operating cadence

| When | What |
|---|---|
| Continuous | Argyle webhooks write gigs/shifts to the database as they arrive |
| Weekly (Mon) | Coverage report: which renters have unconnected platforms. Feeds enforcement. |
| Monthly, 1st | Pull prior-month OneStep GPS export (automate if OneStep exposes an API; otherwise scheduled manual export) |
| Monthly, 10th | Cut the report — the lag lets Argyle backfill settle |
| Monthly, 10th | Deliver to HUB; issue renter leisure invoices through GHL |

---

## 9. Build phases

**Phase 1 — Foundation (1–2 weeks).**
Vehicle master table with VIN, canonical unit ID, and the OneStep device-name crosswalk. Rental assignment register exported from GHL as a queryable table. **Fix the GPS device naming in OneStep itself** — that is a 30-minute admin task that removes a permanent class of bug.

**Phase 2 — Argyle pipeline (2–3 weeks).**
API integration, webhook receiver, database schema, historical backfill.

**Phase 3 — Attribution engine (1–2 weeks).**
The algorithm in §5, with the reconciliation control and a unit test suite covering every case in §6.

**Phase 4 — Reporting (1 week).**
Workbook generation, CSV export, delivery automation.

Total: **5–8 weeks** for a competent contractor, part-time.

---

## 10. Data quality — must be closed before submission

Findings from the January–July 2026 OneStep export (21,591 records):

1. **Unit FL-016 is assigned to two different vehicles** — a 2010 Lexus RX 350 and a 2017 Toyota RAV4. Miles cannot be attributed correctly until this is split.
2. **1,531 drive segments carry the device name `DISABLED`** — 2,851 miles in June alone that cannot be tied to a vehicle on the fleet schedule.
3. **Naming inconsistencies:** FL-009, FL-019 and FL-006 omit the dash; `FL - 014` has stray spaces; `FL-020-` is malformed; "Infinit" is misspelled.
4. **Test devices `7/6 #1` and `7/6 #2`** should be deleted from the account.
5. **No VIN or plate in the GPS export** — the join to the HUB vehicle schedule runs through a manually maintained crosswalk, which needs an owner.

---

## 11. Open questions for Ed Walker (HUB)

1. ~~Three-period or four-period model?~~ **CLOSED — three-period model (P0/P1/P2).**
2. ~~Billing basis.~~ **CLOSED — leisure is billed per mile at a rate set in underwriting. FastLane reports verified mileage; the carrier applies the rate.**
3. ~~Leisure-day threshold.~~ **CLOSED — not applicable under per-mile billing.**
4. **Unconnected renters.** Does the carrier accept "unmatched defaults to P0," or is there a minimum Argyle coverage percentage below which the report isn't accepted? *(Now the highest-value open question — see §13.)*
5. **P1 rating.** Is P1 rated at the same per-mile rate as P2, or separately? This determines how much precision the P1/P2 boundary actually needs.
6. **Delivery format and cadence.** Workbook, CSV, or API feed? Monthly on the 10th acceptable?
7. **Retention.** How long must segment-level data be retained for claims support?
8. **Telematics system of record.** The method walkthrough references Samsara; the fleet runs OneStep. The algorithm is telematics-agnostic, but the system of record should be stated once and held consistently in all correspondence.

---

## 12. Note on scope

None of this requires a large system. It is roughly 800–1,200 lines of Python, a small database, and a scheduled job. The difficulty is not volume — it is that the **classification logic has to be exactly right and auditable**, because it is the basis of both an insurance filing and a customer invoice. Hire for judgment and testing discipline, not for framework fluency.


---

## 13. Per-mile billing changes the risk profile

Under a per-day charge, a misclassified hour rarely moved the bill. Under **per-mile billing, every misclassified mile is money**, and the error runs one direction: unverified time defaults to P0, and P0 is what FastLane pays for.

Two consequences to build around.

**Argyle coverage becomes a financial control.** A renter who connects Uber but not DoorDash has all their DoorDash time billed as leisure. A renter who connects nothing has 100% of their miles billed as leisure. Connection enforcement at pickup is not a compliance formality — it is the single largest lever on the monthly bill. Report coverage % per vehicle, review it weekly, and drive it to 100%.

**Pro-rata allocation is now the binding accuracy limit.** Splitting a drive segment's miles by time assumes constant speed across that segment. A segment mixing highway running and stop-and-go will misallocate at the margin. The summarized "drives and stops" export cannot do better than this. If OneStep exposes raw breadcrumb data, computing true distance per sub-window removes the approximation entirely and is worth doing once the pipeline is live.

Disclose the approximation to underwriting rather than waiting to be asked. A stated limitation is a sign of a controlled process; a discovered one is a reason to distrust every other number in the report.

---

## 14. Demonstrating capability to underwriting

The objective is not only to produce the report — it is to show that FastLane can produce it reliably. Four things carry that argument:

1. **The reconciliation control.** P0+P1+P2 ties exactly to total GPS miles, every vehicle, every month. Lead with this. It is the cheapest possible proof that nothing is being estimated or fudged.
2. **The worked example.** One driver, one day, every minute classified with its reason. This shows method rather than output.
3. **Stated limitations.** §13 and the disclosed-limitations block on the Methodology tab. Volunteering the approximation is what separates a process from a spreadsheet.
4. **The coverage metric.** Reporting verified vs unverified leisure says: we know what we can prove and what we cannot, and we are closing the gap. An underwriter has seen plenty of confident reports with no error bars.


---

## 15. Automation architecture

Manual reporting works at 15 vehicles. It does not work at 74, which is the 36-month target.

**The volume argument.** June 2026 produced 3,907 GPS drive segments across 15 active vehicles — roughly 260 per vehicle per month. At 74 vehicles that is **~19,300 drive segments per month**, joined against gig records from renters who may each generate 300–600 assignments monthly. Call it 40,000+ records to classify every month. That is not large data, but it is far past what anyone reconciles by hand, and it is a monthly deadline against an insurance filing.

The build should therefore target **zero manual steps except exception review**.

| Step | Today | Automated target |
|---|---|---|
| GPS data | Manual CSV export from OneStep | OneStep API if exposed; otherwise a scheduled export job. **Confirm API availability first** — it changes the effort materially |
| Gig data | Argyle Console, viewed per renter | Argyle webhooks writing continuously to Postgres |
| Rental assignments | Looked up in GHL | GHL API pull, nightly |
| Vehicle master / crosswalk | Maintained by hand | Maintained table with change history; the only genuinely manual artifact |
| Classification | Not yet running | Scheduled job, monthly plus on-demand |
| Report generation | Built by hand | Scheduled job producing workbook + CSV |
| Delivery to HUB | Manual email | Automated, with a human approval gate |
| Exception review | — | **The only human step.** Coverage gaps, unmapped devices, flagged records |

**Sequence matters.** Build the database and the ingestion first, then the classification, then the reporting. A pipeline that stores raw records lets you re-run any prior month when a rule changes or Argyle backfills late. A pipeline that only stores monthly totals cannot, and every restatement becomes a rebuild.

**Design for restatement from day one.** Argyle data arrives late; rules will change; underwriting will ask for a prior month recut on a new basis. Store raw, compute derived, and version the ruleset used for each issued report so any figure can be traced to the logic that produced it.

**The one thing not to automate.** Exception review. Coverage gaps, unmapped devices, and flagged records need judgment, and the moment they are silently auto-resolved the report stops being defensible.
