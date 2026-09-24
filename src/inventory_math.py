import numpy as np
import pandas as pd
from src.utils import calculate_order_deadline
import datetime

def calculate_production_needs(df_plan, target_months, constraints_dict=None):
    """
    Berechnet Produktionsmengen und Bestelldaten für einen dynamischen Planungshorizont.
    Vollständig vektorisiert ohne row-wise iterrows().
    """
    if df_plan.empty or not target_months:
        cols = ["Artikelnummer", "Artikelname", "MOQ", "Safety_Stock", "Lead_Time_Weeks"]
        for i in range(1, len(target_months) + 1):
            cols.extend([f"Produktion_M{i}", f"Bestelldatum_M{i}"])
        return pd.DataFrame(columns=cols)

    if constraints_dict is None:
        constraints_dict = {}

    res_df = pd.DataFrame()
    res_df["Artikelnummer"] = df_plan["Artikelnummer"].values
    res_df["Artikelname"] = df_plan["Artikelname"].values

    # Mapping der Constraints mit robusten Fallbacks
    get_c = lambda a, key, default: (constraints_dict.get(a) if isinstance(constraints_dict.get(a), dict) else {}).get(key, default)
    res_df["MOQ"] = res_df["Artikelnummer"].map(lambda a: get_c(a, "MOQ", 1000)).fillna(1000).replace(0, 1000).astype(int)
    res_df["Safety_Stock"] = res_df["Artikelnummer"].map(lambda a: get_c(a, "Mindestbestand", 0)).fillna(0).astype(int)
    res_df["Lead_Time_Weeks"] = res_df["Artikelnummer"].map(lambda a: get_c(a, "Vorlaufzeit_Wochen", 4)).fillna(4).astype(int)

    current_stock = df_plan["Aktueller_Bestand"].fillna(0).astype(float).values.copy()
    safety_stock = res_df["Safety_Stock"].values
    moq = res_df["MOQ"].values
    lead_time_weeks = res_df["Lead_Time_Weeks"]

    # Einmalige Berechnung der Deadlines pro eindeutiger Vorlaufzeit
    unique_leads = lead_time_weeks.unique()

    for i, target_date in enumerate(target_months, start=1):
        manuell_col = f"Manuell_M{i}"
        prod_col = f"Produktion_M{i}"
        order_col = f"Bestelldatum_M{i}"

        if manuell_col in df_plan.columns:
            demand = df_plan[manuell_col].fillna(0).astype(float).values
        else:
            demand = np.zeros(len(df_plan))

        # 1. Bestelldatum vorab berechnen
        lead_to_deadline = {lt: calculate_order_deadline(target_date, lt, unit='weeks') for lt in unique_leads}
        deadlines = lead_time_weeks.map(lead_to_deadline).values

        # 2. Prüfen, ob die Deadline bereits in der Vergangenheit liegt (Gefrorene Periode)
        today = datetime.date.today()
        is_frozen = np.array([d is not None and d < today for d in deadlines])

        # Fehlbestand erfassen
        shortage = np.where(is_frozen & (demand > current_stock), demand - current_stock, 0).astype(int)
        res_df[f"Fehlbestand_M{i}"] = shortage

        # 3. Bedarf berechnen: Produktion nur auslösen, wenn NICHT gefroren
        required = np.maximum(0, demand + safety_stock - current_stock)
        prod = np.where((required > 0) & (~is_frozen), np.ceil(required / moq) * moq, 0).astype(int)

        # 4. Bestandsfortschreibung mit Lost Sales (Bestand kann nicht unter 0 fallen)
        current_stock = np.maximum(0.0, current_stock + prod - demand)

        res_df[prod_col] = prod
        res_df[order_col] = np.where(prod > 0, deadlines, None)

    return res_df