import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

import streamlit as st
import pandas as pd
from src.data_integration import fetch_current_inventory, fetch_historical_sales, save_forecast_to_bq
from src.forecasting import generate_system_forecast
from src.inventory_math import calculate_production_needs
import datetime

def get_target_months():
    """Generiert die echten Datums-Objekte für den 1. der nächsten 3 Monate"""
    heute = datetime.date.today()
    # Den 1. des nächsten Monats berechnen
    m1 = (heute.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
    m2 = (m1 + datetime.timedelta(days=32)).replace(day=1)
    m3 = (m2 + datetime.timedelta(days=32)).replace(day=1)
    return [m1, m2, m3]

target_month_dates = get_target_months()

# Labels für die Spalten (Deutsch formatiert, z.B. "Okt 2026")
monate_de = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
m1_label = f"{monate_de[target_month_dates[0].month - 1]} {target_month_dates[0].year}"
m2_label = f"{monate_de[target_month_dates[1].month - 1]} {target_month_dates[1].year}"
m3_label = f"{monate_de[target_month_dates[2].month - 1]} {target_month_dates[2].year}"

# --- 1. SEITENKONFIGURATION ---
st.set_page_config(page_title="Produktions-Forecast", layout="wide")
st.title("📦 Produktions-Forecast & Planung")

# --- 2. DATEN LADEN (mit Cache, damit es im Meeting schnell bleibt) ---
@st.cache_data
def load_and_prepare_data(target_months):
    inventory = fetch_current_inventory()
    history = fetch_historical_sales()
    
    # Hier übergeben wir jetzt die Monate an die neue Logik
    forecast = generate_system_forecast(inventory, history, target_months)
    
    df_merged = pd.merge(inventory, forecast, on="Artikelnummer", how="left")
    
    df_merged["Manuell_M1"] = df_merged["System_M1"].fillna(0).astype(int)
    df_merged["Manuell_M2"] = df_merged["System_M2"].fillna(0).astype(int)
    df_merged["Manuell_M3"] = df_merged["System_M3"].fillna(0).astype(int)
    
    return df_merged

# State initialisieren
if "plan_data" not in st.session_state:
    st.session_state.plan_data = load_and_prepare_data(target_month_dates)

# --- 3. EINGABEBEREICH (Die Meeting-Ansicht) ---
st.header("1. Erwarteter Abverkauf (Forecast anpassen)")
st.write("Das System schlägt Werte vor. Bitte passe die 'Manuell'-Spalten an.")

# Spalten definieren, die man bearbeiten darf
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

# Editierbare Tabelle anzeigen
edited_df = st.data_editor(
    st.session_state.plan_data,
    column_config=column_config,
    hide_index=True,
    width='stretch',
    key="data_editor"
)

# --- 4. BERECHNUNG & ERGEBNIS ---
st.divider()
st.header("2. Produktionsbedarf & Bestelltermine")
st.write("Bestelltermine basieren auf einer Bestellung zum 15. des Monats minus X Wochen Vorlaufzeit.")

# Wir übergeben nun auch die Zieldaten an die Mathe-Funktion
production_plan = calculate_production_needs(edited_df, target_month_dates)

# Fürs UI bereiten wir die Datumsangaben schön auf (z.B. "15.08.2026")
display_plan = production_plan.copy()
for m in [1, 2, 3]:
    # Wenn ein Datum drin steht, formatiere es, sonst mach einen Strich "-"
    display_plan[f"Bestelldatum_M{m}"] = display_plan[f"Bestelldatum_M{m}"].apply(
        lambda d: d.strftime("%d.%m.%Y") if pd.notnull(d) else "-"
    )

# Spalten fürs UI auswählen und umbenennen
ui_columns = ["Artikelname", "MOQ", "Lead_Time_Weeks", 
              "Produktion_M1", "Bestelldatum_M1", 
              "Produktion_M2", "Bestelldatum_M2", 
              "Produktion_M3", "Bestelldatum_M3"]

display_plan = display_plan[ui_columns].rename(
    columns={
        "Lead_Time_Weeks": "Vorlauf (Wochen)",
        "Produktion_M1": f"Produktion {m1_label}",
        "Bestelldatum_M1": f"Order für {m1_label}",
        "Produktion_M2": f"Produktion {m2_label}",
        "Bestelldatum_M2": f"Order für {m2_label}",
        "Produktion_M3": f"Produktion {m3_label}",
        "Bestelldatum_M3": f"Order für {m3_label}"
    }
)

st.dataframe(display_plan, hide_index=True, width='content')

# --- 5. SPEICHERN ---
if st.button("💾 Forecast speichern", type="primary"):
    with st.spinner("Lösche alte Daten dieses Monats und speichere neu..."):
        try:
            # Jetzt auch die target_month_dates übergeben
            save_forecast_to_bq(edited_df, production_plan, target_month_dates)
            st.success("Erfolgreich gespeichert! (Alte Speicherungen von diesem Meeting wurden sauber überschrieben).")
        except Exception as e:
            st.error(f"Fehler beim Speichern: {e}")