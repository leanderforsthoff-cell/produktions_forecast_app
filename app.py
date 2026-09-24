import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

import datetime
import pandas as pd
import numpy as np
import streamlit as st

from src.data_integration import (
    fetch_current_inventory, 
    fetch_historical_sales, 
    save_forecast_to_bq, 
    fetch_available_articles, 
    fetch_bom_for_articles,
    fetch_material_stock
)
from src.forecasting import generate_system_forecast
from src.inventory_math import calculate_production_needs
from src.mrp_math import calculate_material_requirements
from src.utils import get_target_months

# ==========================================
# 0. SEITEN-KONFIGURATION & GLOBALE FUNKTIONEN
# ==========================================
st.set_page_config(page_title="Produktions-Forecast", layout="wide")
st.title("📦 Produktions-Forecast & Planung")

def format_date_deadline(d):
    """
    Formatiert das Datum. Liegt es in der Vergangenheit, wird das heutige 
    Datum als nächstmöglicher Aktionstag gesetzt und das alte Datum markiert.
    """
    if pd.isnull(d):
        return "-"
    
    heute = datetime.date.today()
    if d < heute:
        # Ausgabe z.B.: "16.09.2026 🔴 (eig. 15.08.2026)"
        return f"{heute.strftime('%d.%m.%Y')} 🔴 (eig. {d.strftime('%d.%m.%Y')})"
    
    return d.strftime("%d.%m.%Y")

monate_de = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]

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

# Standard-Werte für das Sidebar-UI aufbauen
df_constraints_raw = pd.DataFrame({
    "Artikelnummer": selected_article_numbers,
    "Name": [art_names.get(nr, nr) for nr in selected_article_numbers],
    "MOQ": 2000,
    "Mindestbestand": 100,
    "Vorlaufzeit_Wochen": 4
})

# Der interaktive Editor in der Seitenleiste
edited_constraints_df = st.sidebar.data_editor(
    df_constraints_raw,
    disabled=["Artikelnummer", "Name"],
    hide_index=True,
    width='stretch'
)

# Umwandeln in ein Dictionary für unsere Mathematik-Funktionen
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
    inventory = fetch_current_inventory(article_list)
    history = fetch_historical_sales(article_list)
    
    forecast = generate_system_forecast(inventory, history, target_months)
    df_merged = pd.merge(inventory, forecast, on="Artikelnummer", how="left")
    
    # Manuelle Spalten initialisieren
    for m in range(1, len(target_months) + 1):
        df_merged[f"Manuell_M{m}"] = df_merged[f"System_M{m}"].fillna(0).astype(int)
    return df_merged

# State-Management für die Tabelle
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
st.write("Bestelltermine basieren auf einer Bestellung zum 15. des Monats minus X Wochen Vorlaufzeit.")

# Produktionsplan berechnen
production_plan = calculate_production_needs(edited_df, target_month_dates, constraints_dict)

# --- A. WARNHINWEISE FÜR DROHENDE FEHLBESTÄNDE ---
out_of_stock_warnings = []
for m_idx, m_label in enumerate(month_labels, start=1):
    shortage_col = f"Fehlbestand_M{m_idx}"
    if shortage_col in production_plan.columns:
        affected = production_plan[production_plan[shortage_col] > 0]
        for _, row in affected.iterrows():
            fehlmenge = f"{row[shortage_col]:,}".replace(",", ".")
            out_of_stock_warnings.append(
                f"**{row['Artikelname']}**: Drohende Fehlmenge von **{fehlmenge} Stück** im **{m_label}** "
                f"(Bestelldeadline ist abgelaufen – Produktion nicht mehr rechtzeitig möglich!)."
            )
            
if out_of_stock_warnings:
    with st.container():
        st.error("🚨 **Achtung: Drohende Fehlbestände (Out-of-Stock)!**")
        for warnung in out_of_stock_warnings:
            st.warning(warnung)

# --- B. UI-AUFBEREITUNG DER TABELLE ---
display_plan = production_plan.copy()
for m in range(1, len(target_month_dates) + 1):
    order_col = f"Bestelldatum_M{m}"
    shortage_col = f"Fehlbestand_M{m}"
    prod_col = f"Produktion_M{m}"

    if order_col in display_plan.columns:
        display_plan[order_col] = display_plan[order_col].apply(format_date_deadline)

    # Betroffene Produktionszellen visuell mit ⚠️ hervorheben
    if shortage_col in display_plan.columns and prod_col in display_plan.columns:
        display_plan[prod_col] = np.where(
        display_plan[shortage_col] > 0,
        display_plan[prod_col].astype(str) + " ⚠️",
        display_plan[prod_col].astype(str)
    )

