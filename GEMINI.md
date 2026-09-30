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
                 │                           └── src/planning_service.py (Orchestrator)
                 │                                  ├── 1. Matrix & Forecasting (src/forecasting.py)
                 │                                  ├── 2. Production Plan (src/inventory_math.py)
                 │                                  ├── 3. BOM & FIFO Tracking (src/bom.py, src/supply_tracker.py)
                 ▼                                  └── 4. MRP & Cash Needs (src/mrp_math.py)
pollymain.playground.forecast_snapshots ◄────┘   (Idempotent Save)
```

| Module | Core Responsibility |
|---|---|
| `app.py` | UI layout, sidebar filters, data editor, MRP tabs, metric cards, snapshot trigger. |
| `src/planning_service.py` | Planning orchestration: matrix preparation (`build_initial_planning_matrix`), full planning cycle, warnings & UI formatting. |
| `src/data_integration.py` | BigQuery queries (`@st.cache_data`), client (`@st.cache_resource`), snapshot persistence. |
| `src/forecasting.py` | Statistical baseline sales forecast combining trailing velocity and seasonality. |
| `src/inventory_math.py` | Net requirement calculation with MOQ, safety stock, flexible lead times, and material-feasibility validation. |
| `src/bom.py` | BOM index building (`prepare_cleaned_cogs`, `build_bom_index`) and open production material resolution. |
| `src/supply_tracker.py` | Stateful warehouse stock and PO tracking with FIFO allocation and bottleneck analysis. |
| `src/mrp_math.py` | BOM explosion, chronological supply/demand netting, supplier order grouping, and cash needs calculation. |
| `src/utils.py` | Target month series generation, day-exact deadline calculation, and date/currency/quantity formatting. |

---

## Domain Logic & Formulas

### 1. Sales Forecast (`src/forecasting.py`)
- Reindexes historical monthly sales (`pollymain.weclapp.shipmentitems`) to continuous monthly intervals.
- If $T - 1\text{ yr}$ sales exist: $\text{Forecast} = \text{int}(0.6 \times \text{Sales}_{T-1\text{yr}} + 0.4 \times \text{Avg}_{3\text{m}})$.
- Else: $\text{Forecast} = \text{Avg}_{3\text{m}}$. (Fills missing historical months with 0).

### 2. Production Planning (`src/inventory_math.py`)
- Dynamic horizon (1–6 months, default 3), with at most one production run per product per target month.
- Default constraints: `MOQ=2000` (min/fallback 1000), `Mindestbestand=100`, `Vorlaufzeit_Wochen=4`.
- Per month $t$ (considering already scheduled productions $S_t$ from open production orders):
  - $\text{Req}_t = \max(0, \text{Demand}_t + \text{SafetyStock} - (I_{t-1} + S_t))$
  - $\text{Prod}_t = \lceil \text{Req}_t / \text{MOQ} \rceil \times \text{MOQ}$
  - $I_t = I_{t-1} + S_t + \text{Prod}_t - \text{Demand}_t$
- **Open Production Orders ($S_t$):** Only orders with `targetEndDate >= today` are considered upcoming additions (older orders are already completed and physically in initial stock). Incomings are booked one day after completion: $\text{Buchungsdatum} = \text{targetEndDate} + 1\text{ day}$.
- **Deadlines:** Target completion is the 1st of the target month ($M_t$). Production start deadline is flexible and day-exact: $M_t - L_{\text{FG}} \times 7\text{ days}$ (no snapping to 15th).
- **Stringent Feasibility & Lost Sales:** If either the FG production start deadline or any required BOM component's procurement deadline is in the past ($< \text{today}$) and cannot be satisfied from stock/open POs:
  - The production is canceled ($\text{Prod}_t = 0, \text{Bestelldatum}_t = \text{None}$).
  - A customer shortage ($\text{Fehlbestand}_t = \max(0, D_t - (I_{t-1} + S_t))$) is recorded with the exact bottleneck reason.
  - Zero material purchase orders are generated in MRP.
  - An out-of-stock warning is displayed in the UI.

### 3. MRP & BOM Explosion (`src/mrp_math.py`)
- **Key Matching:** Finished product numbers matched to BOM/stock via base prefix (`art.split('-')[0]`).
- $\text{Brutto\_Bedarf} = \text{Produktion\_Menge} \times \text{quantity}$.
- **Chronological Supply & Demand Netting:**
  - Supplies: Initial warehouse stock (at $t_0$) + open Purchase Orders (at $\text{plannedDeliveryDate} + 1\text{ day}$). Only POs with $\text{plannedDeliveryDate} \ge \text{today}$ are considered.
  - Demands: Open Production Orders material consumption (at `targetEndDate`) + new planned productions (at `Bedarfs_Datum`).
  - Supplies arriving on or before demand date are consumed chronologically (FIFO).
  - Feasible demands trigger net orders with day-exact deadlines: $T_{\text{Bedarf}} - L_{m, \text{days}}\text{ days}$.
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
| `pollymain.weclapp.purchaseorder` | Open POs | `id`, `status`, `orderDate`, `plannedDeliveryDate` (Unix ms), `purchaseOrderItems` (JSON string or `_n_` flat columns) |
| `pollymain.weclapp.productionorder` | Open Prod Orders | `id`, `status`, `articleNumber`, `targetQuantity`, `targetEndDate` (Unix ms), `createdDate`, `productionOrderItems` (JSON string) |
| `pollymain.playground.forecast_snapshots` | Saved runs | `Planungsmonat`, `Speicherzeitpunkt`, `Artikelnummer`, `Zielmonat`, `System_Forecast`, `Manuell_Forecast`, `Produktionsbedarf`, `Spaetestes_Bestelldatum` |

*Write Strategy:* Idempotent; deletes `Planungsmonat = '{YYYY-MM-01}'` before appending current run snapshot in long format.

---

## Engineering Guidelines

- **Vectorized Math:** Strictly avoid `iterrows()`. Use Pandas/NumPy vectorization (`cumsum()`, `clip()`, `np.where()`, `map()`).
- **Caching:** Wrap BQ queries in `@st.cache_data(ttl=...)` with `show_spinner`; wrap client init in `@st.cache_resource`.
- **Query Security:** Use `bigquery.ArrayQueryParameter` for all array lookups (`IN UNNEST(@param)`).
- **Naming Conventions:** German identifiers throughout dataframes and UI (`Artikelnummer`, `Aktueller_Bestand`, `Manuell_M{i}`, `Produktion_M{i}`, `Spätestes_Bestelldatum`).
- **Testing:** Unit test all domain and service logic using `pytest` against mocked DataFrames (`src/inventory_math.py`, `src/mrp_math.py`, `src/forecasting.py`, `src/bom.py`, `src/supply_tracker.py`, `src/planning_service.py`, `src/utils.py`).
