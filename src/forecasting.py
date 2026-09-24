import pandas as pd

def generate_system_forecast(inventory_df, history_df, target_months):
    if not target_months:
        return pd.DataFrame()

    # 1. Ziel-Daten einmalig parsen
    parsed_target_dates = [pd.to_datetime(m) for m in target_months]
    m1_date = parsed_target_dates[0]
    max_date = m1_date - pd.DateOffset(months=1)

    # 2. Historie EINMALIG vorbereiten und als schnelles Dictionary/MultiIndex anlegen
    if not history_df.empty:
        history_df = history_df.copy()
        history_df["Verkaufsmonat"] = pd.to_datetime(history_df["Verkaufsmonat"])
        # Wir summieren die Mengen pro Artikel und Monat und speichern sie direkt ab
        hist_grouped = history_df.groupby(["Artikelnummer", "Verkaufsmonat"])["Verkaufsmenge"].sum()
    else:
        hist_grouped = pd.Series(dtype=float)

    forecast_data = []

    # 3. Iteration über Bestand
    for artikel_nr in inventory_df["Artikelnummer"]:
        month_vals = {f"System_M{i}": 0 for i in range(1, len(parsed_target_dates) + 1)}
        
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

            for i, target_date in enumerate(parsed_target_dates, start=1):
                month_vals[f"System_M{i}"] = calc_month(target_date)

        record = {"Artikelnummer": artikel_nr}
        record.update(month_vals)
        forecast_data.append(record)
        
    return pd.DataFrame(forecast_data)