ui_columns = ["Artikelname", "MOQ", "Lead_Time_Weeks"]
rename_map = {"Lead_Time_Weeks": "Vorlauf (Wochen)"}

for m_idx, m_label in enumerate(month_labels, start=1):
    ui_columns.extend([f"Produktion_M{m_idx}", f"Bestelldatum_M{m_idx}"])
    rename_map[f"Produktion_M{m_idx}"] = f"Produktion {m_label}"
    rename_map[f"Bestelldatum_M{m_idx}"] = f"Order für {m_label}"

valid_ui_cols = [c for c in ui_columns if c in display_plan.columns]
display_plan = display_plan[valid_ui_cols].rename(columns=rename_map)

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
st.write("Berechnet auf Basis des Produktionsplans, abzgl. aktuellem Materialbestand. Gruppiert nach Lieferant.")

with st.spinner("Berechne Materialbedarf und prüfe Lagerbestände..."):
    df_cogs = fetch_bom_for_articles(selected_article_numbers)
    
    if not df_cogs.empty:
        unique_materials = df_cogs["materialArticleNumber"].dropna().unique().tolist()
        df_mat_stock = fetch_material_stock(unique_materials)
        
        material_orders, material_details = calculate_material_requirements(production_plan, df_cogs, df_mat_stock)
        
        # --- A. BESTELLLISTEN FÜR DIE LIEFERANTEN ---
        if not material_orders.empty:
            display_orders = material_orders.copy()
            # Hier nutzen wir jetzt auch unsere smarte Datums-Funktion!
            display_orders["Spätestes_Bestelldatum"] = display_orders["Spätestes_Bestelldatum"].apply(format_date_deadline)
            display_orders["Für_Produktion_Am"] = display_orders["Für_Produktion_Am"].apply(format_date_deadline)

            # Spalten schöner benennen
            display_orders = display_orders.rename(columns={
                "Einzelpreis": "Einzelpreis (€)",
                "Gesamtpreis": "Gesamtpreis (€)"
            })

            suppliers = sorted(display_orders["Lieferant"].dropna().unique())
            
            if suppliers:
                tabs = st.tabs([str(s) for s in suppliers])
                
                spalten_reihenfolge = [
                    "Material_Nr", 
                    "Material_Name", 
                    "Bestellmenge", 
                    "Einheit", 
                    "Einzelpreis (€)",
                    "Gesamtpreis (€)",
                    "Vorlaufzeit_Tage",
                    "Spätestes_Bestelldatum", 
                    "Für_Produktion_Am", 
                    "Benötigt_Für_Produkt"
                ]
                
                for idx, supplier_name in enumerate(suppliers):
                    with tabs[idx]:
                        sup_df = display_orders[display_orders["Lieferant"] == supplier_name]
                        
                        # --- ZUSAMMENFASSUNG: CARD-GRID PRO PRODUKTIONSTERMIN MIT BORDER ---
                        st.subheader("💰 Bestellvolumen")
                        
                        summary = sup_df.groupby("Für_Produktion_Am")["Gesamtpreis (€)"].agg(["sum", "count"]).reset_index()
                        
                        cols = st.columns(len(summary))
                        for s_idx, r in summary.iterrows():
                            datum = r["Für_Produktion_Am"]
                            kosten = f"{r['sum']:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
                            anzahl = int(r["count"])
                            
                            with cols[s_idx]:
                                with st.container(border=True):
                                    st.metric(
                                        label=f"Produktion: {datum}",
                                        value=kosten,
                                        delta=f"{anzahl} Position{'en' if anzahl != 1 else ''}",
                                        delta_color="off"
                                    )
                                    
                        st.write("---")
                        # ----------------------------------------------------------------------
                        
                        # Die eigentliche Tabelle anzeigen
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
            
            # Datum schick formatieren
            material_details["Produktion_Am"] = material_details["Produktion_Am"].apply(format_date_deadline)
            
            # NEU: Gruppierung in ausklappbare Menüs (Expanders) pro Produkt
            unique_products = sorted(material_details["Produkt"].unique())
            
            for product in unique_products:
                # Wir filtern die Tabelle für das jeweilige Produkt
                prod_df = material_details[material_details["Produkt"] == product]
                
                with st.expander(f"📦 Materialbedarf für: {product}"):
                    # Wir blenden die Spalte "Produkt" aus, da sie im Titel des Expanders steht
                    st.dataframe(prod_df.drop(columns=["Produkt"]), hide_index=True, width='stretch')

    else:
        st.warning("Keine Stücklisten (COGS) für die ausgewählten Produkte gefunden.")