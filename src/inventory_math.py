"""Nettobedarfs- und Produktionsplanung unter Berücksichtigung von Mindestbeständen, Losgrößen, Vorlaufzeiten und Materialverfügbarkeit."""

import datetime
import numpy as np
import pandas as pd
from src.utils import (
    calculate_order_deadline,
    normalize_mat_nr,
    parse_weclapp_date,
    to_date,
)
from src.supply_tracker import MaterialSupplyTracker
from src.bom import resolve_open_production_materials, build_bom_index  # noqa: F401


def check_open_productions_material_coverage(
    df_open_prod,
    df_cogs,
    df_mat_stock,
    df_open_po=None,
    reference_date=None,
    **kwargs
):
    """
    Prüft für alle offenen Produktionsaufträge, ob die benötigten Materialien rechtzeitig
    durch Lagerbestand und/oder offene Materialbestellungen gedeckt sind.
    """
    ref_dt = to_date(reference_date, default=datetime.date.today())
    if df_open_prod is None or df_open_prod.empty:
        return []

    df_mats = resolve_open_production_materials(
        df_open_prod=df_open_prod,
        df_cogs=df_cogs,
        reference_date=ref_dt
    )
    if df_mats.empty:
        return []

    tracker = MaterialSupplyTracker(df_mat_stock=df_mat_stock, df_open_po=df_open_po, reference_date=ref_dt)

    # Metadaten für Material- und Produktnamen über zentralen BOM-Index laden
    bom_idx = build_bom_index(df_cogs)
    meta_dict = bom_idx["material_meta"]
    prod_name_map = bom_idx["product_name_map"]

    shortages = []
    df_sorted = df_mats.sort_values(by="targetEndDate", kind="stable").reset_index(drop=True)

    for row in df_sorted.itertuples(index=False):
        p_id = row.productionOrderId
        art = row.productArticleNumber
        base_m = row.materialArticleNumber
        req_qty = float(row.quantity)
        t_end = row.targetEndDate

        meta = meta_dict.get(base_m, {})
        m_name = meta.get("materialName", f"Material {base_m}")
        unit = meta.get("unitName", "Stk.")
        p_name = prod_name_map.get(art.split("-")[0].strip(), art)

        timely_avail = tracker.get_available_quantity(base_m, t_end, by_delivery_date=True)

        if timely_avail < req_qty:
            shortage_amt = round(req_qty - timely_avail, 2)
            late_po = tracker.get_earliest_late_supply(base_m, t_end, by_delivery_date=True)
            t_str = t_end.strftime("%d.%m.%Y")
            if late_po:
                d_str = late_po["delivery_date"].strftime("%d.%m.%Y")
                reason = (
                    f"Bestellung ({late_po['qty']:,.0f} {unit}) trifft erst am {d_str} ein "
                    f"(nach Fertigstellungstermin {t_str})"
                )
            else:
                reason = f"Nicht genügend Material auf Lager und keine rechtzeitige Bestellung vor dem {t_str} vorhanden"

            shortages.append({
                "production_order_id": p_id,
                "product_number": art,
                "product_name": p_name,
                "target_end_date": t_end,
                "material_number": base_m,
                "material_name": m_name,
                "unit": unit,
                "required_qty": req_qty,
                "available_in_time": timely_avail,
                "shortage_qty": shortage_amt,
                "reason": reason
            })

        tracker.allocate(base_m, req_qty, t_end, by_delivery_date=True)

    return shortages


def _parse_constraints_dataframe(df_plan, constraints_dict):
    """Liest Constraints (MOQ, Mindestbestand, Vorlaufzeit) mit defensiven Fallbacks aus."""
    c_dict = constraints_dict or {}

    def _parse_val(a, key, default):
        val = c_dict.get(a, {}).get(key) if isinstance(c_dict.get(a), dict) else None
        if pd.isna(val) or val is None:
            return default
        try:
            v = int(val)
            return default if v == 0 and default != 0 else v
        except (ValueError, TypeError):
            return default

    moq = df_plan["Artikelnummer"].map(lambda a: _parse_val(a, "MOQ", 1000)).values
    safety_stock = df_plan["Artikelnummer"].map(lambda a: _parse_val(a, "Mindestbestand", 0)).values
    lead_time_weeks = df_plan["Artikelnummer"].map(lambda a: _parse_val(a, "Vorlaufzeit_Wochen", 4))
    return moq, safety_stock, lead_time_weeks


