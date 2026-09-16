import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

import datetime
import pandas as pd
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

# ==========================================
# 0. SEITEN-KONFIGURATION & GLOBALE FUNKTIONEN
# ==========================================
st.set_page_config(page_title="Produktions-Forecast", layout="wide")
st.title("📦 Produktions-Forecast & Planung")

def get_target_months():
    """Berechnet den 1. der nächsten 3 Monate."""
    heute = datetime.date.today()
    m1 = (heute.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
    m2 = (m1 + datetime.timedelta(days=32)).replace(day=1)
    m3 = (m2 + datetime.timedelta(days=32)).replace(day=1)
    return [m1, m2, m3]

target_month_dates = get_target_months()

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

# UI-Labels für die Monate (z.B. "Okt 2026")
monate_de = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
m1_label = f"{monate_de[target_month_dates[0].month - 1]} {target_month_dates[0].year}"
m2_label = f"{monate_de[target_month_dates[1].month - 1]} {target_month_dates[1].year}"
m3_label = f"{monate_de[target_month_dates[2].month - 1]} {target_month_dates[2].year}"

# ==========================================
# 1. SIDEBAR (ARTIKEL & CONSTRAINTS)
# ==========================================
@st.cache_data
def load_article_master():
    return fetch_available_articles()

df_articles = load_article_master()

st.sidebar.header("⚙️ 1. Artikelauswahl")
TARGET_ARTICLES = [
    "10024-C", "10025-C", "10026-C", "10027-C", 
    "10028-C", "10029-C", "10030-C", "10031-C", "10020-C"
]

# 1. Dictionaries für blitzschnelles Mapping aufbauen
# Name -> Nummer (für die Auswertung der Auswahl)
name_to_num = dict(zip(df_articles["Anzeige_Name"], df_articles["Artikelnummer"]))
# Nummer -> Name (für das Setzen der Default-Auswahl)
num_to_name = dict(zip(df_articles["Artikelnummer"], df_articles["Anzeige_Name"]))

# 2. Default-Selektion extrem schnell ermitteln
default_selection = [num_to_name[nr] for nr in TARGET_ARTICLES if nr in num_to_name]

selected_display_names = st.sidebar.multiselect(
    "Getränke für dieses Meeting:",
    options=list(name_to_num.keys()),
    default=default_selection 
)

# 3. Direkter Dictionary-Zugriff statt DataFrame-Filtering
selected_article_numbers = [name_to_num[name] for name in selected_display_names]

if not selected_article_numbers:
    st.warning("👈 Bitte wähle links in der Seitenleiste mindestens einen Artikel aus, um mit der Planung zu beginnen.")
    st.stop()

st.sidebar.divider()
st.sidebar.header("📐 2. Parameter (MOQ & Puffer)")
st.sidebar.write("Bestimme die Nebenbedingungen für die ausgewählten Produkte.")

# Standard-Werte für das Sidebar-UI aufbauen
default_constraints = []
for name, nr in zip(selected_display_names, selected_article_numbers):
    default_constraints.append({
        "Artikelnummer": nr,
        "Name": name.split(' (')[0], # Sauberer Name fürs UI
        "MOQ": 2000,
        "Mindestbestand": 100,
        "Vorlaufzeit_Wochen": 4
    })

df_constraints_raw = pd.DataFrame(default_constraints)

# Der interaktive Editor in der Seitenleiste
edited_constraints_df = st.sidebar.data_editor(
    df_constraints_raw,
    disabled=["Artikelnummer", "Name"],
    hide_index=True,
    width='stretch'
)

# Umwandeln in ein Dictionary für unsere Mathematik-Funktionen
constraints_dict = edited_constraints_df.set_index("Artikelnummer").to_dict(orient="index")

# ==========================================
# 2. DATEN LADEN & FORECAST EDITOR
# ==========================================
@st.cache_data
def load_and_prepare_data(target_months, article_list):
    inventory = fetch_current_inventory(article_list)
    history = fetch_historical_sales(article_list)
    
    forecast = generate_system_forecast(inventory, history, target_months)
    df_merged = pd.merge(inventory, forecast, on="Artikelnummer", how="left")
    
    # Manuelle Spalten initialisieren
    for m in [1, 2, 3]:
        df_merged[f"Manuell_M{m}"] = df_merged[f"System_M{m}"].fillna(0).astype(int)
    return df_merged

# State-Management für die Tabelle
if "last_selection" not in st.session_state or st.session_state.last_selection != selected_article_numbers:
    st.session_state.plan_data = load_and_prepare_data(target_month_dates, selected_article_numbers)
    st.session_state.last_selection = selected_article_numbers

st.header("1. Erwarteter Abverkauf (Forecast anpassen)")
st.write("Das System schlägt Werte vor. Bitte passe die 'Manuell'-Spalten an.")

column_config = {
    "Artikelnummer": st.column_config.TextColumn(disabled=True),
    "Artikelname": st.column_config.TextColumn(disabled=True),
    "Aktueller_Bestand": st.column_config.NumberColumn(disabled=True),
    "System_M1": st.column_config.NumberColumn(f"Vorschlag {m1_label}", disabled=True),
    "System_M2": st.column_config.NumberColumn(f"Vorschlag {m2_label}", disabled=True),
    "System_M3": st.column_config.NumberColumn(f"Vorschlag {m3_label}", disabled=True),
    "Manuell_M1": st.column_config.NumberColumn(f"Eingabe {m1_label} ✏️", step=100),
    "Manuell_M2": st.column_config.NumberColumn(f"Eingabe {m2_label} ✏️", step=100),
    "Manuell_M3": st.column_config.NumberColumn(f"Eingabe {m3_label} ✏️", step=100),
}

edited_df = st.data_editor(
    st.session_state.plan_data,
    column_config=column_config,
    hide_index=True,
    width='stretch',
    key="data_editor"
)

# ==========================================
# 3. PRODUKTIONSBEDARF BERECHNEN
# ==========================================
st.divider()
st.header("2. Produktionsbedarf & Bestelltermine")
st.write("Bestelltermine basieren auf einer Bestellung zum 15. des Monats minus X Wochen Vorlaufzeit.")

# HIER ÜBERGEBEN WIR DAS NEUE CONSTRAINTS-DICTIONARY!
production_plan = calculate_production_needs(edited_df, target_month_dates, constraints_dict)
# UI Aufbereitung der Produktionsdaten
display_plan = production_plan.copy()
for m in [1, 2, 3]:
    display_plan[f"Bestelldatum_M{m}"] = display_plan[f"Bestelldatum_M{m}"].apply(format_date_deadline)

ui_columns = [
    "Artikelname", "MOQ", "Lead_Time_Weeks", 
    "Produktion_M1", "Bestelldatum_M1", 
    "Produktion_M2", "Bestelldatum_M2", 
    "Produktion_M3", "Bestelldatum_M3"
]

display_plan = display_plan[ui_columns].rename(
    columns={
        "Lead_Time_Weeks": "Vorlauf (Wochen)",
        "Produktion_M1": f"Produktion {m1_label}", "Bestelldatum_M1": f"Order für {m1_label}",
        "Produktion_M2": f"Produktion {m2_label}", "Bestelldatum_M2": f"Order für {m2_label}",
        "Produktion_M3": f"Produktion {m3_label}", "Bestelldatum_M3": f"Order für {m3_label}"
    }
)

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

@st.cache_data
def get_cogs_data(articles):
    return fetch_bom_for_articles(articles)

@st.cache_data
def get_material_stock(materials):
    return fetch_material_stock(materials)

with st.spinner("Berechne Materialbedarf und prüfe Lagerbestände..."):
    df_cogs = get_cogs_data(selected_article_numbers)
    
    if not df_cogs.empty:
        unique_materials = df_cogs["materialArticleNumber"].dropna().unique().tolist()
        df_mat_stock = get_material_stock(unique_materials)
        
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
                        
                        # --- NEU: ZUSAMMENFASSUNG PRO BESTELLDATUM ---
                        st.subheader("💰 Bestellvolumen")
                        # Wir gruppieren über den formatierten Datums-String
                        summary = sup_df.groupby("Spätestes_Bestelldatum")["Gesamtpreis (€)"].sum().reset_index()
                        
                        # Erstellt für jedes Bestelldatum eine schöne "Metric"-Kachel nebeneinander
                        cols = st.columns(len(summary))
                        for i, r in summary.iterrows():
                            # Trennt das echte Datum vom Warn-String für ein sauberes Layout
                            datum = r['Spätestes_Bestelldatum']
                            kosten = f"{r['Gesamtpreis (€)']:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
                            cols[i].metric(label=f"Order am: {datum}", value=kosten)
                            
                        st.write("---")
                        # ---------------------------------------------
                        
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