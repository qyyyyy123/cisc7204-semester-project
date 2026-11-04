# Peer review log

The CISC7204 portfolio brief asks for a classmate to review the portfolio and for an improvement
committed in response. This file records that review, the change it produced, and — separately — the
self-review that happened before it, so the two are not confused.

## Reviewer

| Field | Value |
|---|---|
| Reviewer | **LIUZIXUAN**, CISC7204 classmate |
| Date | 4 November 2026 |
| Material reviewed | The published portfolio site and this repository, opened cold, with no explanation from the author |
| Time budget | About five minutes |

## What the reviewer was asked to do

Three tasks, deliberately not "does it look nice":

1. Find the semester project from the home page in **two clicks or fewer**, and say what its research
   question is.
2. Open one figure and say what quantity **each axis** measures, including units.
3. Say whether the analysis could be rerun from the repository alone.

## What the reviewer reported

| Task | Answer (translated from Chinese) | Verdict |
|---|---|---|
| 1. Find the project and state its question | "I can find the semester project, and I can tell what the research question is." | Passed |
| 2. Read the axes and units off a figure | "The axes are longitude and latitude, and the unit is degrees." | Passed — and exposed a gap below |
| 3. Rerun the analysis from the repository alone | "It all runs." | Passed |

## The improvement this produced

Task 2 was answerable for the map she happened to open. Applying the same question to the other five
figures showed that **only two of the six captions on the project page actually stated their axes**;
the rest described the finding and left the reader to infer what was plotted. The marking rubric asks
for readable visualisations with clear titles, labels and units, so this was a genuine gap.

Every caption on
<https://qyyyyy123.github.io/data-science-portfolio/projects/xiamen.html> now names its x-axis, its
y-axis and the unit of each:

| Figure | Axis statement now given |
|---|---|
| 1 | x = ω (unitless), y = number of rides / cumulative share |
| 2 | x = longitude (degrees), y = latitude (degrees), colour = tidal index (unitless) |
| 3 | per slot: x = longitude (degrees), y = latitude (degrees), colour = tidal index (unitless) |
| 4 | x = longitude (degrees), y = latitude (degrees), colour = community label |
| 5 | x = actual pickups per 30 min (count), y = predicted pickups per 30 min (count) |
| 6 | x = increase in MAE (pickups per 30 min), y = feature |

**Commit:** `0301033` in the portfolio repository —
<https://github.com/qyyyyy123/data-science-portfolio/commit/0301033>

No number in this repository changed: `figures/`, `model_metrics.json` and the report are untouched
by that commit.

## Self-review (not peer feedback)

Before the site was shown to anyone, it was checked against the marking rubric and seven issues were
fixed. They are listed on the site's feedback page
(<https://qyyyyy123.github.io/data-science-portfolio/feedback.html>) so that the peer review above is
not credited with work it did not do. The two that touch this repository are:

| Issue found in self-review | Fix | Commit |
|---|---|---|
| The prediction figure said MAE 3.970 while the metrics table said 3.730 | Both correct; different fold populations. `tools/make_report.py` now emits the explanation, computed from `model_metrics.json`, so it cannot drift again | `4a6d985` — <https://github.com/qyyyyy123/xiamen-shared-bicycle-analysis/commit/4a6d985> |
| Nothing stated what the data licence allows | Licence, CC BY-NC 4.0 attribution, SHA-256 of the archive, explicit "not redistributed" statement | `c936ce8` (initial commit) |

## One suggestion declined

During self-review the idea of smoothing the Netflix growth curve came up, because it "looks like a
decline" at the end. Declined: the apparent decline is the dataset's truncated final year (its last
entry is 2021-09-25). Smoothing would hide the one thing that chart is most useful for — checking the
boundary of a dataset before reading a trend into it. Recorded here rather than quietly dropped.

## Responsible sharing check

Performed at the same time as the review:

* No passwords, API keys, tokens or `.env` files in either repository or anywhere in `git log`.
* No personal data: no email addresses, phone numbers, student ID in file names, or rider-level
  records. The bike-share data is event-level and contains no rider identifiers.
* The third-party archive is **not** redistributed: it is CC BY-NC 4.0, linked via its Zenodo DOI and
  hash-verified. Only derived aggregates are committed.
* No restricted or licensed dataset is exposed; the Netflix extract is a public snapshot committed
  with its notebook.