def _group_open_productions_by_month(df_open_prod, target_months, ref_date):
    """Gruppiert offene Produktionsaufträge nach ihrem effektiven Zugangstermin (t_end + 1 Tag) je Zielmonat."""
    open_prod_by_month = {i: {} for i in range(1, len(target_months) + 1)}
    if df_open_prod is None or df_open_prod.empty:
        return open_prod_by_month

    op = df_open_prod.copy()
    op["dt_end"] = op["targetEndDate"].apply(parse_weclapp_date)
    op = op[op["dt_end"].apply(lambda d: d is not None and d >= ref_date)]
    if op.empty:
        return open_prod_by_month

    op["match_key"] = op["articleNumber"].astype(str).str.split("-").str[0].str.strip()
    op["qty"] = pd.to_numeric(op["targetQuantity"], errors="coerce").fillna(0.0)
    op["avail_date"] = op["dt_end"] + datetime.timedelta(days=1)

    first_tuple = (target_months[0].year, target_months[0].month)
    for i, tm in enumerate(target_months, start=1):
        mask = op["avail_date"].apply(
            lambda d: (d.year, d.month) <= first_tuple if i == 1 else (d.year, d.month) == (tm.year, tm.month)
        )
        sub = op[mask]
        if not sub.empty:
            open_prod_by_month[i] = sub.groupby("match_key")["qty"].sum().to_dict()

    return open_prod_by_month


def _evaluate_candidate_feasibility(candidate_prod, target_date, lt_w, p_base, bom_by_product, tracker, ref_date):
    """
    Prüft zweistufig die zeitliche Machbarkeit einer geplanten Fertigwarenproduktion:
    1. Fertigwaren-Startdeadline (target_date - lt_w Wochen) >= ref_date
    2. Rohstoffbestellfristen aller BOM-Komponenten >= ref_date bei Fehlmengen im Lager.
    """
    prod_deadline = calculate_order_deadline(target_date, lt_w, unit='weeks')
    if prod_deadline is None or prod_deadline < ref_date:
        d_str = prod_deadline.strftime("%d.%m.%Y") if prod_deadline else "-"
        return True, None, f"Produktionsstart-Deadline ({d_str}) ist abgelaufen – Produktion nicht mehr rechtzeitig möglich!"

    if bom_by_product and p_base in bom_by_product and tracker is not None:
        for comp in bom_by_product[p_base]:
            needed_mat = candidate_prod * comp["quantity"]
            m_base = comp["base_mat_nr"]
            avail_mat = tracker.get_available_quantity(m_base, prod_deadline)
            if avail_mat < needed_mat:
                mat_lead = comp["lead_days"]
                mat_deadline = prod_deadline - datetime.timedelta(days=mat_lead)
                if mat_deadline < ref_date:
                    m_dead_str = mat_deadline.strftime("%d.%m.%Y")
                    reason = (
                        f"Material '{comp['materialName']}' benötigt {mat_lead} Tage Vorlauf "
                        f"(Bestellfrist {m_dead_str} abgelaufen) – rechtzeitige Produktion nicht möglich!"
                    )
                    return True, None, reason

    return False, prod_deadline, ""


