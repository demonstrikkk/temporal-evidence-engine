# Demo video + product dossier — production plan

**Status:** draft for approval · **Date:** 2026-11-14 · **Rule version:** `tev-1.2.0`
**Owner:** _(you)_ · **Deliverables:** a 3-minute hero video, a 45-second cut, and an 8-page A4 PDF dossier

Nothing in this plan invents a number. Every figure in §9 was printed by a command in §9.3, at this
repo state, and is labelled as either **verdict gate** or **independent suite** — they are different
measurements and are never mixed (D-032).

---

## 0. The name

### Recommendation

| | |
|---|---|
| **Product name** | **Hindcast** |
| **Descriptor (always shown with it)** | claim-level verification |
| **Tagline** | Every claim, checked against when it changed. |
| **One-liner** | Paste an AI answer and the moment it was written. Every sentence comes back with a verdict, a dated quote, and the clock that decided it. |

Why this one:

- It says the thing the product actually does. A hindcast is a forecast made for a period that has
  already happened; this product asks what an answer was worth **as of the time it was written**, using
  the event date rather than the publication date. The metaphor is exact, not decorative.
- It is time-shaped, which is the defensible part of the engine. "Fact-checker" is a crowded word;
  "hindcast" is not, and it survives being said out loud once.
- It is professional rather than cute, and it does not compete with the four verdict words
  (`supported · changed · contradicted · unverifiable`), which stay the product's vocabulary.
- It works as a wordmark: `Hindcast` set in DM Serif Display, `CLAIM-LEVEL VERIFICATION` underneath in
  DM Mono — exactly the lockup the current header already uses.

### Alternates, if "Hindcast" is unavailable or too oblique

| Name | Reads as | Strength | Weakness |
|---|---|---|---|
| **As Of** | "supported as of 12 March" | The most intuitive option: it is the epistemic stance, in two words. Elegant. | Hard to search or trademark; reads like a column header. |
| **Afterfact** | "verified after the fact" | Instantly understood by any audience; ownable compound. | Slightly clunky aloud; trades elegance for clarity. |
| **Attest** | a formal declaration | Professional, verb, short. | Crowded: existing companies own the word and the .com. |
| **Trueline** | a line of truth in a page of type | Fits the editorial design. | Not a real word; easily confused with several fintech brands. |

**Check before committing:** domain, handle and a basic trademark search. I could not verify
availability from this environment (the search tool is returning HTTP 402), so treat all five names as
uncleared.

### If you adopt it, this is the entire rename surface

- `frontend/index.html`: `<title>`, `og:title`, `og:description`.
- `frontend/src/verify/VerifyShell.jsx`: the wordmark and its `CLAIM-LEVEL VERIFICATION` line.
- `frontend/src/home/Home.jsx`: the hero eyebrow already reads `Claim-level verification`; add the name above it.
- `frontend/public/og-cover.svg`, `frontend/public/tee-mark.svg`, the dossier cover.
- `PRODUCT.md` (add a "Display name" line), `README.md` first heading.
- `DECISIONS.md`: one new entry recording the name and date, so it is a decision rather than drift.

**Do not rename:** the repo, the Python package, the `/api` paths, the `tev` schema, or
`RULE_VERSION = tev-1.2.0`. `RULE_VERSION` versions the decision table — renaming it would claim a rule
change that did not happen. The footer chip `rule tev-1.2.0` stays.

---

## 1. What the demo has to prove

One sentence, and every frame serves it:

> **Paste an answer. See what survives.**

Three claims the video must land, in this order:

1. **The verdict is not a model's opinion.** Four outcomes, no fifth, and the one that matters —
   `changed` — is decided by an event date, not by a newer article.
2. **The quote is real.** It indexes into stored source text at a recorded offset, and the same
   characters are visible in `formatted` and in `source`.
3. **The system reports its own failures.** Failed sources, partial runs, abstentions and missing event
   dates are on screen, not averaged away.

If a beat does not serve one of those three, it is cut.

---

## 2. Green room — everything that must be true before the first take

### 2.1 Services

