"""Streamlit-Webanwendung für Produktions-Forecasting, Produktionsplanung und Material Requirements Planning (MRP)."""

import datetime
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

import pandas as pd
import numpy as np
import streamlit as st

from src.data_integration import (
    get_bq_client as _get_bq_client,
    fetch_available_articles as _fetch_available_articles,
    fetch_current_inventory as _fetch_current_inventory,
    fetch_historical_sales as _fetch_historical_sales,
    fetch_bom_for_articles as _fetch_bom_for_articles,
    fetch_material_stock as _fetch_material_stock,
    fetch_open_purchase_orders as _fetch_open_purchase_orders,
    fetch_open_production_orders as _fetch_open_production_orders,
    save_forecast_to_bq
)
from src.planning_service import (
    run_full_planning_cycle,
    extract_out_of_stock_warnings,
    format_display_plan,
    build_initial_planning_matrix,
    load_and_prepare_data as _service_load_and_prepare_data,
    prepare_supplier_order_summary,
)
from src.utils import get_target_months, format_date_deadline, format_currency, format_quantity, monate_de, to_date

# ==========================================
# 0. SEITEN-KONFIGURATION & GLOBALE FUNKTIONEN
# ==========================================
st.set_page_config(page_title="Produktions-Forecast", layout="wide")
st.title("📦 Produktions-Forecast & Planung")

# ==========================================
# STREAMLIT CACHING (DATA INTEGRATION LAYER)
# ==========================================
@st.cache_resource
def get_bq_client():
    """Hält den initialisierten BigQuery-Client als Streamlit-Ressource im Cache."""
    return _get_bq_client()

@st.cache_data(ttl=3600, show_spinner="Lade Artikelstammdaten aus BigQuery...")
def fetch_available_articles():
    """Lädt verfügbare Fertigwarenartikel aus BigQuery (1 Stunde gecacht)."""
    return _fetch_available_articles(client=get_bq_client())

@st.cache_data(ttl=600, show_spinner="Lade aktuellen Lagerbestand...")
def fetch_current_inventory(article_list):
    """Lädt den aktuellen Fertigwarenbestand für die gewählten Artikel (10 Minuten gecacht)."""
    return _fetch_current_inventory(article_list, client=get_bq_client())

@st.cache_data(ttl=1800, show_spinner="Lade historische Verkaufsdaten...")
def fetch_historical_sales(article_list):
    """Lädt monatliche Verkaufsdaten der letzten 36 Monate (30 Minuten gecacht)."""
    return _fetch_historical_sales(article_list, client=get_bq_client())

@st.cache_data(ttl=3600, show_spinner="Lade Stücklisten (COGS)...")
def fetch_bom_for_articles(article_list):
    """Lädt Stücklisten und Lieferanteninformationen für die Artikel (1 Stunde gecacht)."""
    return _fetch_bom_for_articles(article_list, client=get_bq_client())

@st.cache_data(ttl=600, show_spinner="Lade Materialbestände...")
def fetch_material_stock(material_numbers):
    """Lädt Rohstoff-Lagerbestände für die Stücklisten-Komponenten (10 Minuten gecacht)."""
    return _fetch_material_stock(material_numbers, client=get_bq_client())

@st.cache_data(ttl=600, show_spinner="Lade offene Materialbestellungen...")
def fetch_open_purchase_orders():
    """Lädt offene Materialbestellungen aus BigQuery (10 Minuten gecacht)."""
    return _fetch_open_purchase_orders(client=get_bq_client(), reference_date=datetime.date.today())

@st.cache_data(ttl=600, show_spinner="Lade offene Produktionsaufträge...")
def fetch_open_production_orders():
    """Lädt offene Produktionsaufträge und deren Materialbedarfe aus BigQuery (10 Minuten gecacht)."""
    return _fetch_open_production_orders(client=get_bq_client(), reference_date=datetime.date.today())

# ==========================================
# 1. SIDEBAR (ARTIKEL & CONSTRAINTS)
# ==========================================
df_articles = fetch_available_articles()

st.sidebar.header("⚙️ 1. Artikelauswahl")
TARGET_ARTICLES = [
    "10024-C", "10025-C", "10026-C", "10027-C", 
    "10028-C", "10029-C", "10030-C", "10031-C", "10020-C"
]

