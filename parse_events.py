import re
from pathlib import Path
from datetime import datetime

import pandas as pd


EVENT_FILE = "Event_IDs.xlsx"
REPORT_FILE = "Events.txt"
OUTPUT_XLSX = "matched_event_details.xlsx"
OUTPUT_PARQUET = "matched_event_details.parquet"
OUTPUT_CSV = "matched_event_details.csv"


def parse_dt(value):
    if pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value
    value = str(value).strip()
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    if "+" in value or value.endswith(" UTC"):
        try:
            return pd.Timestamp(value).to_pydatetime()
        except Exception:
            pass
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    try:
        return pd.Timestamp(value).to_pydatetime()
    except Exception as exc:
        raise ValueError(f"Could not parse timestamp: {value!r}") from exc


def extract_reports(text):
    # Split on the report blocks that start with a timestamp line after the header.
    blocks = re.split(r"(?=\n?REPORT\s+\|\s+Beam\s+\|\s+Beam\s*\n)", text)
    reports = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        time_match = re.search(r"Time\s*:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", block)
        if time_match:
            reports.append({
                "time_str": time_match.group(1),
                "time_dt": datetime.strptime(time_match.group(1), "%Y-%m-%d %H:%M:%S"),
                "text": block,
            })
    return reports


def get_deepest_mps_lines(report_text):
    mps_match = re.search(r"\[MPS\]\s*(.*?)(?:\n\s*═|\Z)", report_text, flags=re.DOTALL)
    if not mps_match:
        return ""
    section = mps_match.group(1)
    lines = []
    for raw in section.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("→") or line.lstrip().startswith("├") or line.lstrip().startswith("└"):
            lines.append(line)
    if not lines:
        return ""

    # Determine deepest indentation among valid MPS lines.
    depths = []
    for line in lines:
        s = line.lstrip()
        indent = len(line) - len(s)
        depths.append(indent)
    max_depth = max(depths)
    deepest = [line for line, d in zip(lines, depths) if d == max_depth]

    return "\n".join(deepest)


def main():
    if not Path(EVENT_FILE).exists():
        raise FileNotFoundError(f"Missing Excel file: {EVENT_FILE}")
    if not Path(REPORT_FILE).exists():
        raise FileNotFoundError(f"Missing report text file: {REPORT_FILE}")

    df = pd.read_excel(EVENT_FILE)

    # Accept common column names.
    rename_map = {}
    for col in df.columns:
        norm = str(col).strip().lower().replace(" ", "_")
        if norm in {"event_id", "eventid", "id"}:
            rename_map[col] = "event_id"
        elif norm in {"ctype", "type"}:
            rename_map[col] = "ctype"
        elif norm in {"event_ts", "event_time", "timestamp", "ts"}:
            rename_map[col] = "event_ts"
    if rename_map:
        df = df.rename(columns=rename_map)

    required = {"event_id", "event_ts"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Excel file is missing required columns: {missing}")

    df["event_id"] = df["event_id"].astype(str).str.strip()
    df["event_ts"] = df["event_ts"].map(parse_dt)
    if "ctype" not in df.columns:
        df["ctype"] = "PM"

    report_blocks = extract_reports(Path(REPORT_FILE).read_text(encoding="utf-8", errors="replace"))

    results = []
    for row in df.itertuples(index=False):
        event_id = getattr(row, "event_id")
        ctype = getattr(row, "ctype", "PM")
        event_dt = getattr(row, "event_ts")

        best = None
        for rep in report_blocks:
            td = abs((rep["time_dt"] - event_dt).total_seconds())
            if best is None or td < best[0]:
                best = (td, rep)

        if best is None or best[0] > 2.0:
            matched_time = None
            last_level = "NO_MATCH"
        else:
            matched_time = best[1]["time_str"]
            last_level = get_deepest_mps_lines(best[1]["text"])

        results.append({
            "event_id": event_id,
            "ctype": ctype,
            "event_ts": event_dt.strftime("%Y-%m-%d %H:%M:%S.%f") if event_dt else None,
            "matched_report_time": matched_time,
            "last_level_lines": last_level,
        })

    out_df = pd.DataFrame(results)
    out_df.to_csv(OUTPUT_CSV, index=False)
    out_df.to_excel(OUTPUT_XLSX, index=False)
    out_df.to_parquet(OUTPUT_PARQUET, index=False)

    print(f"Saved {len(out_df)} rows to {OUTPUT_CSV}, {OUTPUT_XLSX}, and {OUTPUT_PARQUET}")
    print(out_df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