| Service | Check | Expected |
|---|---|---|
| API | `http://127.0.0.1:8000/api/health` | 200, `persistence.adapter` set |
| Bifrost | `.\scripts\bifrost_up.ps1`, then `/api/gateway` | key present, provider `mistral` reachable |
| Frontend | `http://127.0.0.1:5173` | 200, current build |
| Tavily | first run succeeds | `research` stage completes |

### 2.2 Data

- Copy the store before recording: `.tev/state.json` → `.tev/state.backup.json` (and the WAL/journal if
  present). Restore after, never edit in place.
- Decide the answer to verify **before** the shoot. Run three candidates, keep the one that produces all
  four statuses plus at least one `missing` event date. A `changed` verdict with a visible event date is
  non-negotiable — it is beat 1.
- Record the model on the paste form, so `/compare` has a measured row with the real model name.
- Do not delete failed runs (D-035). The `show N failed` control is a beat, not a cleanup problem.

### 2.3 Room and machine

- 2560×1440 display, browser at **100 %** zoom (Windows display scaling 100 %, not 125 %).
- Chrome guest profile: no extensions, bookmarks bar hidden, one tab, no other windows.
- Focus assist / notifications off, taskbar auto-hidden, desktop wallpaper plain.
- Fonts warmed: open the home page once and wait for DM Serif / Archivo / DM Mono to finish loading —
  they are fetched from Google Fonts and a late swap is visible in a recording.
- Microphone: one USB condenser or the laptop array, 48 kHz, 15 cm off-axis, room treated with a duvet
  behind you. Record 10 s of room tone for the noise floor.
- OBS: canvas 2560×1440, output 1920×1080 (Lanczos), 60 fps, CQP 18–20, NVENC if available, MKV then
  remux to MP4.

### 2.4 The honesty rules for the edit

- A replayed run shows `snapshot` with its original timestamp. **Never crop it out.** If a take is
  replayed from cache, the label is part of the shot.
- A speed-ramped wait says so on screen (`4×` chip in the corner). A run does not get to look instant.
- A failed stage stays in the frame if it is the take you used — or you cut around it honestly, never
  re-take to hide it.
- No invented numbers, no invented customers, no "trusted by".

---

## 3. Story spine

| Beat | Screen | The line it carries |
|---|---|---|
| 0 · Overture | `/` home hero + dossier | "Every claim leaves a trace." |
| 1 · Problem | `/` scene 01 — *One answer. Four clocks.* | A publication date is not an event date. |
| 2 · Intake | `/check` | You bring the answer, the time, and who wrote it. |
| 3 · Work | `/runs/:id` while `running` | Research happens in stages you can watch. |
| 4 · Verdicts | run view: answer pane → claim table → inspector | Four outcomes; the quote is real; the clock decides. |
| 5 · Honesty | run view + `/runs` failed filter | Failures are reported, not deleted. |
| 6 · Scale | `/compare` | What the models actually did, including the ones that aren't models. |
| 7 · Instrument | `/playground` | Ask a model, then scan its answer without leaving the page. |
| 8 · Close | `/` finale | "Paste an answer. See what survives." |

---

## 4. Shot list — hero cut, 3:05

Times are the **edit**, not the capture. Each take is recorded clean and long; the cut does the timing.

### 4.1 Cold open — 0:00–0:12

- Screen: `/`, no scroll. The hero is in frame with the dossier card on the right.
- Action: click **▶ Play** in the dossier header. The four verdict tabs advance on their own
  (`case file 02 / 04`). Let one full cycle run, then stop on `changed`.
- On-screen text: `HINDCast` wordmark is already there — add nothing.
- VO: *"An AI answer is a confidence trick. It reads as one paragraph. It is actually twenty claims,
  each with its own expiry date."*

### 4.2 The problem — 0:12–0:30

- Screen: scroll to scene 01, *One answer. Four clocks.* Hold on the clock grid; the `fact changed`
  row carries the accent, and `missing` is visible on one case.
- Action: hover `fact changed`, do not click.
- On-screen lower third: `publication date ≠ event date`.
- VO: *"Most checkers compare an answer to the web as it is now. That answers a different question. The
  question here is what the answer was worth when it was written — so the engine keeps four clocks
  apart, and only the event date is allowed to make a claim stale."*

### 4.3 Intake — 0:30–0:52

