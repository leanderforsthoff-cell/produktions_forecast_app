import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

import streamlit as st
import pandas as pd
from src.data_integration import fetch_current_inventory, fetch_historical_sales, save_forecast_to_bq, fetch_available_articles, fetch_bom_for_articles
from src.forecasting import generate_system_forecast
from src.inventory_math import calculate_production_needs
import datetime

st.set_page_config(page_title="Produktions-Forecast", layout="wide")
st.title("📦 Produktions-Forecast & Planung")

# --- 1. DATEN FÜR SIDEBAR LADEN ---
@st.cache_data
def load_article_master():
    return fetch_available_articles()

df_articles = load_article_master()

# --- 2. SIDEBAR (UI FÜR DIE AUSWAHL) ---
st.sidebar.header("⚙️ Einstellungen")
st.sidebar.write("Wähle die Artikel für dieses Meeting:")

# Vorauswahl
TARGET_ARTICLES = [
    "10024-C", 
    "10025-C",
    "10026-C",
    "10027-C",
    "10028-C",
    "10029-C",
    "10030-C",
    "10031-C",
    "10020-C"
]

# Wir suchen aus allen geladenen Artikeln genau die heraus, 
# die in unserer TARGET_ARTICLES Liste stehen, um sie als Vorauswahl zu setzen.
default_selection = df_articles[df_articles["Artikelnummer"].isin(TARGET_ARTICLES)]["Anzeige_Name"].tolist()

# Multiselect-Dropdown mit der perfekten Vorauswahl
selected_display_names = st.sidebar.multiselect(
    "Artikel auswählen",
    options=df_articles["Anzeige_Name"].tolist(),
    default=default_selection 
)

# Umwandlung zurück in Artikelnummern
selected_article_numbers = df_articles[df_articles["Anzeige_Name"].isin(selected_display_names)]["Artikelnummer"].tolist()
# --- 3. MONATE & DATEN-LADEN ---
def get_target_months():
    heute = datetime.date.today()
    m1 = (heute.replace(day=1) + datetime.timedelta(days=32)).replace(day=1)
    m2 = (m1 + datetime.timedelta(days=32)).replace(day=1)
    m3 = (m2 + datetime.timedelta(days=32)).replace(day=1)
    return [m1, m2, m3]

target_month_dates = get_target_months()

monate_de = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
m1_label = f"{monate_de[target_month_dates[0].month - 1]} {target_month_dates[0].year}"
m2_label = f"{monate_de[target_month_dates[1].month - 1]} {target_month_dates[1].year}"
m3_label = f"{monate_de[target_month_dates[2].month - 1]} {target_month_dates[2].year}"

@st.cache_data
def load_and_prepare_data(target_months, article_list):
    # Wenn keine Artikel ausgewählt sind, leeren DataFrame zurückgeben
    if not article_list:
        return pd.DataFrame()
        
    inventory = fetch_current_inventory(article_list)
    history = fetch_historical_sales(article_list)
    
    forecast = generate_system_forecast(inventory, history, target_months)
    df_merged = pd.merge(inventory, forecast, on="Artikelnummer", how="left")
    
    df_merged["Manuell_M1"] = df_merged["System_M1"].fillna(0).astype(int)
    df_merged["Manuell_M2"] = df_merged["System_M2"].fillna(0).astype(int)
    df_merged["Manuell_M3"] = df_merged["System_M3"].fillna(0).astype(int)
    
    return df_merged

# State-Management: 
# Wir merken uns, welche Artikelnummern zuletzt geladen wurden. 
# Ändert sich die Auswahl, holen wir frische Daten aus BigQuery.
if "last_selection" not in st.session_state or st.session_state.last_selection != selected_article_numbers:
    st.session_state.plan_data = load_and_prepare_data(target_month_dates, selected_article_numbers)
    st.session_state.last_selection = selected_article_numbers

# Wenn gar nichts ausgewählt wurde, brechen wir hier höflich ab
if not selected_article_numbers:
    st.warning("👈 Bitte wähle links in der Seitenleiste mindestens einen Artikel aus, um mit der Planung zu beginnen.")
    st.stop()

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

# --- 6. ENTWICKLER-ANSICHT: STÜCKLISTEN (BOM) ---
st.divider()
st.header("🛠️ Entwickler-Bereich: Bestandteile (COGS)")
st.write("Live-Abruf der Stücklisten für die aktuell ausgewählten Getränke.")

with st.spinner("Lade Stücklisten aus pollymain.mart.COGS..."):
    try:
        # 1. Alle benötigten COGS-Daten auf einmal laden (gut für die Performance)
        df_cogs = fetch_bom_for_articles(selected_article_numbers)
        
        if not df_cogs.empty:
            # Hilfsspalte: Da productArticleNumber aus BQ als Zahl kommt, 
            # wandeln wir sie hier sicher in einen String ohne Kommastellen um.
            df_cogs["match_number"] = df_cogs["productArticleNumber"].astype(str).str.replace(r'\.0$', '', regex=True)
            
            # 2. Für jeden ausgewählten Artikel einen eigenen Expander bauen
            # Wir nutzen zip(), um gleichzeitig den schönen Namen und die Artikelnummer zu haben
            for display_name, art_nr in zip(selected_display_names, selected_article_numbers):
                
                # Wieder die Basisnummer berechnen (z.B. "10024-C" -> "10024")
                base_nr = str(art_nr).split('-')[0]
                
                # Tabelle filtern
                df_art_cogs = df_cogs[df_cogs["match_number"] == base_nr]
                
                # Eigenen ausklappbaren Bereich erstellen
                with st.expander(f"📦 Stückliste: {display_name}"):
                    if not df_art_cogs.empty:
                        # Die Hilfsspalte blenden wir für die Anzeige wieder aus
                        st.dataframe(df_art_cogs.drop(columns=["match_number"]), width='stretch')
                        st.caption(f"{len(df_art_cogs)} Bestandteile für dieses Produkt gefunden.")
                    else:
                        st.info("Für dieses Produkt wurden keine Bestandteile in der COGS-Tabelle gefunden.")
        else:
            st.warning("Es wurden generell keine COGS-Daten für die aktuelle Auswahl gefunden.")
            
    except Exception as e:
        st.error(f"Fehler beim Laden der COGS-Tabelle: {e}")