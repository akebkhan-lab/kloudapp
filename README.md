# POS + Foodpanda Item Quantity Combiner

This Streamlit app combines two sales sources:

- POS sales report (your HTML-style `.xls` export)
- Foodpanda item report (`.csv` or Excel)

It produces one final report with:

- Item Name
- POS Qty
- Foodpanda Qty
- Total Qty

## Features

- Parses the uploaded POS `.xls` report
- Parses Foodpanda CSV/Excel report
- Keeps item-level quantity data
- Suggests matches between POS items and Foodpanda items
- Lets you edit mapping inside the app
- Exports a final Excel workbook with multiple sheets

## Files included

- `app.py` - Streamlit app
- `requirements.txt` - Python dependencies

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Workflow

1. Upload POS file
2. Upload Foodpanda file
3. Review/edit mapping
4. Download final workbook

## Output workbook sheets

- `Final Summary`
- `POS Clean`
- `Foodpanda Clean`
- `Mapping`
- `Unmatched FP Items`