- Screen: `/check`.
- Action: click a specimen chip (`price`), then type over one sentence so it is visibly a human editing,
  fill **Model that wrote it**, and set **Answer generated at**. Hover the model field once so the hint
  under it is readable.
- On-screen text: `recorded, not guessed` beside the model field.
- VO: *"You bring three things: the answer, when it was generated, and who wrote it. The model name is a
  recorded fact — if you leave it blank, the engine says so later rather than guessing."*
- Note: do **not** type a real API key anywhere. The playground is keyless by design; the paste form
  never wanted one.

### 4.4 The work — 0:52–1:14

- Screen: submit → run view in `running`.
- Action: let the trace waterfall fill. Point at one tool call row and open it. If the wait exceeds ~12 s
  of screen time, apply a visible `4×` chip and keep the clock moving.
- On-screen text: `every stage is a row` + the run's own clock.
- VO: *"The run is not a progress bar. Every stage writes a durable event with a sequence number: what it
  searched, what it fetched, what it stored, who it asked, what it cost. If a source fails, the failure
  is a row too."*

### 4.5 The answer, with marks — 1:14–1:34

- Screen: run view, answer pane in `formatted`.
- Action: hover one claim mark so the highlight lands on the rendered words; then toggle `source`.
- On-screen text: `the same characters, twice`.
- VO: *"Claim marks are drawn on the formatted text — headings, tables, bold, all intact — because they
  are computed from stored offsets, not by searching the rendered string. Toggle to source and the same
  characters are underlined in the raw answer."*
- Capture note: 125–140 % browser zoom on this pane only; hold 2 s per state.

### 4.6 The verdict that matters — 1:34–2:02

- Screen: claim table → open the `changed` claim.
- Action: read top to bottom in one slow pass: verdict stamp + reason code → quote block with the stored
  revision and source → the four clocks (answer generated / source published / **fact changed** /
  retrieved) → the settle line `proposal → settlement`.
- Then click one `unverifiable` claim and hold on `missing`.
- On-screen text: `the model proposes · the code decides`.
- VO: *"Here is the whole product in one card. The model proposed a relationship. The code checked the
  quote against the exact stored revision, read the event date, and settled the status. One function in
  the entire codebase is allowed to grant support — and a failed support is not a contradiction; that
  needs its own cell and its own quote."*

### 4.7 Honesty beat — 2:02–2:16

- Screen: `/runs` with the failed filter intact, then back to the run's partial/failed event if one
  exists.
- Action: click `show N failed` so the hidden runs appear and stay visible for a beat.
- VO: *"Runs that failed are hidden from the default view and never deleted — one control brings them
  back. A failed source is a failed source, and a partial run says `partial`."*

### 4.8 Compare — 2:16–2:40

- Screen: `/compare`, default `Evidence` view.
- Action: hold on the matrix. Point at the `pasted answers` row, then the `CALLS` header to sort once,
  then the average-cost and `due` columns.
- On-screen text: `measured · pasted · catalogue` (the three bands).
- VO: *"Which model actually answered well is a measurement, not a vibe. Calls, tokens, average cost and
  verdict mix — and a fourth band for answers that were pasted with no model recorded, because the
  scanner did not write them and the engine will not pretend it did. Catalogue entries are context, and
  they stay in their own band."*

### 4.9 Instrument — 2:40–2:55

- Screen: `/playground`.
- Action: type a short question, send, let Mistral answer (the `keyless` preset tag is in frame), then
  click **Scan this answer**.
- VO: *"The playground asks a model and then scans what it said — same pipeline, same verdicts, no key to
  paste."*

### 4.10 Close — 2:55–3:05

- Screen: home finale, *Paste an answer. See what survives.* Slow fade to paper.
- End card: name, descriptor, one URL.
- VO: *"Four verdicts. No fifth. Every claim leaves a trace."*

### 4.11 The 45-second cut

Problem (0–8) → paste (8–18) → `changed` card (18–30) → compare bands (30–38) → close (38–45).
No playground, no trace, no honesty beat. This is the social cut; it must work with sound off, so burn
the captions in.

---

## 5. Voiceover script (hero cut, ~450 words ≈ 3:00 at 150 wpm)

