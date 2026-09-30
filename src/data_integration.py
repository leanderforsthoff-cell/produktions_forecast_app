"""BigQuery-Datenintegration für Stammdaten, Bestände, Absätze, Stücklisten und Snapshots."""

import datetime
import json
import logging
import pandas as pd
from google.cloud import bigquery
from google.api_core.exceptions import NotFound
from dotenv import load_dotenv
from src.utils import parse_weclapp_date, to_date

load_dotenv()


def get_bq_client():
    """Initialisiert und liefert einen Google Cloud BigQuery-Client."""
    return bigquery.Client()


def _get_min_ms(ref_dt):
    """Berechnet UTC-Millisekunden für den Vortag eines Stichtags zur Vorfilterung in SQL."""
    if ref_dt is None:
        return None
    d = ref_dt - datetime.timedelta(days=1)
    return int(datetime.datetime.combine(d, datetime.time.min).replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def fetch_available_articles(client=None):
    """Lädt aktive Fertigwarenartikel aus BigQuery für die Artikelauswahl."""
    if client is None:
        client = get_bq_client()

    query = """
        SELECT DISTINCT 
            stock.article_number AS Artikelnummer,
            art.name AS Artikelname
        FROM 
            `pollymain.weclapp.article_totalStockQuantity` AS stock
        LEFT JOIN
            `pollymain.weclapp.article` AS art
        ON 
            stock.article_number = art.articleNumber
        WHERE 
            stock.article_number IS NOT NULL
            AND art.active = TRUE
        ORDER BY 
            Artikelname
    """
    return client.query(query).to_dataframe()


def fetch_current_inventory(article_list, client=None):
    """Lädt den aktuellen Lagerbestand der angegebenen Fertigwarenartikel."""
    if not article_list:
        return pd.DataFrame(columns=["Artikelnummer", "Artikelname", "Aktueller_Bestand"])

    if client is None:
        client = get_bq_client()

    query = """
        SELECT 
            stock.article_number AS Artikelnummer,
            art.name AS Artikelname,
            SUM(CAST(stock.quantity AS FLOAT64)) AS Aktueller_Bestand
        FROM 
            `pollymain.weclapp.article_totalStockQuantity` AS stock
        LEFT JOIN
            `pollymain.weclapp.article` AS art
        ON 
            stock.article_number = art.articleNumber
        WHERE 
            stock.article_number IN UNNEST(@article_numbers)
        GROUP BY 
            stock.article_number,
            art.name
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("article_numbers", "STRING", sorted(article_list))]
    )
    df = client.query(query, job_config=job_config).to_dataframe()
    df['Aktueller_Bestand'] = df['Aktueller_Bestand'].fillna(0).astype(int)
    return df


def fetch_historical_sales(article_list, client=None):
    """Lädt monatliche Absatzdaten der letzten 36 Monate für die Artikel."""
    if not article_list:
        return pd.DataFrame(columns=["Artikelnummer", "Verkaufsmonat", "Verkaufsmenge"])

    if client is None:
        client = get_bq_client()

    query = """
        SELECT 
            articleNumber AS Artikelnummer,
            DATE_TRUNC(DATE(TIMESTAMP_MILLIS(CAST(createdDate AS INT64))), MONTH) AS Verkaufsmonat,
            SUM(CAST(quantity AS FLOAT64)) AS Verkaufsmenge
        FROM 
            `pollymain.weclapp.shipmentitems`
        WHERE 
            articleNumber IN UNNEST(@article_numbers)
            AND createdDate IS NOT NULL
            AND TIMESTAMP_MILLIS(CAST(createdDate AS INT64)) >= TIMESTAMP(DATE_SUB(CURRENT_DATE(), INTERVAL 36 MONTH))
        GROUP BY 
            articleNumber,
            Verkaufsmonat
        ORDER BY 
            articleNumber, 
            Verkaufsmonat
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("article_numbers", "STRING", sorted(article_list))]
    )
    df = client.query(query, job_config=job_config).to_dataframe()
    df['Verkaufsmonat'] = pd.to_datetime(df['Verkaufsmonat'])
    df['Verkaufsmenge'] = df['Verkaufsmenge'].fillna(0).astype(int)
    return df


