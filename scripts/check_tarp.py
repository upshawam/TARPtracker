#!/usr/bin/env python3
"""Fetch TWRA TARP data, detect changes, and update local artifacts."""

from __future__ import annotations

import csv
import datetime as dt
import html
import json
import os
import re
import socket
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
HISTORY_DIR = DATA_DIR / "history"
REPORTS_DIR = ROOT / "reports"
DOCS_DIR = ROOT / "docs"

CURRENT_CSV = DATA_DIR / "current.csv"
CURRENT_META = DATA_DIR / "current_meta.json"
CHANGE_HISTORY = DATA_DIR / "change_history.json"
LATEST_DIFF = REPORTS_DIR / "latest_diff.md"
RUN_RESULT = REPORTS_DIR / "run_result.json"

SOURCE_PAGE_URL = "https://www.tn.gov/twra/fishing/tennessee-angler-recognition-program/#summary"
DEFAULT_AJAX_PATH = "/twra/fishing/tennessee-angler-recognition-program/_jcr_content/contentFullWidth/tn_complex_datatable.exceldriven.json"
RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}

COLUMNS = [
    "Angler's Name",
    "Kind of Fish (Species)",
    "Length of fish",
    "Body of Water Caught",
    "County",
    "Date Caught",
]

ANGLER_NAME_KEY = "Angler's Name"

UA = "TARPtracker/1.0 (+https://github.com/)"


class TrackerError(RuntimeError):
    pass


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso_z(value: dt.datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch_text(url: str, timeout: int = 30, attempts: int = 5, base_delay: float = 1.5) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            "Connection": "close",
        },
    )

    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")

        except HTTPError as exc:
            last_exc = exc
            if exc.code not in RETRYABLE_HTTP or i == attempts - 1:
                raise TrackerError(f"Request failed for {url}: {exc}") from exc

        except URLError as exc:
            last_exc = exc
            reason = getattr(exc, "reason", exc)
            retryable = isinstance(reason, (ConnectionResetError, TimeoutError, socket.timeout, OSError))
            if not retryable or i == attempts - 1:
                raise TrackerError(f"Request failed for {url}: {exc}") from exc

        delay = base_delay * (2**i)
        time.sleep(delay)

    raise TrackerError(f"Request failed for {url}: {last_exc}")


def fetch_json(url: str, timeout: int = 30) -> Any:
    text = fetch_text(url, timeout=timeout)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise TrackerError(f"Invalid JSON from {url}: {exc}") from exc


def extract_ajax_path(page_html: str) -> str | None:
    # Try data-config first because this page stores table config as escaped JSON.
    match = re.search(r'data-config="([^"]+)"', page_html)
    if match:
        decoded = html.unescape(match.group(1))
        try:
            cfg = json.loads(decoded)
            ajax_path = cfg.get("ajax") if isinstance(cfg, dict) else None
            if isinstance(ajax_path, str) and ".json" in ajax_path:
                return ajax_path
        except json.JSONDecodeError:
            pass

    # Fallback patterns in case the page embeds ajax directly.
    patterns = [
        r'"ajax"\s*:\s*"([^\"]+\.json[^\"]*)"',
        r"'ajax'\s*:\s*'([^']+\.json[^']*)'",
        r"&quot;ajax&quot;:&quot;([^&]+\.json[^&]*)&quot;",
    ]
    for pattern in patterns:
        match = re.search(pattern, page_html)
        if match:
            return html.unescape(match.group(1))
    return None


def extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("data", "rows", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list) and all(isinstance(x, dict) for x in value):
                return value
        for value in payload.values():
            if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
                return value
    elif isinstance(payload, list) and all(isinstance(x, dict) for x in payload):
        return payload
    raise TrackerError("Could not find tabular row data in JSON payload")


def normalize_row(raw: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for col in COLUMNS:
        value = raw.get(col, "")
        if value is None:
            value = ""
        text = str(value).strip()
        out[col] = re.sub(r"\s+", " ", text)
    return out


def row_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        row.get("Angler's Name", ""),
        row.get("Kind of Fish (Species)", ""),
        row.get("Date Caught", ""),
    )


def sort_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(
        rows,
        key=lambda r: (
            r.get("Angler's Name", ""),
            r.get("Kind of Fish (Species)", ""),
            r.get("Date Caught", ""),
            r.get("Length of fish", ""),
            r.get("Body of Water Caught", ""),
            r.get("County", ""),
        ),
    )


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [normalize_row(dict(row)) for row in reader]


def write_csv_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in COLUMNS})