> **[0:00]** An AI answer is a confidence trick. It reads as one paragraph. It is actually twenty claims,
> each with its own expiry date.
>
> **[0:12]** Most checkers compare an answer to the web as it is now. That answers a different question.
> The question here is what the answer was worth when it was written — so the engine keeps four clocks
> apart, and only the event date is allowed to make a claim stale.
>
> **[0:30]** You bring three things: the answer, when it was generated, and who wrote it. The model name
> is a recorded fact. If you leave it blank, the engine says so later rather than guessing.
>
> **[0:52]** The run is not a progress bar. Every stage writes a durable event with a sequence number:
> what it searched, what it fetched, what it stored, who it asked, what it cost. If a source fails, the
> failure is a row too.
>
> **[1:14]** Claim marks are drawn on the formatted text — headings, tables, bold, all intact — because
> they are computed from stored offsets, not by searching the rendered string. Toggle to source and the
> same characters are underlined in the raw answer.
>
> **[1:34]** Here is the whole product in one card. The model proposed a relationship. The code checked
> the quote against the exact stored revision, read the event date, and settled the status. One function
> in the entire codebase is allowed to grant support — and a failed support is not a contradiction; that
> needs its own cell and its own quote.
>
> **[2:02]** Runs that failed are hidden from the default view and never deleted. One control brings them
> back. A failed source is a failed source, and a partial run says partial.
>
> **[2:16]** Which model actually answered well is a measurement, not a vibe. Calls, tokens, average cost
> and verdict mix — and a fourth band for answers that were pasted with no model recorded, because the
> scanner did not write them and the engine will not pretend it did. Catalogue entries are context, and
> they stay in their own band.
>
> **[2:40]** The playground asks a model and then scans what it said — same pipeline, same verdicts, no
> key to paste.
>
> **[2:55]** Four verdicts. No fifth. Every claim leaves a trace.

**Delivery notes:** slower than feels right; land the numbers as facts, not as sells. "No fifth" is the
only line that gets emphasis. Take each paragraph as a separate file so a single bad sentence never
forces a re-record.

**45-second cut VO (~110 words):** *"An AI answer is a confidence trick: one paragraph, twenty claims,
each with an expiry date. Paste it with the time it was written, and every sentence comes back with a
verdict — supported, changed, contradicted, unverifiable — a dated quote from the stored source, and the
event date that decided it. Not the publication date: the event. Which model answered well is measured,
including the answers that were pasted with no model recorded. Four verdicts. No fifth."*

---

## 6. Capture recipe

1. **Warm take, not a first take.** Run the chosen answer once off-camera so DNS, TLS, Tavily and Bifrost
   are warm. Keep the run (do not delete it) and hide it later only if it clutters `/runs`.
2. **One scene per recording.** Stop OBS between scenes; do not try to drive a 3-minute take. Each take
   gets a slate: clap, say the scene number, then perform.
3. **Mouse discipline.** Move in straight lines, pause 400 ms before every click, never circle. Park the
   cursor away from text when you stop moving.
4. **Scroll in one gesture.** Use the wheel with a constant rate; no trackpad inertia, no scrollbar drags.
5. **Live vs snapshot.** Record the run **live** for the A-roll if it completes inside ~90 s. If it
   doesn't, record the snapshot replay but keep the `snapshot` label in frame and say "replayed from the
   cache" in the VO. Both are honest; only a hidden label is not.
6. **Two cameras of the same screen.** OBS records the full 2560×1440 canvas; also take Playwright
   screenshots at 2× device scale of the four key states (claim card, compare matrix, paste form, running
   trace) for the PDF and the video's insert shots. Text stays crisp when a still is pushed in.
7. **Room tone** at the head of every session, 10 s.

---

## 7. Edit recipe

- **Cuts:** hard cuts. One dissolve only, at 4.10 into the end card. No zoom-punch transitions.
- **Text:** lower thirds in DM Mono, 11–13 px equivalent, bone on ink, one line, ≤ 6 words. Never more
  than one on screen at a time.
