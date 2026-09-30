"""Stücklistenauflösung (BOM) und Materialzuordnung offener Produktionsaufträge."""

import pandas as pd
from src.utils import normalize_mat_nr, parse_weclapp_date, to_date


def prepare_cleaned_cogs(df_cogs):
    """
    Bereinigt und typisiert einen COGS/Stücklisten-DataFrame.

    Fügt normalisierte Schlüssel 'base_art' und 'base_mat_nr' hinzu und stellt sicher,
    dass 'quantity', 'procurementLeadDays' und 'articleunitprice' als saubere numerische Werte vorliegen.
    """
    if df_cogs is None or df_cogs.empty:
        return pd.DataFrame()

    cogs = df_cogs.dropna(subset=["productArticleNumber", "materialArticleNumber"]).copy()
    if cogs.empty:
        return pd.DataFrame()

    cogs["base_art"] = cogs["productArticleNumber"].astype(str).str.split("-").str[0].str.strip()
    cogs["base_mat_nr"] = cogs["materialArticleNumber"].apply(normalize_mat_nr)

    if "quantity" in cogs.columns:
        cogs["quantity"] = pd.to_numeric(cogs["quantity"], errors="coerce").fillna(0.0).astype(float)
    else:
        cogs["quantity"] = 0.0

    if "procurementLeadDays" in cogs.columns:
        cogs["procurementLeadDays"] = pd.to_numeric(cogs["procurementLeadDays"], errors="coerce").fillna(0).astype(int)
    else:
        cogs["procurementLeadDays"] = 0

    if "articleunitprice" in cogs.columns:
        cogs["articleunitprice"] = pd.to_numeric(cogs["articleunitprice"], errors="coerce").fillna(0.0).astype(float)
    else:
        cogs["articleunitprice"] = 0.0

    return cogs


def build_bom_index(df_cogs):
    """
    Erstellt einheitliche Lookup-Indizes für Stücklisten (BOM) und Material-Metadaten.

    Returns:
        dict:
            - 'cleaned_cogs': Bereinigter COGS-DataFrame
            - 'bom_by_product': Dict {base_art: [{"base_mat_nr", "materialName", "quantity", "lead_days"}]}
            - 'material_meta': Dict {base_mat_nr: {"materialArticleNumber", "materialName", "unitName", "Lieferant", ...}}
            - 'product_name_map': Dict {base_art: productName}
    """
    cleaned = prepare_cleaned_cogs(df_cogs)
    if cleaned.empty:
        return {
            "cleaned_cogs": pd.DataFrame(),
            "bom_by_product": {},
            "material_meta": {},
            "product_name_map": {},
        }

    bom_by_product = {}
    material_meta = {}
    product_name_map = {}

    for row in cleaned.itertuples(index=False):
        p_base = row.base_art
        m_base = row.base_mat_nr
        m_name = str(getattr(row, "materialName", f"Material {m_base}"))
        unit = str(getattr(row, "unitName", "Stk."))
        lead = int(getattr(row, "procurementLeadDays", 0))
        price = float(getattr(row, "articleunitprice", 0.0))
        qty = float(getattr(row, "quantity", 0.0))

        bom_by_product.setdefault(p_base, []).append({
            "base_mat_nr": m_base,
            "materialName": m_name,
            "quantity": qty,
            "lead_days": lead
        })

        if m_base not in material_meta:
            material_meta[m_base] = {
                "materialArticleNumber": row.materialArticleNumber,
                "materialName": m_name,
                "unitName": unit,
                "Lieferant": str(getattr(row, "Lieferant", "Unbekannt")),
                "procurementLeadDays": lead,
                "articleunitprice": price
            }

        p_name = getattr(row, "productName", None)
        if pd.notna(p_name) and p_base not in product_name_map:
            product_name_map[p_base] = str(p_name)

    return {
        "cleaned_cogs": cleaned,
        "bom_by_product": bom_by_product,
        "material_meta": material_meta,
        "product_name_map": product_name_map,
    }


def resolve_open_production_materials(
    df_open_prod=None,
    df_cogs=None,
    reference_date=None,
    **kwargs
):
    """
    Ermittelt den Materialbedarf aller offenen Produktionsaufträge über Stücklisten (BOM / COGS).

    Args:
        df_open_prod (pd.DataFrame, optional): Offene Produktionsaufträge mit 'articleNumber',
            'targetQuantity', 'targetEndDate' (und optional 'productionOrderId' / 'id').
        df_cogs (pd.DataFrame, optional): Stücklisten und Lieferanteninformationen (COGS).
        reference_date (date-like, optional): Stichtag zur Fristprüfung.

    Returns:
        pd.DataFrame: Konsolidierte Materialbedarfe mit 'productionOrderId', 'productArticleNumber',
            'materialArticleNumber', 'quantity', 'targetEndDate'.
    """
    cols = ["productionOrderId", "productArticleNumber", "materialArticleNumber", "quantity", "targetEndDate"]
    ref_dt = to_date(reference_date)

    if df_open_prod is not None and not df_open_prod.empty and df_cogs is not None and not df_cogs.empty:
        op = df_open_prod.copy()
        op["t_end"] = op["targetEndDate"].apply(parse_weclapp_date)
        if ref_dt is not None:
            op = op[op["t_end"] >= ref_dt]
        op["qty"] = pd.to_numeric(op.get("targetQuantity"), errors="coerce").fillna(0.0)
        op = op[op["qty"] > 0]
        if op.empty:
            return pd.DataFrame(columns=cols)

        op["base_art"] = op["articleNumber"].astype(str).str.split("-").str[0].str.strip()
        p_col = "productionOrderId" if "productionOrderId" in op.columns else ("id" if "id" in op.columns else None)
        op["p_id"] = op[p_col].astype(str) if p_col else [f"OPEN_{i}" for i in range(len(op))]

        cogs = prepare_cleaned_cogs(df_cogs)
        if cogs.empty:
            return pd.DataFrame(columns=cols)

        merged = pd.merge(op, cogs, on="base_art", how="inner")
        if merged.empty:
            return pd.DataFrame(columns=cols)

        return pd.DataFrame({
            "productionOrderId": merged["p_id"],
            "productArticleNumber": merged["articleNumber"],
            "materialArticleNumber": merged["base_mat_nr"],
            "quantity": merged["qty"] * merged["quantity"],
            "targetEndDate": merged["t_end"]
        })[cols]

    # Fallback für Legacy-Tests mit expliziter Bedarfsübergabe
    df_legacy = kwargs.get("df_open_prod_mat")
    if df_legacy is not None and not df_legacy.empty:
        records = []
        for row in df_legacy.itertuples(index=False):
            t_end = parse_weclapp_date(getattr(row, "targetEndDate", None))
            if t_end is None or (ref_dt is not None and t_end < ref_dt):
                continue
            raw_m = getattr(row, "materialArticleNumber", None)
            qty = float(getattr(row, "quantity", 0.0))
            if pd.notna(raw_m) and qty > 0:
                records.append({
                    "productionOrderId": str(getattr(row, "productionOrderId", "")),
                    "productArticleNumber": str(getattr(row, "productArticleNumber", "")),
                    "materialArticleNumber": normalize_mat_nr(raw_m),
                    "quantity": qty,
                    "targetEndDate": t_end
                })
        return pd.DataFrame(records, columns=cols) if records else pd.DataFrame(columns=cols)

    return pd.DataFrame(columns=cols)