def row_to_json(row: dict[str, str]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def diff_rows(
    old_rows: list[dict[str, str]],
    new_rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, Any]]]:
    old_counter = Counter(row_to_json(r) for r in old_rows)
    new_counter = Counter(row_to_json(r) for r in new_rows)

    added: list[dict[str, str]] = []
    removed: list[dict[str, str]] = []

    for key, count in (new_counter - old_counter).items():
        row = json.loads(key)
        for _ in range(count):
            added.append(row)
    for key, count in (old_counter - new_counter).items():
        row = json.loads(key)
        for _ in range(count):
            removed.append(row)

    old_by_key: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    new_by_key: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in old_rows:
        old_by_key.setdefault(row_key(row), []).append(row)
    for row in new_rows:
        new_by_key.setdefault(row_key(row), []).append(row)

    changed: list[dict[str, Any]] = []
    common = set(old_by_key).intersection(new_by_key)
    for key in common:
        old_list = old_by_key[key]
        new_list = new_by_key[key]
        if len(old_list) == 1 and len(new_list) == 1 and old_list[0] != new_list[0]:
            old_row = old_list[0]
            new_row = new_list[0]
            changed.append({"key": key, "old": old_row, "new": new_row})

            old_json = row_to_json(old_row)
            new_json = row_to_json(new_row)
            if old_counter[old_json] > 0 and new_counter[new_json] > 0:
                for i, row in enumerate(removed):
                    if row == old_row:
                        removed.pop(i)
                        break
                for i, row in enumerate(added):
                    if row == new_row:
                        added.pop(i)
                        break

    return sort_rows(added), sort_rows(removed), changed


def markdown_row(row: dict[str, str]) -> str:
    return (
        f'- Name: {row[ANGLER_NAME_KEY]} | Species: {row["Kind of Fish (Species)"]} | '
        f"Length: {row['Length of fish']} | Water: {row['Body of Water Caught']} | "
        f"County: {row['County']} | Date: {row['Date Caught']}"
    )