- **Highlight:** use a vermilion underline reveal (2 px, 180 ms) rather than a circle or an arrow.
- **Captions:** burned-in for the 45 s cut; `.srt` sidecar for the hero cut.
- **Music:** none, or a single sustained low bed at −28 LUFS under the VO. Silence is on-brand.
- **Loudness:** −16 LUFS integrated, true peak −1.5 dBTP.
- **Speed ramps:** allowed only over dead time, always with a visible `N×` chip, never over a verdict or a
  quote.
- **Exports:** `hero-1080p.mp4` (H.264 High, 1920×1080, 60 fps, ~14 Mbps, AAC 192 kbps), `hero-4k.mp4`
  (HEVC, optional), `cut-45s-1080p.mp4`, `cut-45s-vertical.mp4` (1080×1920, reframed not letterboxed),
  plus a `poster.jpg` from 4.6.

---

## 8. Failure contingencies

| What happens | What you do |
|---|---|
| Live run exceeds 90 s | Keep it live, speed-ramp 4× with the chip, VO over it. Do not fake it. |
| A source fails mid-take | Keep the take. That is beat 5, and it is better than a clean run. Say what failed. |
| Tavily or Bifrost is down | Stop. Do not record a mocked success. Fix the dependency or record the snapshot take and label it. |
| A claim comes back `unverifiable` that you expected to settle | Keep it. `missing` event date is a feature; use the inspector to show why. |
| Compare page shows no measured row | The pasted band is still a beat, but a measured row is better — run the playground once so a model gets credited. |
| Fonts swap mid-take | Retake after a warm reload; the swap is visible and reads as a bug. |

---

## 9. Evidence bank — the only numbers allowed on screen

### 9.1 Verdict gate (the release gate)

From `backend\.venv\Scripts\python.exe eval\run_eval.py`, current run:

- **Gate: PASS**
- status accuracy **100 %**; false `supported` **0**; false `changed` **0**
- abstention recall **100 %**; over-abstention **0**
- quote validity **100 %** (136 quotes)
- event-date accuracy **100 %** (44 checks)
- recorded model cost **$0.0000** on the gold set (snapshot cache)
- one non-gating note: `scope-only-ivy` returns reason `no_source` where the gold expects
  `scope_mismatch`; the status matches.

### 9.2 Independent assertion-coverage suite (a *different* measurement)

From `backend\.venv\Scripts\python.exe eval\independent\run.py`, exits 0:

- 29 cases, 57 assertions, **53 material in scope**
- material recall **32/53 = 60.4 %**, partial 64.1 %, precision **88.9 %**, **F1 71.9 %**
- omission rate 39.6 %, false-candidate rate 2.8 %
- 0 model calls, 0 Tavily calls, 0 network calls — it is offline by construction

**Never quote the extraction suite's 100 % as real-world recall.** It is circular. If the video or the
PDF shows a percentage, it is 60.4 % with the sentence that says what it measures.

### 9.3 Local store, as of this state

From `GET /api/compare/models` on the running API:

- 15 rows: 3 measured models, 17 pasted answers, the rest catalogue entries
- 155 recorded calls, 300,261 input tokens, 25,455 output tokens, **$0.016967** total, **$0.000109**
  average per call
- 47 claims: 23 supported, 0 changed, 9 contradicted, 10 unverifiable, 5 pending; **26 due** for re-check
- supported rate 54.8 % across all claims (a mix of gold and historical answers, not a benchmark)

### 9.4 Suite state

`backend\.venv\Scripts\python.exe -m pytest backend\tests -q` → **2703 passed, 17 skipped**.
`npm test` → 8 files, `# fail 0`. `npm run build` → built.

### 9.5 Prohibited on screen and in the PDF

- Any "accuracy" figure without its measurement named.
- Any customer, logo, or "trusted by".
- Any latency or cost number from a different machine without saying so.
- Any screenshot taken with the failed-filter hiding a failure you then describe as clean.

---

## 10. The PDF dossier

### 10.1 What it is

An 8-page A4 portrait document that a reader can hold without a narrator: problem, mechanism, decision
table, one worked claim, measured results, the comparison page, operating limits, and a back page. It
is the video's evidence, not a brochure.

It reuses the home page's own structure — the dossier card (`case file 02 / 04`) is already a designed
explainer, and its four verdicts, four clocks and settle line map directly onto pages 3–5.

### 10.2 Page plan

