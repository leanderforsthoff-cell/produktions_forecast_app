# GEMINI.md

Production forecasting & Material Requirements Planning (MRP) Streamlit app backed by Google BigQuery.

## Quick Start
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```
**Auth:** Requires `service-account-key.json` and `.env` containing `GOOGLE_APPLICATION_CREDENTIALS="service-account-key.json"`.

---

## Architecture & Data Flow

```text
BigQuery ──► src/data_integration.py ──► app.py (Streamlit UI)
                 │                           ├── 1. Forecasting (src/forecasting.py)
                 │                           ├── 2. Production Plan (src/inventory_math.py)
                 ▼                           └── 3. MRP & BOM Explosion (src/mrp_math.py)
pollymain.playground.forecast_snapshots ◄────┘   (Idempotent Save)
```

| Module | Core Responsibility |
|---|---|
| `app.py` | UI layout, sidebar filters, data editor, MRP tabs, metric cards, snapshot trigger. |
| `src/data_integration.py` | BigQuery queries (`@st.cache_data`), client (`@st.cache_resource`), snapshot persistence. |
| `src/forecasting.py` | Statistical baseline sales forecast combining trailing velocity and seasonality. |
| `src/inventory_math.py` | Vectorized net requirement calculation with MOQ, safety stock, and lead time snapping. |
| `src/mrp_math.py` | BOM expansion, chronological stock depletion via cumulative sum, supplier order grouping. |
| `src/utils.py` | Target month series generation and deadline calculation (snapped to 15th of month). |

---

## Domain Logic & Formulas

### 1. Sales Forecast (`src/forecasting.py`)
- Reindexes historical monthly sales (`pollymain.weclapp.shipmentitems`) to continuous monthly intervals.
- If $T - 1\text{ yr}$ sales exist: $\text{Forecast} = \text{int}(0.6 \times \text{Sales}_{T-1\text{yr}} + 0.4 \times \text{Avg}_{3\text{m}})$.
- Else: $\text{Forecast} = \text{Avg}_{3\text{m}}$. (Fills missing historical months with 0).

### 2. Production Planning (`src/inventory_math.py`)
- Dynamic horizon (1–6 months, default 3).
- Default constraints: `MOQ=2000` (min/fallback 1000), `Mindestbestand=100`, `Vorlaufzeit_Wochen=4`.
- Per month $t$:
  - $\text{Req}_t = \max(0, \text{Demand}_t + \text{SafetyStock} - I_{t-1})$
  - $\text{Prod}_t = \lceil \text{Req}_t / \text{MOQ} \rceil \times \text{MOQ}$
  - $I_t = I_{t-1} + \text{Prod}_t - \text{Demand}_t$
- **Deadlines:** Target date minus lead time, snapped to 15th of month (`day >= 15` $\to$ 15th of current; `day < 15` $\to$ 15th of prev month). Past dates flagged with 🔴 and shifted to today.

### 3. MRP & BOM Explosion (`src/mrp_math.py`)
- **Key Matching:** Finished product numbers matched to BOM/stock via base prefix (`art.split('-')[0]`).
- $\text{Brutto\_Bedarf} = \text{Produktion\_Menge} \times \text{quantity}$.
- **Stock Depletion:** Chronological allocation of raw material stock (`warehousestock`) across dates using:
  $$\text{stock\_before} = \max(0, \text{initial\_stock} - \text{cumsum}(\text{prior\_demands}))$$
  $$\text{net\_bedarf} = \text{Brutto\_Bedarf} - \min(\text{Brutto\_Bedarf}, \text{stock\_before})$$
- Orders grouped by `(Lieferant, Spätestes_Bestelldatum, Material_Nr, Einheit, Einzelpreis)`.

---

## BigQuery Schema Mapping

| Table | Purpose | Key Fields |
|---|---|---|
| `pollymain.weclapp.article_totalStockQuantity` | FG stock | `article_number`, `quantity` |
| `pollymain.weclapp.article` | FG metadata | `articleNumber`, `name`, `active` |
| `pollymain.weclapp.shipmentitems` | Sales history | `articleNumber`, `createdDate` (Unix ms), `quantity` |
| `pollymain.mart.COGS` | BOM & Suppliers | `productArticleNumber`, `materialArticleNumber`, `quantity`, `company` (`Lieferant`), `procurementLeadDays`, `articleunitprice`, `is_in_article_mapping` |
| `pollymain.mart.warehousestock` | RM stock | `articleNumber` (split on `-`[0]), `quantity` |
| `pollymain.playground.forecast_snapshots` | Saved runs | `Planungsmonat`, `Speicherzeitpunkt`, `Artikelnummer`, `Zielmonat`, `System_Forecast`, `Manuell_Forecast`, `Produktionsbedarf`, `Spaetestes_Bestelldatum` |

*Write Strategy:* Idempotent; deletes `Planungsmonat = '{YYYY-MM-01}'` before appending current run snapshot in long format.

---

## Engineering Guidelines

- **Vectorized Math:** Strictly avoid `iterrows()`. Use Pandas/NumPy vectorization (`cumsum()`, `clip()`, `np.where()`, `map()`).
- **Caching:** Wrap BQ queries in `@st.cache_data(ttl=...)` with `show_spinner`; wrap client init in `@st.cache_resource`.
- **Query Security:** Use `bigquery.ArrayQueryParameter` for all array lookups (`IN UNNEST(@param)`).
- **Naming Conventions:** German identifiers throughout dataframes and UI (`Artikelnummer`, `Aktueller_Bestand`, `Manuell_M{i}`, `Produktion_M{i}`, `Spätestes_Bestelldatum`).
- **Testing:** Unit test math logic using `pytest` against mocked DataFrames (`src/inventory_math.py`, `src/mrp_math.py`, `src/forecasting.py`, `src/utils.py`).
