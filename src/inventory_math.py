import pandas as pd
import math
import datetime

# --- HARDCODED CONSTRAINTS FÜR POC ---
# Passe die Keys ("12345") an deine echten TARGET_ARTICLES an.
CONSTRAINTS = {
    "10024-C": {"moq": 2000, "safety_stock": 150, "lead_time_weeks": 4}, 
    "10025-C": {"moq": 2000, "safety_stock": 150, "lead_time_weeks": 4},
    "10026-C": {"moq": 2000, "safety_stock": 150, "lead_time_weeks": 4},
    "10027-C": {"moq": 2000, "safety_stock": 150, "lead_time_weeks": 4},
    "10028-C": {"moq": 2000, "safety_stock": 150, "lead_time_weeks": 4},
    "10029-C": {"moq": 2000, "safety_stock": 150, "lead_time_weeks": 4},
    "10030-C": {"moq": 2000, "safety_stock": 150, "lead_time_weeks": 4},
    "10031-C": {"moq": 2000, "safety_stock": 150, "lead_time_weeks": 4},
    "10020-C": {"moq": 2000, "safety_stock": 150, "lead_time_weeks": 4},
    # Fallback, falls ein Artikel nicht in der Liste steht:
    "DEFAULT": {"moq": 2000, "safety_stock": 150, "lead_time_weeks": 4}
}

def get_order_date(target_date, lead_time_weeks):
    """
    Berechnet das späteste Bestelldatum (immer der 15. eines Monats).
    Target_date ist immer der 1. des Zielmonats.
    """
    if target_date is None: 
        return None
        
    # Deadline, an der die Ware spätestens beauftragt sein muss
    deadline = target_date - datetime.timedelta(weeks=lead_time_weeks)
    
    # Liegt die Deadline NACH oder AM 15. des Monats? 
    if deadline.day >= 15:
        # Dann reicht es, am 15. dieses Monats zu bestellen
        return deadline.replace(day=15)
    else:
        # Sonst müssen wir am 15. des VORHERIGEN Monats bestellen
        prev_month_end = deadline.replace(day=1) - datetime.timedelta(days=1)
        return prev_month_end.replace(day=15)

def calculate_production_needs(df_plan, target_months):
    """
    Berechnet Produktionsmengen und exakte Bestelldaten.
    target_months ist eine Liste aus 3 datetime.date Objekten (z.B. 1.Okt, 1.Nov, 1.Dez)
    """
    results = []
    m1_date, m2_date, m3_date = target_months
    
    for _, row in df_plan.iterrows():
        art_nr = row["Artikelnummer"]
        rules = CONSTRAINTS.get(art_nr, CONSTRAINTS["DEFAULT"])
        moq = rules["moq"]
        safety = rules["safety_stock"]
        lead_time = rules["lead_time_weeks"]
        
        bestand_start = row["Aktueller_Bestand"]
        
        # --- MONAT 1 ---
        required_m1 = max(0, row["Manuell_M1"] + safety - bestand_start)
        prod_m1 = math.ceil(required_m1 / moq) * moq if required_m1 > 0 else 0
        bestand_ende_m1 = bestand_start + prod_m1 - row["Manuell_M1"]
        order_m1 = get_order_date(m1_date, lead_time) if prod_m1 > 0 else None
        
        # --- MONAT 2 ---
        required_m2 = max(0, row["Manuell_M2"] + safety - bestand_ende_m1)
        prod_m2 = math.ceil(required_m2 / moq) * moq if required_m2 > 0 else 0
        bestand_ende_m2 = bestand_ende_m1 + prod_m2 - row["Manuell_M2"]
        order_m2 = get_order_date(m2_date, lead_time) if prod_m2 > 0 else None
        
        # --- MONAT 3 ---
        required_m3 = max(0, row["Manuell_M3"] + safety - bestand_ende_m2)
        prod_m3 = math.ceil(required_m3 / moq) * moq if required_m3 > 0 else 0
        order_m3 = get_order_date(m3_date, lead_time) if prod_m3 > 0 else None
        
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