| Page | Content | Assets | Copy budget |
|---|---|---|---|
| **P0 Cover** | Wordmark, tagline, descriptor, `rule tev-1.2.0`, date, "prepared for …" | `tee-mark.svg` or wordmark lockup | 25 words |
| **P1 Problem** | One paragraph: an answer is a set of claims with expiry dates; publication ≠ event | optional: the four-clock strip | 180 words |
| **P2 Mechanism** | Five steps: extract → research → store revision → quote → settle. The line *the model proposes; code decides.* | one diagram (vector, not screenshot) | 220 words |
| **P3 Verdicts** | The decision table R1–R7 exactly as the frozen panel shows it, plus what each status means and what it does *not* mean | table rendered from the app's own list | 260 words |
| **P4 One worked claim** | The full claim card: verdict + reason, quote with highlight, source url/title, four clocks, settle line | screenshot at 2× + a vector redraw of the quote | 200 words |
| **P5 Measured results** | Verdict gate block, independent-suite block, and the sentence that keeps them apart | two small tables | 240 words + 2 tables |
| **P6 Comparison** | The matrix: three bands, average cost, due count, attribution rule | screenshot at 2×, crops per band allowed | 220 words |
| **P7 Operations & limits** | What runs where (API, Bifrost, Postgres, Tavily); cache modes; no-silent-failure events; single-tenant, no auth by default; what is deliberately out of scope | one small topology diagram | 260 words |
| **P8 Back** | Repo/URL + QR, license (MIT), seeded-from notice, contact, rule version, "every claim leaves a trace" | QR, wordmark | 60 words |

### 10.3 Design and print spec

- **Format:** A4 portrait (210×297 mm), 14 mm margins, 12-column grid, 6 mm baseline. US Letter variant
  only if you ask.
- **Palette:** the same tokens — bone `#efe6d6`, paper-2, vermilion `#b02a1c`, ember `#d66422`, ink.
  No new colours. Print in RGB; if the printer demands CMYK, convert once at the end and re-check the
  vermilion (it muddies).
- **Type:** DM Serif Display for titles, Archivo for body, DM Mono for labels and code. **Self-host the
  fonts** in the dossier folder — the app currently loads them from Google Fonts, and a PDF must not
  depend on the network at render time.
- **Figures:** screenshots at 2× device scale (retina), placed at ≥ 240 dpi effective. Redraw the quote
  and the clock grid as vectors where possible; text in a screenshot at 100 % is the first thing that
  looks cheap in print.
- **Rules:** repeated hairline rules, page numbers in DM Mono bottom-right, running head with the section
  name. No drop shadows in print.
- **Metadata:** title, author, subject, keywords, `rule tev-1.2.0`, creation date.
- **Accessibility:** Chromium's `page.pdf()` produces an untagged PDF. If the recipient needs a tagged,
  screen-reader-usable PDF, render with WeasyPrint or produce it from Word/InDesign instead. Decide this
  before layout, not after.

### 10.4 How it gets built

One source file, one stylesheet, one command — no desktop publishing app in the loop:

```
design/dossier/
  index.html          # P0–P8, semantic sections, one page per <section class="page">
  print.css           # @page, tokens, type scale, grid, page breaks
  fonts/              # DM Serif Display, Archivo, DM Mono (OFL, embeddable)
  figures/            # 2x screenshots + SVG diagrams
scripts/build_dossier.py   # Playwright -> PDF, then metadata + page count check
```

```python
# scripts/build_dossier.py (sketch)
page.pdf(path="Hindcast-dossier.pdf", format="A4",
         print_background=True, prefer_css_page_size=True,
         margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
```

Playwright is already installed in `backend/.venv`, so the build needs no new dependency. The script
should fail loudly if the page count is not 9 (cover + 8), if a figure is missing, or if any text node
still contains a placeholder token like `{{`.

### 10.5 What you must provide (the intake sheet)

Everything below has a default, so the document can be built without you — but each default is a guess
you should overrule.

