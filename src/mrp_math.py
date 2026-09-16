import pandas as pd
import datetime

def get_material_order_date(target_date, lead_time_days):
    """Berechnet das Bestelldatum für Material (zum 15. des Monats)."""
    if pd.isnull(target_date):
        return None
    
    # Wenn target_date als String kommt, in Date umwandeln
    if isinstance(target_date, str):
        target_date = datetime.datetime.strptime(target_date, "%d.%m.%Y").date()
    elif isinstance(target_date, datetime.datetime):
        target_date = target_date.date()
        
    # --- FEHLERBEHEBUNG: Leere Vorlaufzeiten (NULL / NA) abfangen ---
    if pd.isna(lead_time_days):
        safe_lead_time = 0  # Wenn nichts eingetragen ist, nehmen wir 0 Tage an
    else:
        safe_lead_time = int(lead_time_days)
        
    # Jetzt mit der sicheren Zahl rechnen
    deadline = target_date - datetime.timedelta(days=safe_lead_time)
    
    if deadline.day >= 15:
        return deadline.replace(day=15)
    else:
        # Zum 15. des Vormonats springen
        prev_month_end = deadline.replace(day=1) - datetime.timedelta(days=1)
        return prev_month_end.replace(day=15)

def calculate_material_requirements(production_plan, df_cogs, df_mat_stock):
    """
    Berechnet, wann welches Material bei welchem Lieferanten bestellt werden muss.
    """
    if production_plan.empty or df_cogs.empty:
        return pd.DataFrame()

    # 1. Bestands-Wörterbuch aufbauen (Für schnelles Abziehen)
    # {materialArticleNumber: aktueller_bestand}
    stock_dict = {}
    if not df_mat_stock.empty:
        stock_dict = df_mat_stock.set_index("materialArticleNumber")["Aktueller_Materialbestand"].to_dict()

    # 2. Alle Brutto-Bedarfe (Gross Requirements) sammeln
    # Wir müssen sie chronologisch sammeln, damit wir den Bestand in der richtigen Reihenfolge abziehen.
    demands = []
    
    for _, prod_row in production_plan.iterrows():
        base_nr = str(prod_row["Artikelnummer"]).split("-")[0]
        # Stückliste für dieses Produkt
        bom = df_cogs[df_cogs["productArticleNumber"].astype(str) == base_nr]
        
        for m in [1, 2, 3]:
            prod_qty = prod_row[f"Produktion_M{m}"]
            prod_order_date = prod_row[f"Bestelldatum_M{m}"] # Das ist das Datum, an dem Material DA SEIN MUSS
            
            if prod_qty > 0 and pd.notnull(prod_order_date):
                for _, bom_row in bom.iterrows():
                    mat_nr = bom_row["materialArticleNumber"]
                    req_qty = prod_qty * bom_row["quantity"]
                    
                    demands.append({
                        "Produkt": prod_row["Artikelname"],
                        "Material_Nr": mat_nr,
                        "Material_Name": bom_row["materialName"],
                        "Einheit": bom_row["unitName"],
                        "Lieferant": bom_row["Lieferant"],
                        "Lead_Time_Days": bom_row["procurementLeadDays"],
                        "Bedarfs_Datum": prod_order_date, # Datum des Produktionsstarts
                        "Brutto_Bedarf": req_qty
                    })
    
    df_demands = pd.DataFrame(demands)
    if df_demands.empty:
        return pd.DataFrame()

    # 3. Chronologisch sortieren (Wir verbrauchen Bestand für die frühesten Bedarfe zuerst)
    df_demands = df_demands.sort_values(by="Bedarfs_Datum")
    
    # 4. Netto-Bedarf (Net Requirements) berechnen und Bestand abziehen
    orders = []
    for _, row in df_demands.iterrows():
        mat_nr = row["Material_Nr"]
        current_stock = stock_dict.get(mat_nr, 0)
        
        # Bestand reicht komplett
        if current_stock >= row["Brutto_Bedarf"]:
            stock_dict[mat_nr] -= row["Brutto_Bedarf"]
            net_bedarf = 0
        else:
            # Bestand reicht teilweise oder gar nicht
            net_bedarf = row["Brutto_Bedarf"] - current_stock
            stock_dict[mat_nr] = 0 # Bestand ist aufgebraucht
            
        if net_bedarf > 0:
            order_date = get_material_order_date(row["Bedarfs_Datum"], row["Lead_Time_Days"])
            
            orders.append({
                "Lieferant": row["Lieferant"],
                "Material_Name": row["Material_Name"],
                "Material_Nr": mat_nr,
                "Bestellmenge": round(net_bedarf, 2),
                "Einheit": row["Einheit"],
                "Spätestes_Bestelldatum": order_date,
                "Für_Produktion_Am": row["Bedarfs_Datum"],
                "Benötigt_Für_Produkt": row["Produkt"]
            })
            
    # Wir gruppieren mehrfache Bedarfe, die am SELBEN TAG beim SELBEN LIEFERANTEN für das SELBE MATERIAL anfallen
    df_orders = pd.DataFrame(orders)
    if not df_orders.empty:
        df_orders = df_orders.groupby(
            ["Lieferant", "Spätestes_Bestelldatum", "Material_Nr", "Material_Name", "Einheit", "Für_Produktion_Am"]
        ).agg({
            "Bestellmenge": "sum",
            "Benötigt_Für_Produkt": lambda x: ", ".join(x.unique())
        }).reset_index()
        
        # Sortieren, damit der Einkäufer sieht, was als Nächstes ansteht
        df_orders = df_orders.sort_values(by=["Lieferant", "Spätestes_Bestelldatum"])

    return df_orders