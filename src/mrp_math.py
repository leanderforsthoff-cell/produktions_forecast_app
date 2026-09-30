"""Materialbedarfsplanung (MRP), Stücklistenauflösung und monatliche Cashbedarfsberechnung."""

import datetime
import numpy as np
import pandas as pd
from src.utils import (
    calculate_order_deadline,
    monate_de,
    normalize_mat_nr,
    to_date,
)
from src.supply_tracker import MaterialSupplyTracker
from src.bom import resolve_open_production_materials, build_bom_index


def calculate_material_requirements(
    production_plan,
    df_cogs,
    df_mat_stock,
    df_open_po=None,
    df_open_prod_mat=None,
    reference_date=None,
    df_open_prod=None
):
    """
    Führt eine Materialbedarfsplanung (MRP) mit Stücklistenauflösung und chronologischem Netting durch.

    Löst den Produktionsplan über Stücklisten (COGS) in Bruttobedarfe auf, bezieht Materialbedarfe
    bereits offener Produktionen sowie ankommende Lieferungen offener Bestellungen (Purchase Orders)
    terminbezogen ein, weist Lagerbestände chronologisch zu und ermittelt Netto-Bestellungen
    je Lieferant und Bestelltermin sowie ein detailliertes Verwendungs-Protokoll.

    Args:
        production_plan (pd.DataFrame): Produktionsplan mit Spalten 'Produktion_M{i}'
            und 'Bestelldatum_M{i}'.
        df_cogs (pd.DataFrame): Stücklisten und Lieferanteninformationen (COGS).
        df_mat_stock (pd.DataFrame): Rohstoffbestände ('materialArticleNumber', 'Aktueller_Materialbestand').
        df_open_po (pd.DataFrame, optional): Offene Materialbestellungen.
        df_open_prod_mat (pd.DataFrame, optional): Materialbedarfe offener Produktionen (Legacy).
        reference_date (date-like, optional): Stichtag zur Fristprüfung.
        df_open_prod (pd.DataFrame, optional): Offene Produktionsaufträge.

    Returns:
        tuple[pd.DataFrame, pd.DataFrame]:
            - df_orders: Gruppierte, rechtzeitig ausführbare Materialbestellungen je Lieferant und Stichtag.
            - df_details: Detaillierte Zuteilung (Brutto, Lager, Netto) nur für neu geplante Fertigprodukte.
    """
    if df_cogs is None or df_cogs.empty:
        return pd.DataFrame(), pd.DataFrame()

    ref_dt = to_date(reference_date)

    resolved_open_mat = resolve_open_production_materials(
        df_open_prod=df_open_prod,
        df_cogs=df_cogs,
        reference_date=ref_dt,
        df_open_prod_mat=df_open_prod_mat
    )
    has_open_prod_mat = not resolved_open_mat.empty

    # 1. Dynamisches Entpacken aller Produktionsmonate ins Long-Format
    prod_cols = [c for c in production_plan.columns if c.startswith("Produktion_M")] if not production_plan.empty else []
    month_dfs = []
    for c in prod_cols:
        date_col = f"Bestelldatum_{c[len('Produktion_'):]}"
        if date_col in production_plan.columns:
            sub = production_plan[["Artikelnummer", "Artikelname", c, date_col]].copy()
            sub.columns = ["Artikelnummer", "Produkt", "Produktion_Menge", "Bedarfs_Datum"]
            month_dfs.append(sub)

    df_prod_long = pd.concat(month_dfs, ignore_index=True) if month_dfs else pd.DataFrame()
    if not df_prod_long.empty:
        df_prod_long = df_prod_long[
            (df_prod_long["Produktion_Menge"] > 0) & (df_prod_long["Bedarfs_Datum"].notna())
        ].copy()

    if df_prod_long.empty and not has_open_prod_mat:
        return pd.DataFrame(), pd.DataFrame()

    # 2. Vorbereitung der Stücklisten (COGS) und Metadaten über zentralen BOM-Index
    bom_idx = build_bom_index(df_cogs)
    cogs = bom_idx["cleaned_cogs"]
    mat_meta = bom_idx["material_meta"]

    # 3. Vektorisierter Join neuer Produktionen mit Stücklisten
    demand_dfs = []
    if not df_prod_long.empty:
        df_prod_long["base_art"] = df_prod_long["Artikelnummer"].astype(str).str.split("-").str[0].str.strip()
        merged = pd.merge(df_prod_long, cogs, on="base_art", how="inner")
        if not merged.empty:
            merged["Brutto_Bedarf"] = merged["Produktion_Menge"] * merged["quantity"]
            merged["Material_Nr"] = merged["materialArticleNumber"]
            merged["Material_Name"] = merged["materialName"]
            merged["Einheit"] = merged["unitName"]
            merged["Vorlaufzeit_Tage"] = merged["procurementLeadDays"]
            merged["Einzelpreis"] = merged["articleunitprice"]
            merged["Is_Open_Order"] = False
            demand_dfs.append(merged)

    # 4. Offene Produktionsbedarfe als Vorweg-Abzug hinzufügen
    if has_open_prod_mat:
        open_demands = []
        for row in resolved_open_mat.itertuples(index=False):
            base_m = row.materialArticleNumber
            if base_m in mat_meta:
                meta = mat_meta[base_m]
                t_end = to_date(getattr(row, "targetEndDate", None))
                if t_end is None:
                    continue
                prod_art = getattr(row, "productArticleNumber", "")
                raw_qty = getattr(row, "quantity", 0.0)
                qty = float(raw_qty) if pd.notna(raw_qty) else 0.0
                open_demands.append({
                    "Artikelnummer": str(prod_art),
                    "Produkt": f"Offener Auftrag ({prod_art})",
                    "Produktion_Menge": 0.0,
                    # POs werden am Liefertag + 1 Tag gebucht; Materialverbrauch spätestens targetEndDate + 1 Tag
                    "Bedarfs_Datum": t_end + datetime.timedelta(days=1),
                    "Brutto_Bedarf": qty,
                    "Material_Nr": meta["materialArticleNumber"],
                    "Material_Name": meta["materialName"],
                    "Einheit": meta["unitName"],
                    "Lieferant": meta["Lieferant"],
                    "Vorlaufzeit_Tage": meta["procurementLeadDays"],
                    "Einzelpreis": meta["articleunitprice"],
                    "base_mat_nr": base_m,
                    "Is_Open_Order": True
                })
        if open_demands:
            demand_dfs.append(pd.DataFrame(open_demands))

    if not demand_dfs:
        return pd.DataFrame(), pd.DataFrame()

    df_demands = pd.concat(demand_dfs, ignore_index=True)
    df_demands = df_demands.sort_values(by="Bedarfs_Datum", kind="stable").reset_index(drop=True)

    # 5. Chronologische FIFO-Allokation via MaterialSupplyTracker
    tracker = MaterialSupplyTracker(df_mat_stock=df_mat_stock, df_open_po=df_open_po, reference_date=ref_dt)
    aus_lager = []
    netto = []

    for row in df_demands.itertuples(index=False):
        base_m = row.base_mat_nr
        d_date = to_date(row.Bedarfs_Datum)
        gross = float(row.Brutto_Bedarf)
        covered = tracker.allocate(base_m, gross, d_date)
        aus_lager.append(covered)
        netto.append(max(0.0, gross - covered))

    df_demands["Davon_Aus_Lager"] = aus_lager
    df_demands["Netto_Bedarf"] = netto

    # 6. Detail-Protokoll: Nur für neue geplante Produktionen (keine offenen Aufträge)
    df_details_src = df_demands[~df_demands["Is_Open_Order"]] if "Is_Open_Order" in df_demands.columns else df_demands
    df_details = pd.DataFrame({
        "Produktion_Am": df_details_src["Bedarfs_Datum"],
        "Produkt": df_details_src["Produkt"],
        "Material": df_details_src["Material_Name"],
        "Bedarf_Gesamt": df_details_src["Brutto_Bedarf"],
        "Davon_Aus_Lager": df_details_src["Davon_Aus_Lager"],
        "Fehlmenge_Bestellen": df_details_src["Netto_Bedarf"],
        "Einheit": df_details_src["Einheit"]
    })
    if not df_details.empty:
        df_details = df_details.sort_values(by=["Produktion_Am", "Produkt"]).reset_index(drop=True)

    # 7. Bestellungen ermitteln & gruppieren (nur für neu geplante Bedarfe)
    has_order = (df_demands["Netto_Bedarf"] > 0) & (~df_demands.get("Is_Open_Order", False))
    if not has_order.any():
        return pd.DataFrame(), df_details

    df_orders_raw = df_demands[has_order].copy()
    raw_net = df_orders_raw["Netto_Bedarf"].round(2)
    df_orders_raw["Bestellmenge"] = raw_net
    df_orders_raw["Gesamtpreis"] = (raw_net * df_orders_raw["Einzelpreis"]).round(2)
    df_orders_raw["Für_Produktion_Am"] = df_orders_raw["Bedarfs_Datum"]
    df_orders_raw["Benötigt_Für_Produkt"] = df_orders_raw["Produkt"]

    df_orders_raw["Spätestes_Bestelldatum"] = [
        calculate_order_deadline(d, lt, unit='days')
        for d, lt in zip(df_orders_raw["Bedarfs_Datum"], df_orders_raw["Vorlaufzeit_Tage"])
    ]

    # Nur rechtzeitig bestellbare Positionen berücksichtigen (abgelaufene Fristen sind Lost Sales)
    if ref_dt is not None:
        df_orders_raw = df_orders_raw[
            df_orders_raw["Spätestes_Bestelldatum"].apply(lambda d: d is not None and to_date(d) >= ref_dt)
        ]
        if df_orders_raw.empty:
            return pd.DataFrame(), df_details

    group_cols = [
        "Lieferant", "Spätestes_Bestelldatum", "Material_Nr", "Material_Name",
        "Einheit", "Einzelpreis", "Vorlaufzeit_Tage", "Für_Produktion_Am"
    ]
    df_orders = df_orders_raw.groupby(group_cols, dropna=False).agg({
        "Bestellmenge": "sum",
        "Gesamtpreis": "sum",
        "Benötigt_Für_Produkt": lambda x: ", ".join(dict.fromkeys(str(item) for item in x if pd.notna(item)))
    }).reset_index().sort_values(by=["Lieferant", "Spätestes_Bestelldatum"]).reset_index(drop=True)

    return df_orders, df_details


