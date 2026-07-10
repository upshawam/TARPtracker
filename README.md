# TARP Tracker

Automated tracker for the Tennessee Angler Recognition Program (TARP) participant data.

It runs daily in GitHub Actions, checks TWRA's published TARP dataset, and then:
- Detects count and row-level changes
- Writes full diff details (added, removed, changed rows)
- Stores a historical snapshot when changes occur
- Posts updates to a rolling GitHub issue (email notifications via issue subscription)
- Opens/updates a failure issue when checks fail
- Updates GitHub Pages status content in `docs/index.html`

## Scheduled Run Time
The workflow runs daily at `11:15 UTC`.
- This is overnight for US Central and typically appears by morning.
- You can also run it manually from the GitHub Actions tab using `Run workflow`.

## Data Source Strategy
The checker fetches TWRA data from the JSON endpoint used by the page's table.
- Primary: JSON endpoint discovered from the page config
- Fallback: default known JSON endpoint path

Direct endpoint:
- https://www.tn.gov/twra/fishing/tennessee-angler-recognition-program/_jcr_content/contentFullWidth/tn_complex_datatable.exceldriven.json

## Repository Files
- `.github/workflows/tarp-check.yml` - daily automation workflow
- `scripts/check_tarp.py` - fetch/normalize/diff logic
- `data/current.csv` - latest dataset snapshot
- `data/current_meta.json` - latest metadata (counts and timestamps)
- `data/change_history.json` - dated history entries and added rows
- `data/history/` - full CSV snapshots on change events
- `reports/latest_diff.md` - latest full row-level diff report
- `reports/run_result.json` - machine-readable result from the last run
- `docs/index.html` - GitHub Pages status page

## Notification Model
Two issues are used:
- `TARP Tracker: Rolling Change Log`
  - Gets a new comment for every detected change
  - Subscribe to this issue to receive email notifications
- `TARP Tracker: Failure Alert`
  - Created/updated when a run fails

## Enable GitHub Pages
1. In repository settings, open Pages.
2. Set source to deploy from branch.
3. Choose your default branch and `/docs` folder.
4. Save.

## Initial Baseline
This repo is seeded from your attached baseline CSV (`9826` records).
The first run compares current TWRA data against that baseline.

## Local Run (Optional)
If you want to test locally:

```powershell
python scripts/check_tarp.py
```

Then inspect:
- `reports/latest_diff.md`
- `data/current_meta.json`
- `docs/index.html`

## Notes on Storage
History is retained by committing snapshots only when changes occur.
For this dataset size and likely update cadence, this should stay manageable for GitHub repository limits.
