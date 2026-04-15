import streamlit as st
import pandas as pd
from io import BytesIO

st.set_page_config(page_title="Sales Combiner", layout="wide")
st.title("POS + Foodpanda Sales Combiner")

# -----------------------
# FILE UPLOAD
# -----------------------
pos_file = st.file_uploader("Upload POS File (.xls)", type=["xls", "xlsx", "html"])
fp_file = st.file_uploader("Upload Foodpanda File (.csv / .xlsx)", type=["csv", "xlsx"])

# -----------------------
# POS CLEANING FUNCTION
# -----------------------
def clean_pos(file):
    try:
        tables = pd.read_html(file)
        df = None

        for t in tables:
            cols = [str(c).strip() for c in t.columns]
            if "Item Name" in cols and "Quantity" in cols:
                df = t.copy()
                df.columns = cols
                break

        if df is None:
            raise ValueError("POS table not found")

    except:
        file.seek(0)
        df = pd.read_excel(file)
        df.columns = [str(c).strip() for c in df.columns]

    df = df[["Item Name", "Quantity"]]
    df = df.dropna(subset=["Item Name"])

    df["Item Name"] = df["Item Name"].astype(str).str.strip()
    df = df[~df["Item Name"].str.lower().str.contains("total", na=False)]

    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce")
    df = df.dropna(subset=["Quantity"])

    return df.reset_index(drop=True)

# -----------------------
# FOODPANDA CLEANING FUNCTION
# -----------------------
def clean_fp(file):
    if file.name.endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file)

    df.columns = [str(c).strip() for c in df.columns]

    # Try to detect columns automatically
    item_col = None
    qty_col = None

    for col in df.columns:
        if "dish" in col.lower() or "item" in col.lower():
            item_col = col
        if "qty" in col.lower() or "quantity" in col.lower():
            qty_col = col

    if item_col is None or qty_col is None:
        raise ValueError("Foodpanda columns not detected")

    df = df[[item_col, qty_col]]
    df.columns = ["Item Name", "Quantity"]

    df = df.dropna(subset=["Item Name"])
    df["Item Name"] = df["Item Name"].astype(str).str.strip()

    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce")
    df = df.dropna(subset=["Quantity"])

    return df.reset_index(drop=True)

# -----------------------
# MAIN LOGIC
# -----------------------
if pos_file and fp_file:

    try:
        pos_df = clean_pos(pos_file)
        fp_df = clean_fp(fp_file)

        st.subheader("Step 1: Mapping Table")

        # Unique items
        pos_items = pd.DataFrame({"POS Item": pos_df["Item Name"].unique()})
        fp_items = pd.DataFrame({"Foodpanda Item": fp_df["Item Name"].unique()})

        mapping = pd.concat([pos_items, fp_items], axis=1)

        mapping["Standard Name"] = mapping["POS Item"].combine_first(mapping["Foodpanda Item"])

        mapping = st.data_editor(mapping, num_rows="dynamic")

        # Create mapping dictionary
        map_dict = {}

        for _, row in mapping.iterrows():
            if pd.notna(row["POS Item"]):
                map_dict[row["POS Item"]] = row["Standard Name"]
            if pd.notna(row["Foodpanda Item"]):
                map_dict[row["Foodpanda Item"]] = row["Standard Name"]

        # Apply mapping
        pos_df["Item Name"] = pos_df["Item Name"].map(map_dict).fillna(pos_df["Item Name"])
        fp_df["Item Name"] = fp_df["Item Name"].map(map_dict).fillna(fp_df["Item Name"])

        # -----------------------
        # SUMMARIES
        # -----------------------
        pos_summary = (
            pos_df.groupby("Item Name", as_index=False)["Quantity"]
            .sum()
            .rename(columns={"Quantity": "POS Qty"})
        )

        fp_summary = (
            fp_df.groupby("Item Name", as_index=False)["Quantity"]
            .sum()
            .rename(columns={"Quantity": "Foodpanda Qty"})
        )

        # -----------------------
        # ✅ FIXED MERGE (OUTER)
        # -----------------------
        final = pd.merge(
            pos_summary,
            fp_summary,
            on="Item Name",
            how="outer"
        )

        # Fill missing values
        final["POS Qty"] = pd.to_numeric(final["POS Qty"], errors="coerce").fillna(0)
        final["Foodpanda Qty"] = pd.to_numeric(final["Foodpanda Qty"], errors="coerce").fillna(0)

        final["POS Qty"] = final["POS Qty"].astype(int)
        final["Foodpanda Qty"] = final["Foodpanda Qty"].astype(int)

        # Total
        final["Total Qty"] = final["POS Qty"] + final["Foodpanda Qty"]

        final = final[["Item Name", "POS Qty", "Foodpanda Qty", "Total Qty"]]
        final = final.sort_values(by="Item Name").reset_index(drop=True)

        # -----------------------
        # DISPLAY
        # -----------------------
        st.subheader("Final Combined Report")
        st.dataframe(final, use_container_width=True)

        # -----------------------
        # DOWNLOAD
        # -----------------------
        def to_excel(df):
            output = BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Combined Sales")
            output.seek(0)
            return output

        st.download_button(
            "Download Excel",
            data=to_excel(final),
            file_name="combined_sales.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    except Exception as e:
        st.error(f"Error: {e}")