def calculate_cash_needs(df_orders, reference_date=None):
    """
    Berechnet den aggregierten Liquiditätsbedarf (Cashbedarf) je Bestellmonat.

    Summiert das Bestellvolumen auf Basis des spätesten Bestelldatums. Positionen,
    deren Frist vor dem Stichtag liegt (abgelaufene Bestellfrist / Lost Sales),
    werden nicht berücksichtigt.

    Args:
        df_orders (pd.DataFrame): Bestellpositionen mit 'Spätestes_Bestelldatum' und 'Gesamtpreis'.
        reference_date (date-like, optional): Stichtag zur Fristprüfung. Standard ist heute.

    Returns:
        pd.DataFrame: Aggregierter Cashbedarf mit 'Bestellmonat' und 'Cashbedarf (€)'.
    """
    cols = ["Bestellmonat", "Cashbedarf (€)"]
    if (
        df_orders is None 
        or df_orders.empty 
        or "Spätestes_Bestelldatum" not in df_orders.columns 
        or "Gesamtpreis" not in df_orders.columns
    ):
        return pd.DataFrame(columns=cols)

    ref_date = to_date(reference_date, default=datetime.date.today())

    df = df_orders.copy()
    df["dt"] = pd.to_datetime(df["Spätestes_Bestelldatum"], errors="coerce")
    df = df.dropna(subset=["dt"])
    if df.empty:
        return pd.DataFrame(columns=cols)

    df = df[df["dt"].dt.date >= ref_date]
    if df.empty:
        return pd.DataFrame(columns=cols)

    df["Monat_Period"] = df["dt"].dt.to_period("M")
    cash_df = df.groupby("Monat_Period")["Gesamtpreis"].sum().reset_index().sort_values(by="Monat_Period").reset_index(drop=True)

    cash_df["Bestellmonat"] = cash_df["Monat_Period"].apply(lambda p: f"{monate_de[p.month - 1]} {p.year}")
    cash_df["Cashbedarf (€)"] = cash_df["Gesamtpreis"].round(2)

    return cash_df[cols]
