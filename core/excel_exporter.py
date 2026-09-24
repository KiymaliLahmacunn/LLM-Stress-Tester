"""
core/excel_exporter.py
----------------------
Converts raw 38-column CSV test data into a formatted XLSX report.
Pure function  no UI or app-state dependencies.
"""

import csv
import os

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

#  Styling constants

_HEADER_FILL = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)

#  Main export function


def finalize_and_format_excel(
    csv_path: str,
    model_name: str,
    task_level: str,
    thinking_str: str,
    streaming_str: str,
    hardware_info: str,
    xlsx_path: str | None = None,
) -> str | None:
    """
    Read 38-column semicolon-delimited CSV  produce formatted .xlsx.
    Returns the xlsx path on success, None on failure.
    Removes the source CSV after successful conversion.
    """
    if not csv_path or not os.path.exists(csv_path):
        return None

    try:
        if xlsx_path is None:
            xlsx_path = csv_path.replace(".csv", ".xlsx")

        wb = openpyxl.Workbook()
        ws = wb.worksheets[0]
        ws.title = "Raw Data"

        # --- Read CSV  Raw Data sheet ---
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f, delimiter=";")
            for row_idx, row in enumerate(reader, 1):
                for col_idx, value in enumerate(row, 1):
                    # We might have large text in questions/responses, make sure it doesn't break Excel cell limits
                    if isinstance(value, str) and len(value) > 30000:
                        value = value[:30000] + "...[TRUNCATED]"

                    cell = ws.cell(row=row_idx, column=col_idx, value=value)

                    if row_idx == 1:
                        cell.fill = _HEADER_FILL
                        cell.font = _HEADER_FONT
                        cell.alignment = _HEADER_ALIGN
                    else:
                        # Top align raw data, but disable text wrapping so row heights stay compact.
                        # Users can click the cell to read the full text in the formula bar.
                        cell.alignment = Alignment(vertical="top", wrap_text=False)

        # Auto-adjust column widths (cap at 60 for readability)
        for col in ws.columns:
            # We skip long text columns when calculating max length to avoid super wide columns
            col_letter = get_column_letter(int(col[0].column or 1))

            # Identify if this is a text-heavy column based on header
            header_val = str(col[0].value).lower()
            is_text_heavy = (
                "text" in header_val
                or "content" in header_val
                or "prompt" in header_val
                or "response" in header_val
            )

            if is_text_heavy:
                ws.column_dimensions[col_letter].width = 60
            else:
                max_len = max(
                    (
                        len(str(cell.value)[:40])
                        for cell in col
                        if cell.value is not None
                    ),
                    default=0,
                )
                ws.column_dimensions[col_letter].width = min(max_len + 3, 40)

        wb.save(xlsx_path)

        # Remove raw CSV
        try:
            os.remove(csv_path)
        except OSError:
            pass

        # Add Summary sheet
        try:
            from core.math_report import generate_report

            report_data = generate_report(xlsx_path)
            if "error" not in report_data:
                wb = openpyxl.load_workbook(xlsx_path)
                ws_summary = wb.create_sheet("Summary")

                # Headers for summary
                headers = [
                    "Metric",
                    "N",
                    "Mean",
                    "Median",
                    "StDev",
                    "Min",
                    "p50",
                    "p90",
                    "p95",
                    "p99",
                    "Max",
                ]
                ws_summary.append(headers)

                for cell in ws_summary[1]:
                    cell.font = _HEADER_FONT
                    cell.fill = _HEADER_FILL

                for metric, stats in report_data.get("metrics", {}).items():
                    row = [
                        metric,
                        stats["n_ok"],
                        stats["mean"],
                        stats["median"],
                        stats["stdev"],
                        stats["min"],
                        stats["p50"],
                        stats["p90"],
                        stats["p95"],
                        stats["p99"],
                        stats["max"],
                    ]
                    ws_summary.append(row)
                wb.save(xlsx_path)
        except Exception as e:
            print(f"[excel_exporter] Failed to add Summary sheet: {e}")

        return xlsx_path

    except Exception as exc:
        print(f"[excel_exporter] Error: {exc}")
        return None