def write_latest_diff(
    checked_at: str,
    source_url: str,
    old_count: int,
    new_count: int,
    added: list[dict[str, str]],
    removed: list[dict[str, str]],
    changed: list[dict[str, Any]],
) -> None:
    lines: list[str] = []
    lines.append("# Latest Diff Report")
    lines.append("")
    lines.append(f"Checked at (UTC): {checked_at}")
    lines.append(f"Source: {source_url}")
    lines.append(f"Count: {old_count} -> {new_count}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Added rows: {len(added)}")
    lines.append(f"- Removed rows: {len(removed)}")
    lines.append(f"- Changed rows: {len(changed)}")
    lines.append("")

    lines.append("## Added Rows")
    lines.append("")
    if added:
        lines.extend(markdown_row(row) for row in added)
    else:
        lines.append("- None")
    lines.append("")

    lines.append("## Removed Rows")
    lines.append("")
    if removed:
        lines.extend(markdown_row(row) for row in removed)
    else:
        lines.append("- None")
    lines.append("")

    lines.append("## Changed Rows")
    lines.append("")
    if changed:
        for item in changed:
            lines.append("- Key: " + " | ".join(item["key"]))
            lines.append("  - Old: " + markdown_row(item["old"])[2:])
            lines.append("  - New: " + markdown_row(item["new"])[2:])
    else:
        lines.append("- None")
    lines.append("")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_DIFF.write_text("\n".join(lines), encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def build_pages_status(meta: dict[str, Any], history: list[dict[str, Any]]) -> None:
    last_checked = meta.get("lastCheckedAt") or "Never"
    last_changed = meta.get("lastChangedAt") or "No detected changes yet"
    last_count = meta.get("latestRecordCount", "Unknown")
    source_url = meta.get("sourceUrl", SOURCE_PAGE_URL)

    rows: list[str] = []
    for item in reversed(history[-50:]):
        ts = html.escape(str(item.get("changedAt", "")))
        old_count = item.get("oldCount", "")
        new_count = item.get("newCount", "")
        added_count = item.get("addedCount", 0)
        removed_count = item.get("removedCount", 0)
        changed_count = item.get("changedCount", 0)
        adds = item.get("addedRows", [])
        added_preview = "<br>".join(
            html.escape(
                f'{r.get(ANGLER_NAME_KEY, "")} | {r.get("Kind of Fish (Species)", "")} | {r.get("Date Caught", "")}'
            )
            for r in adds[:8]
        )
        if len(adds) > 8:
            added_preview += f"<br>... and {len(adds) - 8} more"
        if not added_preview:
            added_preview = "None"

        rows.append(
            "<tr>"
            f"<td>{ts}</td>"
            f"<td>{old_count} -&gt; {new_count}</td>"
            f"<td>+{added_count} / -{removed_count} / ~{changed_count}</td>"
            f"<td>{added_preview}</td>"
            "</tr>"
        )

    if not rows:
        rows.append("<tr><td colspan=\"4\">No changes recorded yet.</td></tr>")

    html_doc = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>TARP Tracker Status</title>
  <style>
    :root {{
      --bg: #f2f7f3;
      --card: #ffffff;
      --ink: #1f2a25;
      --muted: #4f6a5c;
      --accent: #1f7a55;
      --line: #d7e4db;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: \"Segoe UI\", Tahoma, Geneva, Verdana, sans-serif;
      color: var(--ink);
      background: radial-gradient(circle at top right, #dff1e5 0%, var(--bg) 55%);
    }}
    .wrap {{ max-width: 1100px; margin: 0 auto; padding: 24px; }}
    .hero {{
      background: linear-gradient(120deg, #1f7a55, #2a9d63);
      color: #fff;
      border-radius: 16px;
      padding: 24px;
      box-shadow: 0 12px 30px rgba(31, 122, 85, 0.2);
    }}
    .grid {{
      margin-top: 16px;
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
    }}
    .label {{ color: var(--muted); font-size: 13px; margin-bottom: 6px; }}
    .value {{ font-size: 20px; font-weight: 700; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 10px; text-align: left; vertical-align: top; }}
    th {{ background: #f7fcf8; }}
    a {{ color: var(--accent); }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <section class=\"hero\">
      <h1>Tennessee TARP Tracker</h1>
      <p>Automated monitor for TWRA participant data changes.</p>
    </section>

    <section class=\"grid\">
      <div class=\"card\"><div class=\"label\">Latest Record Count</div><div class=\"value\">{html.escape(str(last_count))}</div></div>
      <div class=\"card\"><div class=\"label\">Last Checked (UTC)</div><div class=\"value\" style=\"font-size:16px\">{html.escape(last_checked)}</div></div>
      <div class=\"card\"><div class=\"label\">Last Change (UTC)</div><div class=\"value\" style=\"font-size:16px\">{html.escape(last_changed)}</div></div>
      <div class=\"card\"><div class=\"label\">Source</div><div class=\"value\" style=\"font-size:16px\"><a href=\"{html.escape(source_url)}\">TWRA TARP Page</a></div></div>
    </section>

    <section class=\"card\" style=\"margin-top: 16px;\">
      <h2>Change History</h2>
      <table>
        <thead>
          <tr>
            <th>Changed At (UTC)</th>
            <th>Count</th>
            <th>Delta</th>
            <th>Added Rows</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </section>
  </div>
</body>
</html>
"""

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "index.html").write_text(html_doc, encoding="utf-8")


def discover_source() -> tuple[str, str, list[dict[str, str]]]:
    ajax_url = urljoin("https://www.tn.gov", DEFAULT_AJAX_PATH)

    try:
        page_html = fetch_text(SOURCE_PAGE_URL)
        ajax_path = extract_ajax_path(page_html) or DEFAULT_AJAX_PATH
        ajax_url = urljoin("https://www.tn.gov", ajax_path)
    except TrackerError:
        pass

    payload = fetch_json(ajax_url)
    rows_raw = extract_rows(payload)
    rows = [normalize_row(r) for r in rows_raw]
    rows = sort_rows(rows)

    if not rows:
        raise TrackerError("Source returned zero rows")

    return SOURCE_PAGE_URL, ajax_url, rows


def main() -> int:
    checked = utc_now()
    checked_at = iso_z(checked)

    source_page, source_data, new_rows = discover_source()
    old_rows = sort_rows(read_csv_rows(CURRENT_CSV))

    old_count = len(old_rows)
    new_count = len(new_rows)

    added, removed, changed = diff_rows(old_rows, new_rows)

    changed_any = bool(added or removed or changed or old_count != new_count)

    write_latest_diff(
        checked_at=checked_at,
        source_url=source_data,
        old_count=old_count,
        new_count=new_count,
        added=added,
        removed=removed,
        changed=changed,
    )

    meta = read_json(CURRENT_META, {})
    if not isinstance(meta, dict):
        meta = {}

    if changed_any:
        write_csv_rows(CURRENT_CSV, new_rows)
        stamp = checked.strftime("%Y%m%dT%H%M%SZ")
        history_snapshot = HISTORY_DIR / f"tarp_{stamp}.csv"
        write_csv_rows(history_snapshot, new_rows)

    history = read_json(CHANGE_HISTORY, [])
    if not isinstance(history, list):
        history = []

    if changed_any:
        history.append(
            {
                "changedAt": checked_at,
                "oldCount": old_count,
                "newCount": new_count,
                "addedCount": len(added),
                "removedCount": len(removed),
                "changedCount": len(changed),
                "addedRows": added,
            }
        )
        write_json(CHANGE_HISTORY, history)

    meta["lastCheckedAt"] = checked_at
    if changed_any:
        meta["lastChangedAt"] = checked_at
    meta["latestRecordCount"] = new_count
    meta["sourceUrl"] = source_page
    meta["dataUrl"] = source_data
    write_json(CURRENT_META, meta)

    build_pages_status(meta, history)

    run_result = {
        "status": "success",
        "changed": changed_any,
        "checkedAt": checked_at,
        "sourcePageUrl": source_page,
        "sourceDataUrl": source_data,
        "oldCount": old_count,
        "newCount": new_count,
        "addedCount": len(added),
        "removedCount": len(removed),
        "changedCount": len(changed),
    }
    write_json(RUN_RESULT, run_result)

    print(json.dumps(run_result))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        checked_at = iso_z(utc_now())
        write_json(
            RUN_RESULT,
            {
                "status": "failure",
                "changed": False,
                "checkedAt": checked_at,
                "error": str(exc),
            },
        )
        LATEST_DIFF.write_text(
            "# Latest Diff Report\n\n"
            f"Check failed at {checked_at}.\n\n"
            f"Error: {exc}\n",
            encoding="utf-8",
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
