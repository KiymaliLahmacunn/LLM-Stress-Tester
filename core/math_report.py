import numpy as np
import pandas as pd


def generate_report(xlsx_path: str) -> dict:
    """
    Reads the raw Excel file, filters for successful requests,
    and calculates statistical metrics (mean, median, stdev, min, percentiles, max)
    for key numerical columns.
    """
    try:
        # Load the Raw Data sheet
        df = pd.read_excel(xlsx_path, sheet_name="Raw Data")
    except Exception as e:
        return {"error": f"Could not read Excel file: {str(e)}"}

    # Filter only successful requests
    if "Status" in df.columns:
        df_ok = df[df["Status"] == "Success"]
    else:
        df_ok = df

    n_ok = len(df_ok)
    if n_ok == 0:
        return {
            "error": "No successful requests found in the dataset to calculate math."
        }

    # The columns we want to calculate stats for (if they exist)
    metrics = {
        "TTFT (ms)": "Time to First Token (ms)",
        "Generation Time (ms)": "Generation Time (ms)",
        "Total Duration (ms)": "Total Duration (ms)",
        "TPS (tok/s)": "Tokens Per Second (TPS)",
        "Prompt Tokens": "Prompt Tokens",
        "Completion Tokens": "Completion Tokens",
    }

    report_data = {}
    for col, display_name in metrics.items():
        if col in df_ok.columns:
            # Convert to numeric, handling comma decimals and coercing errors to NaN
            s = pd.to_numeric(
                df_ok[col].astype(str).str.replace(",", "."), errors="coerce"
            ).dropna()

            if len(s) > 0:
                report_data[display_name] = {
                    "n_ok": len(s),
                    "mean": float(round(s.mean(), 2)),
                    "median": float(round(s.median(), 2)),
                    "stdev": float(round(s.std(), 2)) if len(s) > 1 else 0.0,
                    "min": float(round(s.min(), 2)),
                    "p50": float(round(np.percentile(s, 50), 2)),
                    "p75": float(round(np.percentile(s, 75), 2)),
                    "p90": float(round(np.percentile(s, 90), 2)),
                    "p95": float(round(np.percentile(s, 95), 2)),
                    "p99": float(round(np.percentile(s, 99), 2)),
                    "max": float(round(s.max(), 2)),
                }

    test_duration_sec = 0.0
    try:
        if (
            "Sent At (epoch ms)" in df.columns
            and "Finished At (epoch ms)" in df.columns
        ):
            min_start = pd.to_numeric(df["Sent At (epoch ms)"], errors="coerce").min()
            max_end = pd.to_numeric(df["Finished At (epoch ms)"], errors="coerce").max()
            if pd.notna(min_start) and pd.notna(max_end) and max_end > min_start:
                test_duration_sec = round((max_end - min_start) / 1000.0, 2)
    except Exception:
        pass

    return {
        "total_requests": len(df),
        "successful_requests": n_ok,
        "failed_requests": len(df) - n_ok,
        "test_duration_sec": test_duration_sec,
        "metrics": report_data,
    }
