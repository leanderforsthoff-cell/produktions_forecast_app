import pandas as pd

def generate_system_forecast(inventory_df, history_df, target_months):
    forecast_data = []

    for index, row in inventory_df.iterrows():
        artikel_nr = row["Artikelnummer"]
        art_history = history_df[history_df["Artikelnummer"] == artikel_nr].copy()
        
        m1, m2, m3 = 0, 0, 0
        
        if not art_history.empty:
            art_history = art_history.groupby("Verkaufsmonat")["Verkaufsmenge"].sum().reset_index()
            # Sicherstellen, dass Pandas den Index als DateTime erkennt
            art_history["Verkaufsmonat"] = pd.to_datetime(art_history["Verkaufsmonat"])
            art_history = art_history.set_index("Verkaufsmonat")
            
            # Wann wurde dieses Produkt zum allerersten Mal verkauft?
            min_date = art_history.index.min()
            max_date = pd.to_datetime(target_months[0]) - pd.DateOffset(months=1)
            
            all_months = pd.date_range(start=min_date, end=max_date, freq='MS')
            series = art_history.reindex(all_months, fill_value=0)["Verkaufsmenge"]
            
            avg_3m = int(series.tail(3).mean()) if len(series) >= 3 else 0
            
            def calc_month(target_date):
                last_year_date = target_date - pd.DateOffset(years=1)
                
                # Wenn das Produkt vor einem Jahr schon existierte
                if last_year_date >= min_date:
                    # .get() holt den Wert sicher. Wenn in dem spezifischen Monat 
                    # nichts verkauft wurde, wird 0 angenommen.
                    val_last_year = series.get(last_year_date, 0)
                    return int((val_last_year * 0.6) + (avg_3m * 0.4))
                
                # Wenn das Produkt noch kein Jahr alt ist
                return avg_3m

            m1 = calc_month(pd.to_datetime(target_months[0]))
            m2 = calc_month(pd.to_datetime(target_months[1]))
            m3 = calc_month(pd.to_datetime(target_months[2]))

        forecast_data.append({
            "Artikelnummer": artikel_nr,
            "System_M1": m1,
            "System_M2": m2,
            "System_M3": m3
        })
        
    return pd.DataFrame(forecast_data)