import numpy as np
import pandas as pd
from src.utils import calculate_order_deadline


def calculate_material_requirements(production_plan, df_cogs, df_mat_stock):
    """
    Vektorisierte Berechnung des Materialbedarfs (MRP).
    Verbindet Produktionsplan und Stücklisten (COGS) über pd.merge()
    und teilt Lagerbestände chronologisch über kumulierte Summen zu.
    """
    if production_plan.empty or df_cogs.empty:
        return pd.DataFrame(), pd.DataFrame()

    # 1. Dynamisches Entpacken aller Produktionsmonate in Long-Format
    prod_cols = [c for c in production_plan.columns if c.startswith("Produktion_M")]
    if not prod_cols:
        return pd.DataFrame(), pd.DataFrame()

    month_dfs = []
    for c in prod_cols:
        m_suffix = c[len("Produktion_"):]
        date_col = f"Bestelldatum_{m_suffix}"
        if date_col in production_plan.columns:
            sub = production_plan[["Artikelnummer", "Artikelname", c, date_col]].copy()
            sub.columns = ["Artikelnummer", "Produkt", "Produktion_Menge", "Bedarfs_Datum"]
            month_dfs.append(sub)

    if not month_dfs:
        return pd.DataFrame(), pd.DataFrame()

    df_prod_long = pd.concat(month_dfs, ignore_index=True)
    df_prod_long = df_prod_long[
        (df_prod_long["Produktion_Menge"] > 0) & (df_prod_long["Bedarfs_Datum"].notna())
    ].copy()

    if df_prod_long.empty:
        return pd.DataFrame(), pd.DataFrame()

    # 2. Vektorisierter Join mit Stücklisten (BOM / COGS) über pd.merge()
    df_prod_long["base_product_nr"] = (
        df_prod_long["Artikelnummer"].astype(str).str.split("-").str[0]
    )
    df_cogs_clean = df_cogs.copy()
    df_cogs_clean["base_product_nr"] = (
        df_cogs_clean["productArticleNumber"].astype(str).str.split("-").str[0]
    )

    df_demands = pd.merge(
        df_prod_long,
        df_cogs_clean,
        on="base_product_nr",
        how="inner"
    )

    if df_demands.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Bruttobedarf und Kennzahlen berechnen
    df_demands["Brutto_Bedarf"] = df_demands["Produktion_Menge"] * df_demands["quantity"]
    df_demands["Material_Nr"] = df_demands["materialArticleNumber"]
    df_demands["Material_Name"] = df_demands["materialName"]
    df_demands["Einheit"] = df_demands["unitName"]
    df_demands["Lead_Time_Days"] = df_demands["procurementLeadDays"]
    if "articleunitprice" in df_demands.columns:
        df_demands["Einzelpreis"] = df_demands["articleunitprice"].fillna(0.0).astype(float)
    else:
        df_demands["Einzelpreis"] = 0.0

    # Chronologisch sortieren (entscheidend für geteilte Rohstoffe)
    df_demands = df_demands.sort_values(by="Bedarfs_Datum", kind="stable").reset_index(drop=True)

    # 3. Lagerbestände zuordnen & Netto-Bedarf berechnen
    stock_dict = {}
    if not df_mat_stock.empty:
        stock_dict = df_mat_stock.set_index("materialArticleNumber")["Aktueller_Materialbestand"].to_dict()

    # Vektorisierte Bestandszuteilung mittels kumulierter Summen je Rohstoff
    mat_initial_stock = df_demands["Material_Nr"].map(stock_dict).fillna(0).astype(float)
    cum_demand = df_demands.groupby("Material_Nr")["Brutto_Bedarf"].cumsum()
    prev_cum_demand = cum_demand - df_demands["Brutto_Bedarf"]
    stock_before = (mat_initial_stock - prev_cum_demand).clip(lower=0)

    aus_lager = np.minimum(df_demands["Brutto_Bedarf"], stock_before)
    net_bedarf = df_demands["Brutto_Bedarf"] - aus_lager

    # Detail-Protokoll erstellen
    df_details = pd.DataFrame({
        "Produktion_Am": df_demands["Bedarfs_Datum"],
        "Produkt": df_demands["Produkt"],
        "Material": df_demands["Material_Name"],
        "Bedarf_Gesamt": df_demands["Brutto_Bedarf"],
        "Davon_Aus_Lager": aus_lager,
        "Fehlmenge_Bestellen": net_bedarf,
        "Einheit": df_demands["Einheit"]
    })
    if not df_details.empty:
        df_details = df_details.sort_values(by=["Produktion_Am", "Produkt"]).reset_index(drop=True)

    # 4. Bestellungen ermitteln & gruppieren
    has_order = net_bedarf > 0
    if not has_order.any():
        return pd.DataFrame(), df_details

    df_orders_raw = df_demands[has_order].copy()
    raw_net = net_bedarf[has_order]
    df_orders_raw["Bestellmenge"] = raw_net.round(2)

    safe_lead_times = df_orders_raw["Lead_Time_Days"].fillna(0).astype(int)
    safe_prices = df_orders_raw["Einzelpreis"].fillna(0.0).astype(float)
    df_orders_raw["Vorlaufzeit_Tage"] = safe_lead_times
    df_orders_raw["Einzelpreis"] = safe_prices
    df_orders_raw["Gesamtpreis"] = (raw_net * safe_prices).round(2)

    df_orders_raw["Spätestes_Bestelldatum"] = [
        calculate_order_deadline(d, lt, unit='days')
        for d, lt in zip(df_orders_raw["Bedarfs_Datum"], safe_lead_times)
    ]
    df_orders_raw["Für_Produktion_Am"] = df_orders_raw["Bedarfs_Datum"]
    df_orders_raw["Benötigt_Für_Produkt"] = df_orders_raw["Produkt"]

    # Gruppieren der Bestellungen für die Lieferanten
    df_orders = df_orders_raw.groupby(
        ["Lieferant", "Spätestes_Bestelldatum", "Material_Nr", "Material_Name", "Einheit", "Einzelpreis", "Vorlaufzeit_Tage", "Für_Produktion_Am"],
        dropna=False
    ).agg({
        "Bestellmenge": "sum",
        "Gesamtpreis": "sum",
        "Benötigt_Für_Produkt": lambda x: ", ".join(dict.fromkeys(str(item) for item in x if pd.notna(item)))
    }).reset_index().sort_values(by=["Lieferant", "Spätestes_Bestelldatum"]).reset_index(drop=True)

    return df_orders, df_details