def save_forecast_to_bq(edited_df, production_plan, target_months, client=None):
    """Speichert den aktuellen Planungs- und Produktions-Snapshot idempotent in BigQuery."""
    if edited_df.empty or not target_months:
        return

    if client is None:
        client = get_bq_client()

    table_id = "pollymain.playground.forecast_snapshots"
    heute = datetime.date.today()
    planning_month_str = heute.replace(day=1).strftime('%Y-%m-%d')
    timestamp_now = datetime.datetime.now()

    # Idempotentes Löschen bestehender Snapshots dieses Monats
    try:
        client.query(f"DELETE FROM `{table_id}` WHERE Planungsmonat = '{planning_month_str}'").result()
    except NotFound:
        pass

    df_combined = pd.merge(edited_df, production_plan, on="Artikelnummer", suffixes=("", "_prod"))
    month_records = []

    for m_idx, date_obj in enumerate(target_months, start=1):
        sys_col, man_col = f"System_M{m_idx}", f"Manuell_M{m_idx}"
        prod_col, order_col = f"Produktion_M{m_idx}", f"Bestelldatum_M{m_idx}"

        if sys_col in df_combined.columns and man_col in df_combined.columns:
            month_records.append(pd.DataFrame({
                "Planungsmonat": heute.replace(day=1),
                "Speicherzeitpunkt": timestamp_now,
                "Artikelnummer": df_combined["Artikelnummer"],
                "Zielmonat": date_obj,
                "System_Forecast": df_combined[sys_col].fillna(0).astype(int),
                "Manuell_Forecast": df_combined[man_col].fillna(0).astype(int),
                "Produktionsbedarf": df_combined[prod_col].fillna(0).astype(int) if prod_col in df_combined.columns else 0,
                "Spaetestes_Bestelldatum": df_combined[order_col] if order_col in df_combined.columns else None
            }))

    if not month_records:
        return

    df_long = pd.concat(month_records, ignore_index=True)
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    client.load_table_from_dataframe(df_long, table_id, job_config=job_config).result()


def fetch_bom_for_articles(article_list, client=None):
    """Lädt Stücklisten (BOM) und Lieferantenkonditionen (COGS) für die Artikel."""
    if not article_list:
        return pd.DataFrame()

    if client is None:
        client = get_bq_client()

    base_numbers = sorted(list(set([str(art).split('-')[0] for art in article_list])))
    query = """
        SELECT 
            productArticleNumber,
            materialArticleNumber,
            materialName,
            CAST(quantity AS FLOAT64) AS quantity,
            unitName,
            supplierNumber,
            company AS Lieferant,
            CAST(procurementLeadDays AS INT64) AS procurementLeadDays,
            CAST(articleunitprice AS FLOAT64) AS articleunitprice
        FROM 
            `pollymain.mart.COGS`
        WHERE 
            is_in_article_mapping = TRUE
            AND CAST(productArticleNumber AS STRING) IN UNNEST(@base_numbers)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("base_numbers", "STRING", base_numbers)]
    )
    return client.query(query, job_config=job_config).to_dataframe()


def fetch_material_stock(material_numbers, client=None):
    """Lädt aggregierte Rohstoffbestände für die angegebenen Materialnummern."""
    if not material_numbers:
        return pd.DataFrame()

    if client is None:
        client = get_bq_client()

    mat_ints = [int(m) for m in material_numbers if pd.notnull(m) and str(m).isdigit()]
    query = """
        SELECT 
            SAFE_CAST(SPLIT(articleNumber, '-')[OFFSET(0)] AS INT64) AS materialArticleNumber,
            SUM(CAST(quantity AS FLOAT64)) AS Aktueller_Materialbestand
        FROM 
            `pollymain.mart.warehousestock`
        WHERE 
            articleNumber IS NOT NULL
            AND SAFE_CAST(SPLIT(articleNumber, '-')[OFFSET(0)] AS INT64) IN UNNEST(@mat_numbers)
        GROUP BY 
            materialArticleNumber
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("mat_numbers", "INT64", mat_ints)]
    )
    return client.query(query, job_config=job_config).to_dataframe()


def _extract_po_item(order_id, art, qty, d_date, fallback_date, ref_dt):
    """Extrahiert und validiert eine Bestellposition aus Weclapp."""
    if pd.isna(art) or pd.isna(qty) or not str(art).strip():
        return None
    d = parse_weclapp_date(d_date) or fallback_date
    if d is None or (ref_dt is not None and d < ref_dt):
        return None
    base = str(art).split("-")[0].strip()
    return {
        "order_id": order_id,
        "articleNumber": str(art),
        "materialArticleNumber": int(base) if base.isdigit() else base,
        "quantity": float(qty),
        "plannedDeliveryDate": d
    }


