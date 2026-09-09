import pandas as pd
import numpy as np

def generate_system_forecast(inventory_df, history_df):
    """Generiert einen 3-Monats-Forecast basierend auf den echten historischen Daten."""
    forecast_data = []
    
    for index, row in inventory_df.iterrows():
        artikel_nr = row["Artikelnummer"]
        
        # Historie für diesen spezifischen Artikel filtern
        art_history = history_df[history_df["Artikelnummer"] == artikel_nr].copy()
        
        # Falls wir keine Historie haben, setzen wir den Forecast auf 0
        if art_history.empty:
            avg_sales = 0
        else:
            # Sortieren nach Datum und die letzten 3 Monate nehmen
            art_history = art_history.sort_values(by="Verkaufsmonat", ascending=False)
            last_3_months = art_history.head(3)
            # Durchschnitt berechnen
            avg_sales = int(last_3_months["Verkaufsmenge"].mean())
        
        # Für den Anfang nehmen wir den Durchschnitt für alle 3 zukünftigen Monate an
        forecast_data.append({
            "Artikelnummer": artikel_nr,
            "System_M1": avg_sales,
            "System_M2": avg_sales,
            "System_M3": avg_sales
        })
        
    return pd.DataFrame(forecast_data)