art_names = dict(zip(df_articles["Artikelnummer"], df_articles["Artikelname"]))

selected_article_numbers = st.sidebar.multiselect(
    "Getränke für dieses Meeting:",
    options=df_articles["Artikelnummer"].tolist(),
    default=[nr for nr in TARGET_ARTICLES if nr in art_names],
    format_func=lambda nr: f"{art_names.get(nr, nr)} ({nr})"
)

if not selected_article_numbers:
    st.warning("👈 Bitte wähle links in der Seitenleiste mindestens einen Artikel aus, um mit der Planung zu beginnen.")
    st.stop()

st.sidebar.divider()
st.sidebar.header("⏱️ 2. Planungshorizont")
horizon_months = st.sidebar.slider("Monate in die Zukunft:", min_value=1, max_value=6, value=3)
target_month_dates = get_target_months(horizon_months=horizon_months)
month_labels = [f"{monate_de[d.month - 1]} {d.year}" for d in target_month_dates]

st.sidebar.divider()
st.sidebar.header("📐 3. Parameter (MOQ & Puffer)")
st.sidebar.write("Bestimme die Nebenbedingungen für die ausgewählten Produkte.")

df_constraints_raw = pd.DataFrame({
    "Artikelnummer": selected_article_numbers,
    "Name": [art_names.get(nr, nr) for nr in selected_article_numbers],
    "MOQ": 2000,
    "Mindestbestand": 100,
    "Vorlaufzeit_Wochen": 4
})

edited_constraints_df = st.sidebar.data_editor(
    df_constraints_raw,
    disabled=["Artikelnummer", "Name"],
    hide_index=True,
    width='stretch'
)

constraints_dict = edited_constraints_df.set_index("Artikelnummer").to_dict(orient="index")

st.sidebar.divider()
if st.sidebar.button("🔄 Cache leeren & aktualisieren", help="Löscht den Zwischenspeicher und lädt Live-Daten aus BigQuery neu."):
    st.cache_data.clear()
    st.session_state.clear()
    st.rerun()

# ==========================================
# 2. DATEN LADEN & FORECAST EDITOR
# ==========================================
def load_and_prepare_data(target_months, article_list):
    """Lädt Bestände sowie Historie und delegiert die Matrixerstellung an planning_service."""
    return _service_load_and_prepare_data(
        target_months,
        article_list,
        fetch_inventory_fn=fetch_current_inventory,
        fetch_history_fn=fetch_historical_sales,
    )

if (
    "last_selection" not in st.session_state 
    or st.session_state.last_selection != selected_article_numbers
    or "last_horizon" not in st.session_state
    or st.session_state.last_horizon != horizon_months
):
    st.session_state.plan_data = load_and_prepare_data(target_month_dates, selected_article_numbers)
    st.session_state.last_selection = selected_article_numbers
    st.session_state.last_horizon = horizon_months

st.header("1. Erwarteter Abverkauf (Forecast anpassen)")
st.write("Das System schlägt Werte vor. Bitte passe die 'Manuell'-Spalten an.")

column_config = {
    "Artikelnummer": st.column_config.TextColumn(disabled=True),
    "Artikelname": st.column_config.TextColumn(disabled=True),
    "Aktueller_Bestand": st.column_config.NumberColumn(disabled=True),
}
for m_idx, m_label in enumerate(month_labels, start=1):
    column_config[f"System_M{m_idx}"] = st.column_config.NumberColumn(f"Vorschlag {m_label}", disabled=True)
    column_config[f"Manuell_M{m_idx}"] = st.column_config.NumberColumn(f"Eingabe {m_label} ✏️", step=100)

edited_df = st.data_editor(
    st.session_state.plan_data,
    column_config=column_config,
    hide_index=True,
    width='stretch',
    key=f"data_editor_{horizon_months}"
)

# ==========================================
# 3. PRODUKTIONSBEDARF BERECHNEN
# ==========================================
st.divider()
st.header("2. Produktionsbedarf & Bestelltermine")
st.write("Bestelltermine basieren auf dem spätestmöglichen Starttermin, damit die Produktion zum 1. des Monats fertig ist.")
st.caption("ℹ️ Offene Produktionen mit Fertigstellung im aktuellen Monat werden dem Anfangsbestand des ersten Planungsmonats gutgeschrieben und decken die Bedarfe der kommenden Monate ab.")

