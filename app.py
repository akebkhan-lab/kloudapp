import io
import re
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="POS + Foodpanda Sales Combiner", layout="wide")

REQUIRED_OUTPUT_COLS = ["Item Name", "POS Qty", "Foodpanda Qty", "Total Qty"]


def clean_text(value: str) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_key(value: str) -> str:
    text = clean_text(value).lower()
    # common standardizations
    replacements = {
        "1 kg": "1kg",
        "1  kg": "1kg",
        "1 liter": "1l",
        "1 litre": "1l",
        "250 ml": "250ml",
        "500 ml": "500ml",
        "full plate": "full",
        "half plate": "half",
        "matka": "matka",
        "teheri": "tehari",
        "shorbot": "sorbot",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    text = text.replace("-", " ")
    text = re.sub(r"[^a-z0-9 ]", "", text)
    tokens = [t for t in text.split() if t]

    # remove very noisy tokens only if needed; keep most words to avoid false matches
    noise = {"normal", "pcs", "pc"}
    tokens = [t for t in tokens if t not in noise]

    return " ".join(tokens)


def parse_pos_file(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()

    if name.endswith(".xls") or name.endswith(".html"):
        tables = pd.read_html(uploaded_file)
        if not tables:
            raise ValueError("Could not read any table from the POS file.")

        raw = tables[0].copy()
        # Flatten weird MultiIndex columns if present
        raw.columns = [" | ".join([str(x) for x in col if str(x) != "nan"]).strip() if isinstance(col, tuple) else str(col) for col in raw.columns]

        # The sample HTML-exported XLS has fixed columns in the item section after the summary rows.
        raw = raw.reset_index(drop=True)
        if raw.shape[1] < 9:
            raise ValueError("POS table shape was not recognized.")

        raw.columns = [
            "Department Name",
            "Group Name",
            "Item Name",
            "Portion Name",
            "Price",
            "Quantity",
            "Net Amount",
            "Gross",
            "Sub Total",
        ]

        # Keep only actual item lines
        item_df = raw[raw["Department Name"].astype(str).str.strip().eq("All")].copy()
    else:
        uploaded_file.seek(0)
        item_df = pd.read_excel(uploaded_file)
        item_df.columns = [clean_text(c) for c in item_df.columns]

    # Standardize possible column names
    rename_map = {}
    for col in item_df.columns:
        low = clean_text(col).lower()
        if low == "item name":
            rename_map[col] = "Item Name"
        elif low == "price":
            rename_map[col] = "Price"
        elif low == "quantity":
            rename_map[col] = "Quantity"
        elif low == "net amount":
            rename_map[col] = "Net Amount"
    item_df = item_df.rename(columns=rename_map)

    required = ["Item Name", "Price", "Quantity", "Net Amount"]
    missing = [c for c in required if c not in item_df.columns]
    if missing:
        raise ValueError(f"POS file is missing required columns: {missing}")

    item_df = item_df[required].copy()
    item_df["Item Name"] = item_df["Item Name"].map(clean_text)
    item_df = item_df[item_df["Item Name"] != ""]
    item_df = item_df[~item_df["Item Name"].str.lower().str.contains("total|powered by", na=False)]

    for c in ["Price", "Quantity", "Net Amount"]:
        item_df[c] = pd.to_numeric(item_df[c], errors="coerce")
    item_df = item_df.dropna(subset=["Quantity"])

    return item_df.reset_index(drop=True)



def parse_foodpanda_file(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()
    uploaded_file.seek(0)

    if name.endswith(".csv"):
        df = pd.read_csv(uploaded_file)
    else:
        df = pd.read_excel(uploaded_file)

    df.columns = [clean_text(c) for c in df.columns]

    # Likely column names in foodpanda exports
    dish_col = None
    qty_col = None
    sales_col = None

    for col in df.columns:
        low = col.lower()
        if low in {"dish", "dish name", "item name", "product"}:
            dish_col = col
        elif low in {"total", "quantity", "qty", "items sold"}:
            qty_col = col
        elif low in {"sales", "net sales", "amount"}:
            sales_col = col

    if not dish_col or not qty_col:
        raise ValueError(
            "Foodpanda file was not recognized. Need at least a dish column and a quantity/total column."
        )

    out = df[[dish_col, qty_col] + ([sales_col] if sales_col else [])].copy()
    out = out.rename(columns={dish_col: "Item Name", qty_col: "Quantity"})
    if sales_col:
        out = out.rename(columns={sales_col: "Sales"})
    else:
        out["Sales"] = pd.NA

    out["Item Name"] = out["Item Name"].map(clean_text)
    out["Quantity"] = pd.to_numeric(out["Quantity"], errors="coerce")
    if "Sales" in out.columns:
        out["Sales"] = (
            out["Sales"].astype(str).str.replace(",", "", regex=False)
        )
        out["Sales"] = pd.to_numeric(out["Sales"], errors="coerce")

    out = out.dropna(subset=["Item Name", "Quantity"])
    out = out[out["Item Name"] != ""]
    return out.reset_index(drop=True)



def build_mapping_df(pos_df: pd.DataFrame, fp_df: pd.DataFrame) -> pd.DataFrame:
    pos_names = sorted(pos_df["Item Name"].dropna().astype(str).unique().tolist())
    fp_names = sorted(fp_df["Item Name"].dropna().astype(str).unique().tolist())

    fp_key_map = {}
    for name in fp_names:
        key = normalize_key(name)
        fp_key_map.setdefault(key, []).append(name)

    rows = []
    for pos_name in pos_names:
        key = normalize_key(pos_name)
        candidates = fp_key_map.get(key, [])
        suggested = candidates[0] if candidates else ""
        rows.append(
            {
                "POS Item Name": pos_name,
                "Suggested Foodpanda Match": suggested,
                "Final Standard Name": pos_name,
            }
        )

    return pd.DataFrame(rows)



def apply_mapping(
    pos_df: pd.DataFrame,
    fp_df: pd.DataFrame,
    mapping_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    mapping_df = mapping_df.copy()
    mapping_df.columns = [clean_text(c) for c in mapping_df.columns]

    needed = {"POS Item Name", "Suggested Foodpanda Match", "Final Standard Name"}
    if not needed.issubset(set(mapping_df.columns)):
        raise ValueError(
            "Mapping file must contain columns: POS Item Name, Suggested Foodpanda Match, Final Standard Name"
        )

    mapping_df = mapping_df.fillna("")
    pos_to_final = {}
    fp_to_final = {}

    for _, row in mapping_df.iterrows():
        pos_item = clean_text(row["POS Item Name"])
        fp_item = clean_text(row["Suggested Foodpanda Match"])
        final_name = clean_text(row["Final Standard Name"]) or pos_item or fp_item

        if pos_item:
            pos_to_final[pos_item] = final_name
        if fp_item:
            fp_to_final[fp_item] = final_name

    # fallback by normalized keys when a direct manual mapping is absent
    pos_key_to_final = {
        normalize_key(k): v for k, v in pos_to_final.items() if clean_text(k)
    }

    pos_clean = pos_df.copy()
    pos_clean["Standard Item Name"] = pos_clean["Item Name"].map(
        lambda x: pos_to_final.get(clean_text(x), pos_key_to_final.get(normalize_key(x), clean_text(x)))
    )

    fp_clean = fp_df.copy()
    fp_clean["Standard Item Name"] = fp_clean["Item Name"].map(
        lambda x: fp_to_final.get(clean_text(x), pos_key_to_final.get(normalize_key(x), clean_text(x)))
    )

    # unmatched FP items: anything not mapped to one of the final names from mapping sheet and not key-matched
    mapped_final_names = set(mapping_df["Final Standard Name"].map(clean_text))
    fp_unmatched = fp_clean[~fp_clean["Standard Item Name"].isin(mapped_final_names)].copy()

    return pos_clean, fp_clean, fp_unmatched



def build_final_summary(pos_clean: pd.DataFrame, fp_clean: pd.DataFrame) -> pd.DataFrame:
    pos_sum = (
        pos_clean.groupby("Standard Item Name", as_index=False)["Quantity"]
        .sum()
        .rename(columns={"Standard Item Name": "Item Name", "Quantity": "POS Qty"})
    )
    fp_sum = (
        fp_clean.groupby("Standard Item Name", as_index=False)["Quantity"]
        .sum()
        .rename(columns={"Standard Item Name": "Item Name", "Quantity": "Foodpanda Qty"})
    )

    final = pos_sum.merge(fp_sum, on="Item Name", how="outer")
    final[["POS Qty", "Foodpanda Qty"]] = final[["POS Qty", "Foodpanda Qty"]].fillna(0)
    final["Total Qty"] = final["POS Qty"] + final["Foodpanda Qty"]
    final = final.sort_values(["Total Qty", "Item Name"], ascending=[False, True]).reset_index(drop=True)

    for col in ["POS Qty", "Foodpanda Qty", "Total Qty"]:
        final[col] = final[col].astype(int)

    return final[REQUIRED_OUTPUT_COLS]



def to_excel_bytes(final_df, pos_clean, fp_clean, mapping_df, unmatched_fp) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        final_df.to_excel(writer, index=False, sheet_name="Final Summary")
        pos_clean.to_excel(writer, index=False, sheet_name="POS Clean")
        fp_clean.to_excel(writer, index=False, sheet_name="Foodpanda Clean")
        mapping_df.to_excel(writer, index=False, sheet_name="Mapping")
        unmatched_fp.to_excel(writer, index=False, sheet_name="Unmatched FP Items")

        for sheet_name, df in {
            "Final Summary": final_df,
            "POS Clean": pos_clean,
            "Foodpanda Clean": fp_clean,
            "Mapping": mapping_df,
            "Unmatched FP Items": unmatched_fp,
        }.items():
            ws = writer.book[sheet_name]
            for i, col in enumerate(df.columns, start=1):
                max_len = max(len(str(col)), *(len(str(v)) for v in df[col].head(200).tolist())) if len(df) else len(str(col))
                ws.column_dimensions[chr(64 + i) if i <= 26 else 'A'].width = min(max(max_len + 2, 12), 35)

    output.seek(0)
    return output.getvalue()



def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


st.title("POS + Foodpanda Item Quantity Combiner")
st.caption("Upload both files, review the suggested mapping, and download a final combined quantity report.")

with st.sidebar:
    st.header("Files")
    pos_file = st.file_uploader("Upload POS sales report", type=["xls", "xlsx", "html"])
    fp_file = st.file_uploader("Upload Foodpanda report", type=["csv", "xlsx", "xls"])
    mapping_upload = st.file_uploader(
        "Optional: upload saved mapping CSV/XLSX",
        type=["csv", "xlsx", "xls"],
        help="Use this if you already finalized your item mapping and want to reuse it.",
    )

if not pos_file or not fp_file:
    st.info("Upload both the POS file and the Foodpanda file to begin.")
    st.stop()

try:
    pos_df = parse_pos_file(pos_file)
    fp_df = parse_foodpanda_file(fp_file)
except Exception as exc:
    st.error(f"File parsing error: {exc}")
    st.stop()

st.success("Files parsed successfully.")

col1, col2, col3 = st.columns(3)
col1.metric("POS Items", len(pos_df))
col2.metric("Foodpanda Items", len(fp_df))
col3.metric("Combined Raw Qty", int(pos_df["Quantity"].sum() + fp_df["Quantity"].sum()))

with st.expander("Preview cleaned source data", expanded=False):
    a, b = st.columns(2)
    with a:
        st.subheader("POS cleaned")
        st.dataframe(pos_df, use_container_width=True)
    with b:
        st.subheader("Foodpanda cleaned")
        st.dataframe(fp_df, use_container_width=True)

# Build or load mapping
try:
    if mapping_upload is not None:
        if mapping_upload.name.lower().endswith(".csv"):
            mapping_df = pd.read_csv(mapping_upload)
        else:
            mapping_df = pd.read_excel(mapping_upload)
        mapping_df.columns = [clean_text(c) for c in mapping_df.columns]
    else:
        mapping_df = build_mapping_df(pos_df, fp_df)
except Exception as exc:
    st.error(f"Could not create/load mapping table: {exc}")
    st.stop()

st.subheader("Item Mapping")
st.write(
    "Confirm each POS item’s matching Foodpanda item and set the final standard item name. "
    "You can edit the table below directly."
)

edited_mapping = st.data_editor(
    mapping_df,
    use_container_width=True,
    num_rows="dynamic",
    key="mapping_editor",
)

try:
    pos_clean, fp_clean, unmatched_fp = apply_mapping(pos_df, fp_df, edited_mapping)
    final_df = build_final_summary(pos_clean, fp_clean)
except Exception as exc:
    st.error(f"Mapping/merge error: {exc}")
    st.stop()

st.subheader("Final Combined Quantity")
st.dataframe(final_df, use_container_width=True)

sum1, sum2, sum3 = st.columns(3)
sum1.metric("POS Qty Total", int(final_df["POS Qty"].sum()))
sum2.metric("Foodpanda Qty Total", int(final_df["Foodpanda Qty"].sum()))
sum3.metric("Grand Total Qty", int(final_df["Total Qty"].sum()))

if len(unmatched_fp) > 0:
    with st.expander(f"Unmatched Foodpanda items ({len(unmatched_fp)})", expanded=True):
        st.warning(
            "These Foodpanda items did not confidently map to a final standard item name from your mapping sheet. "
            "Review them before finalizing."
        )
        st.dataframe(unmatched_fp[["Item Name", "Quantity", "Standard Item Name"]], use_container_width=True)

excel_bytes = to_excel_bytes(final_df, pos_clean, fp_clean, edited_mapping, unmatched_fp)
mapping_csv_bytes = to_csv_bytes(edited_mapping)

c1, c2 = st.columns(2)
with c1:
    st.download_button(
        "Download final workbook (.xlsx)",
        data=excel_bytes,
        file_name="combined_sales_summary.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
with c2:
    st.download_button(
        "Download mapping template (.csv)",
        data=mapping_csv_bytes,
        file_name="item_mapping.csv",
        mime="text/csv",
        use_container_width=True,
    )

st.markdown("---")
st.markdown(
    "**How it works:** POS item quantities and Foodpanda item quantities are cleaned separately, "
    "matched through the mapping table, then summed into one final item-level quantity report."
)
