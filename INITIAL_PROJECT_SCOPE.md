# TARP Tracker - Initial Project Scope

Date: 2026-07-09

## 1. Objective
Build a lightweight GitHub-hosted tracker that periodically checks Tennessee TWRA TARP participant totals and detects changes.

Primary goals:
- Monitor the published TARP participant record count (example: "1 to 10 of 9,826 records").
- Notify when the count changes.
- Bonus: detect row-level dataset changes and produce a diff report.
- Keep the latest dataset snapshot in-repo so changes are trackable over time.

## 2. Current Inputs
- Existing local snapshot file:
  - `Tennessee Angler Recognition Program (TARP).csv`
- TWRA page:
  - https://www.tn.gov/twra/fishing/tennessee-angler-recognition-program/#summary
- Likely machine data source found in page config:
  - `/twra/fishing/tennessee-angler-recognition-program/_jcr_content/contentFullWidth/tn_complex_datatable.exceldriven.json`

## 3. Proposed Architecture
- Repository type: static repo with GitHub Actions automation.
- Scheduler: GitHub Actions `cron` (for example daily or weekly).
- Data collection strategy:
  - Preferred: fetch the underlying JSON endpoint directly (more stable than scraping rendered DOM text).
  - Fallback: parse count from page HTML if JSON source is unavailable.
- Change detection:
  - Compare newly fetched dataset with previous committed snapshot.
  - Derive:
    - record_count_old -> record_count_new
    - added rows
    - removed rows
    - changed rows (if same key but changed fields)
- Persistence:
  - Commit updated dataset and summary artifacts only when changes occur.
- Notification:
  - Create/update GitHub Issue when a change is detected.
  - You can subscribe to issue notifications for email delivery.
- GitHub Pages:
  - Publish a simple status page with:
    - latest count
    - last checked timestamp
    - last changed timestamp
    - recent change summary

## 4. Suggested Repository Layout
- `.github/workflows/tarp-check.yml`
- `scripts/check_tarp.py` (or JS equivalent)
- `data/current.json` (latest normalized dataset)
- `data/history/` (optional dated snapshots)
- `reports/latest_diff.md`
- `docs/index.html` (GitHub Pages status page)
- `README.md`

## 5. Change Rules (Draft)
A change event is triggered if any of these happen:
- Total record count changes.
- One or more records are added or removed.
- One or more existing records have field-level changes.

No change event:
- Source is unavailable and previous data remains unchanged.
- Pure ordering differences only (if records are normalized and sorted before diff).

## 6. Open Questions for You
1. Check frequency:
- How often should GitHub Action run? (`daily`, `twice daily`, `weekly`, specific UTC times)

2. Notification behavior:
- Prefer one rolling issue that gets commented on, or create a brand-new issue per detected change?

3. Diff detail level:
- Should issues include full row-level added/removed records, or only a summary count plus a link to a diff artifact?

4. Snapshot policy:
- Keep only `data/current.json`, or also store dated snapshots in `data/history/`?

5. Pages scope:
- Should GitHub Pages show only latest status, or also a small change history table?

6. Source preference:
- Is it acceptable to rely primarily on the hidden JSON endpoint if stable, with fallback to page scrape?

7. Technology preference:
- Any preference between `Python` and `Node.js` for the checker script?

8. Triggering:
- Should there also be a manual "Run workflow" trigger in addition to schedule?

9. Failure handling:
- If TWRA endpoint fails (timeout or schema change), should we open an issue immediately or only log failure in Action run?

## 7. Initial Non-Goals
- Building a complex web app UI.
- Sending email directly through SMTP.
- Real-time monitoring (GitHub Actions schedule only).

## 8. Risks and Mitigations
- Site structure changes:
  - Mitigation: prefer API-like JSON endpoint, add fallback parser, add clear error messages.
- False positives due to ordering changes:
  - Mitigation: normalize and sort records before comparison.
- Data quality inconsistencies:
  - Mitigation: canonicalize whitespace, case, and null-like values before diffing.

## 9. Acceptance Criteria (Draft)
- Scheduled workflow runs automatically.
- On no data change, workflow exits cleanly with no issue noise.
- On data change, workflow:
  - updates snapshot files,
  - commits changes,
  - creates or updates notification issue,
  - updates GitHub Pages status artifact.
- README explains setup, schedule, and how notifications work.
