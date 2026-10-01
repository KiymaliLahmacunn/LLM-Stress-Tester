import json
import os
import time
from typing import Dict, List

import pandas as pd

DATASETS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "datasets"
)
os.makedirs(DATASETS_DIR, exist_ok=True)


def _safe_filename(filename: str) -> str | None:
    basename = os.path.basename(filename)
    if not basename or basename != filename or ".." in filename:
        return None
    return basename


def list_datasets() -> List[Dict]:
    datasets = []
    if not os.path.exists(DATASETS_DIR):
        return datasets

    for f in os.listdir(DATASETS_DIR):
        if f.endswith(".csv"):
            base = os.path.splitext(f)[0]
            json_path = os.path.join(DATASETS_DIR, f"{base}.json")
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as file:
                        datasets.append(json.load(file))
                except Exception:
                    datasets.append(
                        {
                            "name": f,
                            "original_name": f,
                            "row_count": "-",
                            "created_at": 0,
                            "updated_at": 0,
                        }
                    )
            else:
                datasets.append(
                    {
                        "name": f,
                        "original_name": f,
                        "row_count": "-",
                        "created_at": os.path.getmtime(os.path.join(DATASETS_DIR, f))
                        * 1000,
                        "updated_at": 0,
                    }
                )

    # Sort by created_at descending
    datasets.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return datasets


def process_and_save_upload(file_content: bytes, filename: str) -> Dict:
    safe_name = _safe_filename(filename)
    if not safe_name:
        raise ValueError("Invalid filename")

    ext = os.path.splitext(safe_name)[1].lower()

    # Read using pandas to normalize
    try:
        if ext == ".csv":
            import io

            # Try sniffing the separator or fallback to common ones
            try:
                df = pd.read_csv(io.BytesIO(file_content), sep=None, engine="python")
            except Exception:
                df = pd.read_csv(io.BytesIO(file_content), sep=";")
        elif ext == ".xlsx":
            import io

            df = pd.read_excel(io.BytesIO(file_content))
        else:
            raise ValueError("Unsupported file extension. Please upload .csv or .xlsx")
    except Exception as e:
        raise ValueError(f"Could not parse file: {str(e)}")

    # Check for required columns
    columns = [str(c).upper().strip() for c in df.columns]
    df.columns = columns

    if "ID" not in columns:
        raise ValueError("Dataset must contain an 'ID' column")

    text_col = None
    for col in ["TEXT", "QUESTION", "SORU"]:
        if col in columns:
            text_col = col
            break

    if not text_col:
        raise ValueError("Dataset must contain a 'TEXT' or 'QUESTION' column")

    # Rename the text column to 'TEXT' for internal consistency
    df.rename(columns={text_col: "TEXT"}, inplace=True)

    out_df = df[["ID", "TEXT"]]

    # We always save as CSV so test_manager can read it
    base_name = os.path.splitext(safe_name)[0]
    final_csv_name = f"{base_name}.csv"
    final_csv_path = os.path.join(DATASETS_DIR, final_csv_name)

    # Avoid overwriting existing datasets by adding a numeric suffix
    counter = 2
    while os.path.exists(final_csv_path):
        final_csv_name = f"{base_name}_{counter}.csv"
        final_csv_path = os.path.join(DATASETS_DIR, final_csv_name)
        counter += 1
    # Update base_name to match the final filename (for metadata JSON)
    base_name = os.path.splitext(final_csv_name)[0]

    out_df.to_csv(
        final_csv_path,
        sep=";",
        index=False,
        header=["ID", "TEXT"],
        encoding="utf-8-sig",
    )

    row_count = len(out_df)
    now = time.time() * 1000

    metadata = {
        "name": final_csv_name,
        "original_name": safe_name,
        "row_count": row_count,
        "created_at": now,
        "updated_at": now,
    }

    json_path = os.path.join(DATASETS_DIR, f"{base_name}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    return metadata


def delete_dataset(filename: str):
    safe_name = _safe_filename(filename)
    if not safe_name:
        raise ValueError("Invalid filename")

    base_name = os.path.splitext(safe_name)[0]
    csv_path = os.path.join(DATASETS_DIR, f"{base_name}.csv")
    json_path = os.path.join(DATASETS_DIR, f"{base_name}.json")

    if os.path.exists(csv_path):
        os.remove(csv_path)
    if os.path.exists(json_path):
        os.remove(json_path)

def get_dataset_preview(filename: str) -> list:
    safe_name = _safe_filename(filename)
    if not safe_name:
        raise ValueError("Invalid filename")
    
    csv_path = os.path.join(DATASETS_DIR, safe_name)
    if not os.path.exists(csv_path):
        raise ValueError("Dataset not found")
        
    df = pd.read_csv(csv_path, sep=";", nrows=5)
    return df.to_dict(orient="records")
