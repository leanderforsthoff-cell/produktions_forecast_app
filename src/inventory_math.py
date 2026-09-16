import pandas as pd
import math
from src.utils import calculate_order_deadline

def calculate_production_needs(df_plan, target_months, constraints_dict):
    """
    Berechnet Produktionsmengen und Bestelldaten.
    Nutzt nun das dynamische constraints_dict aus der Benutzeroberfläche.
    """
    results = []
    m1_date, m2_date, m3_date = target_months
    
    for _, row in df_plan.iterrows():
        art_nr = row["Artikelnummer"]
        
        # Dynamische Constraints abrufen (mit Fallback, falls was fehlt)
        rules = constraints_dict.get(art_nr, {"MOQ": 1000, "Mindestbestand": 0, "Vorlaufzeit_Wochen": 4})
        moq = rules["MOQ"]
        safety = rules["Mindestbestand"]
        lead_time = rules["Vorlaufzeit_Wochen"]
        
        bestand_start = row["Aktueller_Bestand"]
        
        # --- MONAT 1 ---
        required_m1 = max(0, row["Manuell_M1"] + safety - bestand_start)
        prod_m1 = math.ceil(required_m1 / moq) * moq if required_m1 > 0 else 0
        bestand_ende_m1 = bestand_start + prod_m1 - row["Manuell_M1"]
        order_m1 = calculate_order_deadline(m1_date, lead_time, unit='weeks') if prod_m1 > 0 else None
        
        # --- MONAT 2 ---
        required_m2 = max(0, row["Manuell_M2"] + safety - bestand_ende_m1)
        prod_m2 = math.ceil(required_m2 / moq) * moq if required_m2 > 0 else 0
        bestand_ende_m2 = bestand_ende_m1 + prod_m2 - row["Manuell_M2"]
        order_m2 = calculate_order_deadline(m2_date, lead_time, unit='weeks') if prod_m2 > 0 else None
        
        # --- MONAT 3 ---
        required_m3 = max(0, row["Manuell_M3"] + safety - bestand_ende_m2)
        prod_m3 = math.ceil(required_m3 / moq) * moq if required_m3 > 0 else 0
        order_m3 = calculate_order_deadline(m3_date, lead_time, unit='weeks') if prod_m3 > 0 else None
        
        results.append({
            "Artikelnummer": row["Artikelnummer"],
            "Artikelname": row["Artikelname"],
            "MOQ": moq,
            "Safety_Stock": safety,
            "Lead_Time_Weeks": lead_time,
            
            "Produktion_M1": prod_m1,
            "Bestelldatum_M1": order_m1,
            "Produktion_M2": prod_m2,
            "Bestelldatum_M2": order_m2,
            "Produktion_M3": prod_m3,
            "Bestelldatum_M3": order_m3
        })
        
    return pd.DataFrame(results)