def fetch_open_purchase_orders(client=None, reference_date=None):
    """Lädt offene Materialbestellungen (Purchase Orders) aus BigQuery."""
    if client is None:
        client = get_bq_client()

    ref_dt = to_date(reference_date)
    min_ms = _get_min_ms(ref_dt)
    where_clause = "WHERE plannedDeliveryDate IS NOT NULL"
    if min_ms is not None:
        where_clause += f" AND SAFE_CAST(plannedDeliveryDate AS INT64) >= {min_ms}"

    query = f"""
        SELECT 
            id,
            orderDate,
            plannedDeliveryDate,
            purchaseOrderItems,
            purchaseOrderItems_0_articleNumber, purchaseOrderItems_0_quantity, purchaseOrderItems_0_plannedDeliveryDate,
            purchaseOrderItems_1_articleNumber, purchaseOrderItems_1_quantity, purchaseOrderItems_1_plannedDeliveryDate,
            purchaseOrderItems_2_articleNumber, purchaseOrderItems_2_quantity, purchaseOrderItems_2_plannedDeliveryDate,
            purchaseOrderItems_3_articleNumber, purchaseOrderItems_3_quantity, purchaseOrderItems_3_plannedDeliveryDate
        FROM 
            `pollymain.weclapp.purchaseorder`
        {where_clause}
    """
    df_raw = client.query(query).to_dataframe()
    cols = ["order_id", "articleNumber", "materialArticleNumber", "quantity", "plannedDeliveryDate"]
    if df_raw.empty:
        return pd.DataFrame(columns=cols)

    records = []
    for row in df_raw.itertuples(index=False):
        order_id = row.id
        po_delivery = parse_weclapp_date(getattr(row, "plannedDeliveryDate", None))
        items_json = getattr(row, "purchaseOrderItems", None)

        if pd.notna(items_json) and str(items_json).strip():
            try:
                for it in json.loads(items_json):
                    rec = _extract_po_item(
                        order_id, it.get("articleNumber"), it.get("quantity"),
                        it.get("plannedDeliveryDate"), po_delivery, ref_dt
                    )
                    if rec:
                        records.append(rec)
            except Exception as e:
                logging.warning("Fehler beim Parsen von purchaseOrderItems für Order %s: %s", order_id, e)
        else:
            for n in range(4):
                rec = _extract_po_item(
                    order_id,
                    getattr(row, f"purchaseOrderItems_{n}_articleNumber", None),
                    getattr(row, f"purchaseOrderItems_{n}_quantity", None),
                    getattr(row, f"purchaseOrderItems_{n}_plannedDeliveryDate", None),
                    po_delivery,
                    ref_dt
                )
                if rec:
                    records.append(rec)

    return pd.DataFrame(records) if records else pd.DataFrame(columns=cols)


def fetch_open_production_orders(client=None, reference_date=None):
    """Lädt offene Produktionsaufträge (Production Orders) aus BigQuery."""
    if client is None:
        client = get_bq_client()

    ref_dt = to_date(reference_date)
    min_ms = _get_min_ms(ref_dt)
    where_clause = "WHERE targetEndDate IS NOT NULL"
    if min_ms is not None:
        where_clause += f" AND SAFE_CAST(targetEndDate AS INT64) >= {min_ms}"

    query = f"""
        SELECT 
            id,
            articleNumber,
            targetQuantity,
            targetEndDate
        FROM 
            `pollymain.weclapp.productionorder`
        {where_clause}
    """
    df_raw = client.query(query).to_dataframe()
    cols = ["productionOrderId", "articleNumber", "targetQuantity", "targetEndDate"]
    if df_raw.empty:
        return pd.DataFrame(columns=cols)

    orders = []
    for row in df_raw.itertuples(index=False):
        end_date = parse_weclapp_date(getattr(row, "targetEndDate", None))
        if end_date is None or (ref_dt is not None and end_date < ref_dt):
            continue

        art = getattr(row, "articleNumber", None)
        qty = getattr(row, "targetQuantity", None)
        if pd.notna(art) and pd.notna(qty):
            orders.append({
                "productionOrderId": getattr(row, "id", None),
                "articleNumber": str(art),
                "targetQuantity": float(qty),
                "targetEndDate": end_date
            })

    return pd.DataFrame(orders) if orders else pd.DataFrame(columns=cols)