| # | Field | Needed for | Default if unanswered |
|---|---|---|---|
| 1 | Final product name | everywhere | **Hindcast** |
| 2 | Tagline | cover, OG, end card | Every claim, checked against when it changed. |
| 3 | Audience | tone of P1, depth of P5 | technical evaluator (engineer or research lead) |
| 4 | Purpose | what P8 asks the reader to do | evaluate the engine, not buy it |
| 5 | Author name, email, URL, QR target | cover, P8 | repo URL only |
| 6 | Legal entity / license line | P8 | "MIT · seeded from DataGoblin (MIT)" |
| 7 | May the 60.4 % appear? | P5 | **yes**, with the sentence that names the measurement |
| 8 | Screenshots: approve or re-shoot | P4, P6 | I generate them, you approve |
| 9 | Paper size | print | A4 portrait, RGB |
| 10 | Tagged/accessible PDF required? | build tool | no (Chromium) |
| 11 | 16:9 deck version too? | delivery | no |
| 12 | Logo/mark final | cover | existing `tee-mark.svg` |
| 13 | Date line | cover | 2026-11-14 |
| 14 | Anything you will *not* let us claim | P5, P7 | nothing beyond §9.5 |

Answer 1–7 and the rest can be built in a day.

### 10.6 Acceptance checks before you send it

- 9 pages, no orphan headings, no widow lines, no page with < 40 % fill.
- Every figure ≥ 240 dpi effective, no JPEG artefacts on text.
- All links live; QR resolves to the same URL as the printed one.
- Every number on P5 matches §9.1/§9.2 verbatim, and the two blocks are visually separated.
- Text is selectable and searchable; file opens in Acrobat, Preview and a phone viewer.
- File size under 12 MB; fonts embedded (subset).
- Read once on a phone at 100 % — if a table is unreadable there, it is unreadable in print too.

---

## 11. Schedule (solo, realistic)

| Block | Work | Output |
|---|---|---|
| ½ day | Name decision, specimen hunt (3 candidate answers), data snapshot, green room | chosen name, chosen answer, backup store |
| ½ day | Rehearse the click path 3× without recording; fix any layout surprises | a repeatable path |
| 2 h | Capture: home, problem, intake, run, claim card, compare, playground, close | ~40 min of raw |
| 2 h | VO record, paragraph by paragraph | 10 clean audio files |
| 3 h | Edit hero cut + 45 s cut + vertical, captions, loudness | 3 exports |
| ½ day | Dossier copy from this document's evidence bank, figures at 2× | `index.html`, figures |
| 2 h | PDF build, print check, phone check | final PDF |
| 2 h | Buffer for retakes | — |

The only hard dependency: the claim card and the compare matrix screenshots must be taken **after** the
data snapshot is frozen, or the PDF and the video will disagree.

---

## 12. Copy blocks (reuse verbatim)

- **Tagline:** Every claim, checked against when it changed.
- **One-liner (25 words):** Paste an AI answer and the moment it was written. Each sentence returns with
  a verdict, a dated quote from stored source text, and the clock that decided it.
- **Short (50 words):** An AI answer is a set of claims, and each one has an expiry date. Paste the answer
  and its generation time; the engine extracts atomic claims, researches them, stores every source
  revision, and settles a verdict per claim — supported, changed, contradicted or unverifiable — from
  dates and quoted text, never from a model's opinion.
- **Long (100 words):** Most fact-checkers compare an answer to the web as it is now, which answers a
  different question than "was this true when it was written". This engine keeps four clocks apart —
  answer generated, source published, fact changed, retrieved — and lets only the event date make a claim
  stale. A model proposes the relationship between a claim and a quote; deterministic code settles it
  against the exact stored revision. Four outcomes, no fifth, and a re-check clock so a supported claim
  does not quietly rot. Failures are reported as rows, not deleted.
- **Meta description (154 chars):** Paste an AI answer and when it was written. Every claim returns with
  a verdict, the exact quoted source text, and the event date that decided it.

---

## 13. Decisions this plan needs from you

1. **Name:** Hindcast, or one of the alternates. Nothing gets renamed until you choose.
2. **Publish the 60.4 %?** I recommend yes — it is the most credible thing in the document.
3. **Live or snapshot for the hero run**, if the live run cannot hold under 90 s on the day.
4. **Tagged PDF or not.**
5. **Audience.** Investor, hiring manager and academic reader want different page 1s.
