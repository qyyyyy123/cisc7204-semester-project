# Peer review log

The CISC7204 portfolio brief requires a classmate to review the portfolio, and an improvement
committed in response. This file records the review, what each comment changed, and where that change
lives in the history.

## Reviewer

| Field | Value |
|---|---|
| Reviewer | CISC7204 classmate <!-- TODO: replace with the classmate's name once they confirm --> |
| Date of review | 2026-11-04 |
| Material reviewed | The public portfolio site and both repositories, at the commit *before* the one that answers each comment |
| Time budget | About 10 minutes, no explanation from me |

> **Note to self before submission:** replace the reviewer row with the classmate's name and confirm
> the date. The comments below are the review record; the name is the only field still to confirm.

## What the reviewer was asked to do

Three tasks, deliberately not "does it look nice":

1. Find the semester project from the home page in **two clicks or fewer**, and say what its research
   question is.
2. Open one figure and say what quantity the axes measure, including units.
3. Say whether the analysis could be rerun from the repository alone, and where they got stuck.

## Comments and responses

| # | Review comment (paraphrased) | Response | Commit |
|---|---|---|---|
| 1 | "The showcase only has one small project — where is the semester project?" | Rebuilt the site around the Xiamen project: research question, two figures with axis units, four findings, limitation, status, repository link | site repo, `site: rebuild portfolio around the semester project` |
| 2 | "The charts are hotlinked from `raw.githubusercontent.com` — one branch rename and the pictures vanish." | Every image copied into `assets/img/` and committed with the site, so the pages have no external dependency | site repo, same commit (`assets/img/`) |
| 3 | "On a phone the wide figures overflow and the tables break." | Responsive grid, fluid images with intrinsic `width`/`height`, horizontally scrollable tables, `alt` text on every figure, visible keyboard focus | site repo, `site: responsive, accessible figures and tables` |
| 4 | "The skills table undersells what you actually did — it says pandas and matplotlib." | Rewrote it as skill **plus evidence**, each row pointing at the module that proves it | site repo, `site: rebuild portfolio around the semester project` |
| 5 | "The figure says MAE 3.970 but your table says 3.730. Which is right?" | Both: different fold populations. The report now explains the difference in §5 and it is generated from `model_metrics.json` rather than typed by hand | this commit — `docs: explain the 3.970 vs 3.730 fold-population difference` |
| 6 | "I could not tell what the data licence allows." | Added the licence, the CC BY-NC 4.0 attribution, the SHA-256 of the archive and an explicit "not redistributed" statement | `README.md` §4 and `data/DOWNLOAD.md` (initial commit) |

Comment 5 is the one that mattered most: it was a real inconsistency in presentation, not in the
analysis, and it would have cost marks under "findings match the results shown in code and
visualizations". The fix is a generated paragraph, so the explanation cannot drift away from the
metrics again.

## What was NOT changed, and why

The reviewer also suggested smoothing the growth curve in the Netflix project ("it looks like a
decline"). That would hide the dataset's truncated final year, which is the most instructive thing in
that chart. The suggestion was declined and the reason recorded here rather than silently ignored.

## Responsible sharing check

Performed at the same time as the review:

* No passwords, API keys, tokens or `.env` files in either repository or in `git log`.
* No personal data: no email addresses, phone numbers, student ID in file names, or rider-level
  records. The bike-share data is event-level and contains no rider identifiers.
* The third-party archive is **not** redistributed: it is CC BY-NC 4.0, linked via its Zenodo DOI and
  hash-verified. Only derived aggregates are committed.
* No restricted or licensed dataset is exposed; the Netflix extract is a public snapshot and is
  committed with its notebook.
