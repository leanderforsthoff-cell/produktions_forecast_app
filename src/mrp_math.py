import pandas as pd
from src.utils import calculate_order_deadline

def calculate_material_requirements(production_plan, df_cogs, df_mat_stock):
    if production_plan.empty or df_cogs.empty:
        return pd.DataFrame(), pd.DataFrame()

    stock_dict = {}
    if not df_mat_stock.empty:
        stock_dict = df_mat_stock.set_index("materialArticleNumber")["Aktueller_Materialbestand"].to_dict()

    demands = []
    
    # 1. Brutto-Bedarf sammeln
    for _, prod_row in production_plan.iterrows():
        base_nr = str(prod_row["Artikelnummer"]).split("-")[0]
        bom = df_cogs[df_cogs["productArticleNumber"].astype(str) == base_nr]
        
        for m in [1, 2, 3]:
            prod_qty = prod_row[f"Produktion_M{m}"]
            prod_order_date = prod_row[f"Bestelldatum_M{m}"]
            
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
                        "Bedarfs_Datum": prod_order_date,
                        "Brutto_Bedarf": req_qty,
                        "Einzelpreis": bom_row["articleunitprice"]
                    })
    
    df_demands = pd.DataFrame(demands)
    if df_demands.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Chronologisch sortieren (Wichtig für gemeinsame Materialien!)
    df_demands = df_demands.sort_values(by="Bedarfs_Datum")
    
    orders = []
    details = [] # NEU: Hier protokollieren wir die Zuteilung mit
    
    # 2. Netto-Bedarf berechnen & Bestand abziehen
    for _, row in df_demands.iterrows():
        mat_nr = row["Material_Nr"]
        current_stock = stock_dict.get(mat_nr, 0)
        brutto = row["Brutto_Bedarf"]
        
        if current_stock >= brutto:
            stock_dict[mat_nr] -= brutto
            aus_lager = brutto
            net_bedarf = 0
        else:
            aus_lager = current_stock
            net_bedarf = brutto - current_stock
            stock_dict[mat_nr] = 0
            
        # Detail-Protokoll schreiben
        details.append({
            "Produktion_Am": row["Bedarfs_Datum"],
            "Produkt": row["Produkt"],
            "Material": row["Material_Name"],
            "Bedarf_Gesamt": brutto,
            "Davon_Aus_Lager": aus_lager,
            "Fehlmenge_Bestellen": net_bedarf,
            "Einheit": row["Einheit"]
        })
            
        if net_bedarf > 0:
            # 1. NEU: Leere Vorlaufzeiten für die Anzeige abfangen
            safe_lead_time = 0 if pd.isna(row["Lead_Time_Days"]) else int(row["Lead_Time_Days"])
            safe_price = 0.0 if pd.isna(row["Einzelpreis"]) else float(row["Einzelpreis"])
            
            order_date = calculate_order_deadline(row["Bedarfs_Datum"], row["Lead_Time_Days"], unit='days')
            orders.append({
                "Lieferant": row["Lieferant"],
                "Material_Name": row["Material_Name"],
                "Material_Nr": mat_nr,
                "Bestellmenge": round(net_bedarf, 2),
                "Einheit": row["Einheit"],
                "Einzelpreis": safe_price,
                "Gesamtpreis": round(net_bedarf * safe_price, 2),
                "Vorlaufzeit_Tage": safe_lead_time, # <--- Hier den sicheren Wert einsetzen
                "Spätestes_Bestelldatum": order_date,
                "Für_Produktion_Am": row["Bedarfs_Datum"],
                "Benötigt_Für_Produkt": row["Produkt"]
            })
            
    # Gruppieren der Bestellungen für die Lieferanten
    df_orders = pd.DataFrame(orders)
    if not df_orders.empty:
        df_orders = df_orders.groupby(
            # NEU: Einzelpreis mit in die Gruppierung aufnehmen
            ["Lieferant", "Spätestes_Bestelldatum", "Material_Nr", "Material_Name", "Einheit", "Einzelpreis", "Vorlaufzeit_Tage", "Für_Produktion_Am"],
            dropna=False
        ).agg({
            "Bestellmenge": "sum",
            "Gesamtpreis": "sum", # <--- NEU: Summiere den Preis auf
            "Benötigt_Für_Produkt": lambda x: ", ".join(x.unique())
        }).reset_index().sort_values(by=["Lieferant", "Spätestes_Bestelldatum"])

    # Details schön formatieren
    df_details = pd.DataFrame(details)
    if not df_details.empty:
        df_details = df_details.sort_values(by=["Produktion_Am", "Produkt"])

    return df_orders, df_details