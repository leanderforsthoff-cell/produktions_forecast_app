import pandas as pd
from google.cloud import bigquery
from dotenv import load_dotenv
import datetime
import streamlit as st

# Lade die Umgebungsvariablen aus der .env Datei
load_dotenv()

@st.cache_resource
def get_bq_client():
    """Initialisiert den BigQuery Client mit den Credentials aus der .env"""
    return bigquery.Client()

@st.cache_data(ttl=3600, show_spinner="Lade Artikelstammdaten aus BigQuery...")
def fetch_available_articles():
    """Holt eine Liste aller relevanten Artikel (Nummer und Name) für das UI-Dropdown."""
    client = get_bq_client()
    
    # Wir holen alle Artikel aus der Bestandstabelle inkl. Namen
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

@st.cache_data(ttl=600, show_spinner="Lade aktuellen Lagerbestand...")
def fetch_current_inventory(article_list):
    """Holt den aggregierten Bestand inkl. Artikelnamen aus BigQuery."""
    if not article_list:
        return pd.DataFrame(columns=["Artikelnummer", "Artikelname", "Aktueller_Bestand"])

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
    
    # Parameter sicher übergeben
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("article_numbers", "STRING", sorted(article_list))
        ]
    )
    
    # Query mit der Konfiguration ausführen
    df = client.query(query, job_config=job_config).to_dataframe()
    
    df['Aktueller_Bestand'] = df['Aktueller_Bestand'].fillna(0).astype(int)
    
    return df

@st.cache_data(ttl=1800, show_spinner="Lade historische Verkaufsdaten...")
def fetch_historical_sales(article_list):
    if not article_list:
        return pd.DataFrame(columns=["Artikelnummer", "Verkaufsmonat", "Verkaufsmenge"])

    client = get_bq_client()
    
    query = """
        SELECT 
            articleNumber AS Artikelnummer,
            -- Expliziter Cast zu INT64, um String-Fehler bei Unix-Timestamps zu vermeiden
            DATE_TRUNC(DATE(TIMESTAMP_MILLIS(CAST(createdDate AS INT64))), MONTH) AS Verkaufsmonat,
            SUM(CAST(quantity AS FLOAT64)) AS Verkaufsmenge
        FROM 
            `pollymain.weclapp.shipmentitems`
        WHERE 
            articleNumber IN UNNEST(@article_numbers)
            AND createdDate IS NOT NULL
            -- Wir vergleichen saubere Timestamps miteinander
            AND TIMESTAMP_MILLIS(CAST(createdDate AS INT64)) >= TIMESTAMP(DATE_SUB(CURRENT_DATE(), INTERVAL 36 MONTH))
        GROUP BY 
            articleNumber,
            Verkaufsmonat
        ORDER BY 
            articleNumber, 
            Verkaufsmonat
    """
    
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("article_numbers", "STRING", sorted(article_list))
        ]
    )
    
    df = client.query(query, job_config=job_config).to_dataframe()
    df['Verkaufsmonat'] = pd.to_datetime(df['Verkaufsmonat'])
    df['Verkaufsmenge'] = df['Verkaufsmenge'].fillna(0).astype(int)
    
    return df

def save_forecast_to_bq(edited_df, production_plan, target_months):
    """
    Formatiert die Daten ins saubere Datenbank-Format (Long-Format) um, 
    löscht eventuelle alte Speichervorgänge des gleichen Meetings und speichert dann.
    Dynamisch für beliebige Planungshorizonte ohne iterrows.
    """
    if edited_df.empty or not target_months:
        return

    client = get_bq_client()
    table_id = "pollymain.playground.forecast_snapshots"
    
    # Wir definieren den aktuellen Monat als "Planungsmonat" (z.B. 2026-09-01)
    # So können wir alte Klicks auf "Speichern" aus dem selben Monat überschreiben.
    heute = datetime.date.today()
    planning_month_str = heute.replace(day=1).strftime('%Y-%m-%d')
    timestamp_now = datetime.datetime.now()
    
    # 1. Löschen der alten Einträge von diesem Monat (Idempotenz)
    delete_query = f"""
        DELETE FROM `{table_id}` 
        WHERE Planungsmonat = '{planning_month_str}'
    """
    try:
        client.query(delete_query).result()
    except Exception as e:
        # Wenn die Tabelle noch gar nicht existiert, wirft BQ einen Fehler. Den ignorieren wir.
        pass

    # 2. Daten dynamisch ins Long-Format transformieren
    # Merge für direkten Zugriff ohne Zeileniteration
    df_combined = pd.merge(
        edited_df, 
        production_plan, 
        on="Artikelnummer", 
        suffixes=("", "_prod")
    )

    month_records = []
    for m_idx, date_obj in enumerate(target_months, start=1):
        sys_col = f"System_M{m_idx}"
        man_col = f"Manuell_M{m_idx}"
        prod_col = f"Produktion_M{m_idx}"
        order_col = f"Bestelldatum_M{m_idx}"

        if sys_col in df_combined.columns and man_col in df_combined.columns:
            sub = pd.DataFrame({
                "Planungsmonat": heute.replace(day=1),
                "Speicherzeitpunkt": timestamp_now,
                "Artikelnummer": df_combined["Artikelnummer"],
                "Zielmonat": date_obj,
                "System_Forecast": df_combined[sys_col].fillna(0).astype(int),
                "Manuell_Forecast": df_combined[man_col].fillna(0).astype(int),
                "Produktionsbedarf": df_combined[prod_col].fillna(0).astype(int) if prod_col in df_combined.columns else 0,
                "Spaetestes_Bestelldatum": df_combined[order_col] if order_col in df_combined.columns else None
            })
            month_records.append(sub)

    if not month_records:
        return

    df_long = pd.concat(month_records, ignore_index=True)
    
    # 3. Speichern (Tabelle wird angelegt, falls sie nach dem DELETE noch nicht / nicht mehr existiert)
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    client.load_table_from_dataframe(df_long, table_id, job_config=job_config).result()

@st.cache_data(ttl=3600, show_spinner="Lade Stücklisten (COGS)...")
def fetch_bom_for_articles(article_list):
    """Holt die Stücklisten inkl. Lieferantendaten für die ausgewählten Artikel."""
    if not article_list:
        return pd.DataFrame()

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

@st.cache_data(ttl=600, show_spinner="Lade Materialbestände...")
def fetch_material_stock(material_numbers):
    """
    Holt den Bestand der Rohstoffe aus warehousestock.
    Schneidet eventuelle '-C' Suffixe ab und summiert die Mengen.
    """
    if not material_numbers:
        return pd.DataFrame()

    client = get_bq_client()
    
    # Da warehousestock teils -C hat, nutzen wir SPLIT in SQL, 
    # um den vorderen Teil zu nehmen und ihn als INT64 zu casten.
    query = """
        SELECT 
            CAST(SPLIT(articleNumber, '-')[OFFSET(0)] AS INT64) AS materialArticleNumber,
            SUM(CAST(quantity AS FLOAT64)) AS Aktueller_Materialbestand
        FROM 
            `pollymain.mart.warehousestock`
        WHERE 
            articleNumber IS NOT NULL
            AND CAST(SPLIT(articleNumber, '-')[OFFSET(0)] AS INT64) IN UNNEST(@mat_numbers)
        GROUP BY 
            materialArticleNumber
    """
    
    # Umwandeln in Integer-Liste für die Query
    mat_ints = sorted([int(m) for m in material_numbers if pd.notnull(m)])
    
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("mat_numbers", "INT64", mat_ints)]
    )
    df = client.query(query, job_config=job_config).to_dataframe()
    return df