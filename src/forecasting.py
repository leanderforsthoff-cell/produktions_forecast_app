import pandas as pd

def generate_system_forecast(inventory_df, history_df, target_months):
    # 1. Ziel-Daten einmalig parsen
    m1_date = pd.to_datetime(target_months[0])
    m2_date = pd.to_datetime(target_months[1])
    m3_date = pd.to_datetime(target_months[2])
    max_date = m1_date - pd.DateOffset(months=1)

    # 2. Historie EINMALIG vorbereiten und als schnelles Dictionary/MultiIndex anlegen
    if not history_df.empty:
        history_df["Verkaufsmonat"] = pd.to_datetime(history_df["Verkaufsmonat"])
        # Wir summieren die Mengen pro Artikel und Monat und speichern sie direkt ab
        hist_grouped = history_df.groupby(["Artikelnummer", "Verkaufsmonat"])["Verkaufsmenge"].sum()
    else:
        hist_grouped = pd.Series(dtype=float)

    forecast_data = []

    # 3. Iteration über Bestand (jetzt viel schneller, da nur noch O(1) Lookups passieren)
    for artikel_nr in inventory_df["Artikelnummer"]:
        m1, m2, m3 = 0, 0, 0
        
        # Prüfen, ob der Artikel überhaupt in der Historie existiert
        if not hist_grouped.empty and artikel_nr in hist_grouped.index.get_level_values(0):
            art_series = hist_grouped[artikel_nr]
            min_date = art_series.index.min()
            
            # Reindex für eine lückenlose Zeitreihe (füllt Monate ohne Verkauf mit 0)
            all_months = pd.date_range(start=min_date, end=max_date, freq='MS')
            series = art_series.reindex(all_months, fill_value=0)
            
            avg_3m = int(series.tail(3).mean()) if len(series) >= 3 else 0
            
            # Helper-Funktion für die Berechnung
            def calc_month(target_date):
                last_year_date = target_date - pd.DateOffset(years=1)
                if last_year_date >= min_date:
                    val_last_year = series.get(last_year_date, 0)
                    return int((val_last_year * 0.6) + (avg_3m * 0.4))
                return avg_3m

            m1 = calc_month(m1_date)
            m2 = calc_month(m2_date)
            m3 = calc_month(m3_date)

        forecast_data.append({
            "Artikelnummer": artikel_nr,
            "System_M1": m1,
            "System_M2": m2,
            "System_M3": m3
        })
        
    return pd.DataFrame(forecast_data)