df_open_prod = fetch_open_production_orders()
df_cogs = fetch_bom_for_articles(selected_article_numbers)

if not df_cogs.empty:
    unique_materials = df_cogs["materialArticleNumber"].dropna().unique().tolist()
    df_mat_stock = fetch_material_stock(unique_materials)
    df_open_po = fetch_open_purchase_orders()
else:
    df_mat_stock = pd.DataFrame()
    df_open_po = pd.DataFrame()

# Zentraler Aufruf des Planungszyklus
planning_res = run_full_planning_cycle(
    edited_df,
    target_month_dates,
    constraints_dict,
    df_open_prod=df_open_prod,
    df_cogs=df_cogs,
    df_mat_stock=df_mat_stock,
    df_open_po=df_open_po,
    reference_date=datetime.date.today(),
)

production_plan = planning_res["production_plan"]
open_prod_shortages = planning_res["open_prod_shortages"]
material_orders = planning_res["material_orders"]
material_details = planning_res["material_details"]
df_cash_needs = planning_res["cash_needs"]

# --- A. WARNHINWEISE FÜR OFFENE PRODUKTIONEN (MATERIALMANGEL) ---
if open_prod_shortages:
    with st.container():
        st.error("🚨 **Achtung: Fehlende Materialien für bereits angesetzte Produktionen!**")
        for s in open_prod_shortages:
            d_str = format_date_deadline(s["target_end_date"])
            qty_str = format_quantity(s["shortage_qty"], s["unit"])
            st.warning(
                f"**Auftrag {s['production_order_id']} ({s['product_name']})** zum **{d_str}**: "
                f"Es fehlen **{qty_str}** von **{s['material_name']}**! ({s['reason']})"
            )

# --- B. WARNHINWEISE FÜR DROHENDE FEHLBESTÄNDE (NEUE PRODUKTIONEN) ---
out_of_stock_warnings = extract_out_of_stock_warnings(production_plan, month_labels)
if out_of_stock_warnings:
    with st.container():
        st.error("🚨 **Achtung: Drohende Fehlbestände (Out-of-Stock)!**")
        for warnung in out_of_stock_warnings:
            st.warning(warnung)

# --- C. UI-AUFBEREITUNG DER TABELLE ---
display_plan = format_display_plan(production_plan, target_month_dates, month_labels)
st.dataframe(display_plan, hide_index=True, width='content')

# ==========================================
# 4. DATEN SPEICHERN
# ==========================================
if st.button("💾 Forecast speichern", type="primary"):
    with st.spinner("Lösche alte Daten dieses Monats und speichere neu..."):
        try:
            save_forecast_to_bq(edited_df, production_plan, target_month_dates)
            st.success("Erfolgreich gespeichert! (Alte Speicherungen von diesem Meeting wurden sauber überschrieben).")
        except Exception as e:
            st.error(f"Fehler beim Speichern: {e}")

# ==========================================
# 5. MATERIALBEDARFSPLANUNG (MRP)
# ==========================================
st.divider()
st.header("3. Material-Bestelllisten (MRP)")
st.write("Berechnet auf Basis des Produktionsplans, abzgl. aktuellem Materialbestand und offenen Bestellungen. Gruppiert nach Lieferant.")