def calculate_production_needs(
    df_plan,
    target_months,
    constraints_dict=None,
    df_open_prod=None,
    df_cogs=None,
    df_mat_stock=None,
    df_open_po=None,
    df_open_prod_mat=None,
    reference_date=None
):
    """
    Berechnet monatliche Nettoproduktionsbedarfe und Bestelltermine unter Berücksichtigung von Restriktionen,
    bereits angesetzten Produktionsaufträgen sowie der Beschaffungsfristen aller benötigten Rohmaterialien.
    """
    if df_plan.empty or not target_months:
        cols = ["Artikelnummer", "Artikelname", "MOQ", "Safety_Stock", "Lead_Time_Weeks"]
        for i in range(1, len(target_months) + 1):
            cols.extend([f"Angesetzte_Produktion_M{i}", f"Produktion_M{i}", f"Bestelldatum_M{i}"])
        return pd.DataFrame(columns=cols)

    ref_date = to_date(reference_date, default=datetime.date.today())

    res_df = pd.DataFrame({
        "Artikelnummer": df_plan["Artikelnummer"].values,
        "Artikelname": df_plan["Artikelname"].values
    })

    moq, safety_stock, lead_time_weeks = _parse_constraints_dataframe(df_plan, constraints_dict)
    res_df["MOQ"] = moq
    res_df["Safety_Stock"] = safety_stock
    res_df["Lead_Time_Weeks"] = lead_time_weeks.values

    current_stock = df_plan["Aktueller_Bestand"].fillna(0).astype(float).values.copy()
    plan_base_keys = df_plan["Artikelnummer"].astype(str).str.split("-").str[0].str.strip()

    # Offene Produktionen je Zielmonat gruppieren
    open_prod_by_month = _group_open_productions_by_month(df_open_prod, target_months, ref_date)

    # Material-Tracking via MaterialSupplyTracker initialisieren
    has_bom = df_cogs is not None and not df_cogs.empty
    bom_by_product = {}
    tracker = None

    if has_bom:
        tracker = MaterialSupplyTracker(df_mat_stock=df_mat_stock, df_open_po=df_open_po, reference_date=ref_date)
        resolved_open_mat = resolve_open_production_materials(
            df_open_prod=df_open_prod,
            df_cogs=df_cogs,
            reference_date=ref_date,
            df_open_prod_mat=df_open_prod_mat
        )
        if not resolved_open_mat.empty:
            for row in resolved_open_mat.itertuples(index=False):
                base_m = row.materialArticleNumber
                raw_qty = getattr(row, "quantity", 0.0)
                qty = float(raw_qty) if pd.notna(raw_qty) else 0.0
                t_end = row.targetEndDate
                if t_end and qty > 0:
                    tracker.allocate(base_m, qty, t_end + datetime.timedelta(days=1))

        bom_by_product = build_bom_index(df_cogs)["bom_by_product"]

    # Monatliche Nettobedarfsberechnung
    for i, target_date in enumerate(target_months, start=1):
        manuell_col = f"Manuell_M{i}"
        demands = df_plan[manuell_col].fillna(0).astype(float).values if manuell_col in df_plan.columns else np.zeros(len(df_plan))
        m_dict = open_prod_by_month.get(i, {})
        scheduled = plan_base_keys.map(lambda k: m_dict.get(k, 0.0)).fillna(0.0).values
        res_df[f"Angesetzte_Produktion_M{i}"] = scheduled.astype(int)

        prod_list = []
        order_date_list = []
        shortage_list = []
        reason_list = []

        for idx in range(len(df_plan)):
            demand_val = demands[idx]
            sched_val = scheduled[idx]
            cur_stock = current_stock[idx]
            ss = safety_stock[idx]
            m_val = moq[idx]
            lt_w = lead_time_weeks.iloc[idx]
            p_base = plan_base_keys.iloc[idx]

            avail_before = cur_stock + sched_val
            req_val = max(0.0, demand_val + ss - avail_before)

            if req_val <= 0:
                prod_list.append(0)
                order_date_list.append(None)
                shortage_list.append(0)
                reason_list.append("")
                current_stock[idx] = max(0.0, avail_before - demand_val)
                continue

            candidate_prod = int(np.ceil(req_val / m_val) * m_val)
            is_unfeasible, prod_deadline, reason = _evaluate_candidate_feasibility(
                candidate_prod, target_date, lt_w, p_base, bom_by_product, tracker, ref_date
            )

            if is_unfeasible:
                prod_list.append(0)
                order_date_list.append(None)
                shortage_list.append(int(max(0.0, demand_val - avail_before)))
                reason_list.append(reason)
                current_stock[idx] = max(0.0, avail_before - demand_val)
            else:
                prod_list.append(candidate_prod)
                order_date_list.append(prod_deadline)
                shortage_list.append(0)
                reason_list.append("")
                current_stock[idx] = max(0.0, avail_before + candidate_prod - demand_val)

                if has_bom and p_base in bom_by_product:
                    for comp in bom_by_product[p_base]:
                        tracker.allocate(comp["base_mat_nr"], candidate_prod * comp["quantity"], prod_deadline)

        res_df[f"Produktion_M{i}"] = prod_list
        res_df[f"Bestelldatum_M{i}"] = order_date_list
        res_df[f"Fehlbestand_M{i}"] = shortage_list
        res_df[f"Fehlbestand_Grund_M{i}"] = reason_list

    return res_df