if not df_cogs.empty:
    if open_prod_shortages:
        with st.expander("🚨 Materialengpässe bei offenen Produktionsaufträgen", expanded=True):
            st.write("Für folgende bereits angesetzte Produktionen reicht das Material auf Lager oder in rechtzeitigen Bestellungen nicht aus:")
            df_ops = pd.DataFrame(open_prod_shortages)[[
                "production_order_id", "product_name", "target_end_date", "material_name", "shortage_qty", "unit", "reason"
            ]].rename(columns={
                "production_order_id": "Auftrags-ID",
                "product_name": "Produkt",
                "target_end_date": "Fertigstellung",
                "material_name": "Fehlendes Material",
                "shortage_qty": "Fehlmenge",
                "unit": "Einheit",
                "reason": "Ursache"
            })
            df_ops["Fertigstellung"] = df_ops["Fertigstellung"].apply(format_date_deadline)
            df_ops["Fehlmenge"] = df_ops["Fehlmenge"].apply(format_quantity)
            st.dataframe(df_ops, hide_index=True, width='stretch')
    
    # --- A. BESTELLLISTEN FÜR DIE LIEFERANTEN ---
    if not material_orders.empty:
        display_orders = material_orders.copy()
        display_orders["Produktion_Datum"] = display_orders["Für_Produktion_Am"].apply(to_date)
        display_orders["Spätestes_Bestelldatum"] = display_orders["Spätestes_Bestelldatum"].apply(format_date_deadline)
        display_orders["Für_Produktion_Am"] = display_orders["Für_Produktion_Am"].apply(format_date_deadline)
        display_orders = display_orders.rename(columns={
            "Einzelpreis": "Einzelpreis (€)",
            "Gesamtpreis": "Gesamtpreis (€)"
        })

        suppliers = sorted(display_orders["Lieferant"].dropna().unique())
        
        if suppliers:
            tabs = st.tabs([str(s) for s in suppliers])
            spalten_reihenfolge = [
                "Material_Nr", "Material_Name", "Bestellmenge", "Einheit", 
                "Einzelpreis (€)", "Gesamtpreis (€)", "Vorlaufzeit_Tage",
                "Spätestes_Bestelldatum", "Für_Produktion_Am", "Benötigt_Für_Produkt"
            ]
            
            for idx, supplier_name in enumerate(suppliers):
                with tabs[idx]:
                    sup_df = display_orders[display_orders["Lieferant"] == supplier_name]
                    st.subheader("💰 Bestellvolumen")
                    
                    summary = prepare_supplier_order_summary(sup_df)
                    
                    if not summary.empty:
                        cols = st.columns(len(summary))
                        for s_idx, r in enumerate(summary.to_dict(orient="records")):
                            with cols[s_idx]:
                                with st.container(border=True):
                                    st.metric(
                                        label=f"Produktion: {r['Für_Produktion_Am']}",
                                        value=r["formatted_cost"],
                                        delta=r["delta_text"],
                                        delta_color="off"
                                    )
                                
                    st.write("---")
                    st.dataframe(sup_df[spalten_reihenfolge], hide_index=True, width='stretch')
        else:
            st.info("Alle Lieferanten-Felder sind leer.")
    else:
        st.success("Aktuell ausreichender Materialbestand für den geplanten Produktionszeitraum! Keine Bestellungen notwendig.")
        
    # --- B. DETAILLIERTE STÜCKLISTEN-AUFLÖSUNG ---
    if not material_details.empty:
        st.write("---")
        st.subheader("📋 Detaillierte Materialverwendung pro Produkt")
        st.write("Diese Übersicht zeigt, wie der aktuelle Lagerbestand auf die geplanten Produktionen aufgeteilt wird.")
        
        material_details["Produktion_Am"] = material_details["Produktion_Am"].apply(format_date_deadline)
        unique_products = sorted(material_details["Produkt"].unique())
        
        for product in unique_products:
            prod_df = material_details[material_details["Produkt"] == product]
            with st.expander(f"📦 Materialbedarf für: {product}"):
                st.dataframe(prod_df.drop(columns=["Produkt"]), hide_index=True, width='stretch')

else:
    st.warning("Keine Stücklisten (COGS) für die ausgewählten Produkte gefunden.")

# ==========================================
# 6. CASHBEDARF PRO MONAT
# ==========================================
st.divider()
st.header("4. Cashbedarf pro Monat")
st.write("Aggregierter Cashbedarf basierend auf den spätesten Bestelldaten für Material und Produktion. Bestellungen mit abgelaufener Frist (Lost Sales) werden nicht berücksichtigt.")

if not df_cash_needs.empty:
    st.dataframe(
        df_cash_needs,
        column_config={
            "Cashbedarf (€)": st.column_config.NumberColumn(
                "Cashbedarf (€)",
                format="%.2f €"
            )
        },
        hide_index=True,
        width='stretch'
    )
else:
    st.info("Kein Cashbedarf ermittelt, da keine rechtzeitig bestellbaren Bedarfe im Planungszeitraum anfallen.")
