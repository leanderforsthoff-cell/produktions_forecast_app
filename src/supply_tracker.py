"""MaterialSupplyTracker: Verwaltung und FIFO-Allokation von Rohstoff- und Verpackungsbeständen."""

import datetime
import pandas as pd
from src.utils import normalize_mat_nr, parse_weclapp_date, to_date


class MaterialSupplyTracker:
    """
    Verwaltet Lagerbestände und terminierte Bestellungen für Rohstoffe und Verpackungsmaterialien.
    Ermöglicht FIFO-Allokation und Fristprüfungen für offene und künftige Produktionen.
    """

    def __init__(self, df_mat_stock=None, df_open_po=None, reference_date=None):
        self.ref_date = to_date(reference_date)
        self.supplies = {}  # {mat_nr: [{"avail_date": date, "delivery_date": date, "qty": float}]}
        self._init_stock(df_mat_stock)
        self._init_po(df_open_po)

    def _init_stock(self, df_mat_stock):
        if df_mat_stock is None or df_mat_stock.empty:
            return
        for row in df_mat_stock.itertuples(index=False):
            raw_m = getattr(row, "materialArticleNumber", None)
            if pd.isna(raw_m):
                continue
            base_m = normalize_mat_nr(raw_m)
            raw_qty = getattr(row, "Aktueller_Materialbestand", 0.0)
            qty = float(raw_qty) if pd.notna(raw_qty) else 0.0
            if qty > 0:
                self.supplies.setdefault(base_m, []).append({
                    "avail_date": datetime.date.min,
                    "delivery_date": datetime.date.min,
                    "qty": qty
                })

    def _init_po(self, df_open_po):
        if df_open_po is None or df_open_po.empty:
            return
        for row in df_open_po.itertuples(index=False):
            raw_m = getattr(row, "materialArticleNumber", None)
            if pd.isna(raw_m):
                continue
            base_m = normalize_mat_nr(raw_m)
            raw_qty = getattr(row, "quantity", 0.0)
            qty = float(raw_qty) if pd.notna(raw_qty) else 0.0
            if qty <= 0:
                continue
            d_date = parse_weclapp_date(getattr(row, "plannedDeliveryDate", None))
            if d_date is None or (self.ref_date is not None and d_date < self.ref_date):
                continue
            # Zugang wird einen Tag nach geplantem Liefertermin verbucht
            booking_date = d_date + datetime.timedelta(days=1)
            self.supplies.setdefault(base_m, []).append({
                "avail_date": booking_date,
                "delivery_date": d_date,
                "qty": qty
            })
        for mat_nr in self.supplies:
            self.supplies[mat_nr].sort(key=lambda s: s["avail_date"])

    def get_available_quantity(self, mat_nr, deadline, by_delivery_date=False):
        """Gibt die bis zur Frist verfügbare Menge zurück."""
        base_m = normalize_mat_nr(mat_nr)
        date_key = "delivery_date" if by_delivery_date else "avail_date"
        d = to_date(deadline)
        if d is None:
            return 0.0
        return sum(
            s["qty"] for s in self.supplies.get(base_m, [])
            if s[date_key] <= d and s["qty"] > 0
        )

    def allocate(self, mat_nr, req_qty, deadline, by_delivery_date=False):
        """
        Zieht bis zu req_qty nach FIFO von den bis zur Frist verfügbaren Beständen ab.
        Gibt die tatsächlich gedeckte Menge zurück.
        """
        base_m = normalize_mat_nr(mat_nr)
        date_key = "delivery_date" if by_delivery_date else "avail_date"
        d = to_date(deadline)
        if d is None or req_qty <= 0:
            return 0.0

        covered = 0.0
        for s in self.supplies.get(base_m, []):
            if s[date_key] <= d and s["qty"] > 0:
                take = min(req_qty - covered, s["qty"])
                covered += take
                s["qty"] -= take
                if covered >= req_qty:
                    break
        return covered

    def get_earliest_late_supply(self, mat_nr, deadline, by_delivery_date=False):
        """Liefert die erste verspätete Lieferung für Begründungstexte bei Fehlbeständen."""
        base_m = normalize_mat_nr(mat_nr)
        date_key = "delivery_date" if by_delivery_date else "avail_date"
        d = to_date(deadline)
        if d is None:
            return None
        for s in self.supplies.get(base_m, []):
            if s[date_key] > d and s["qty"] > 0:
                return s
        return None

    def copy(self):
        """Erstellt eine tiefe Kopie des Trackers für isolierte Prüfungen."""
        new_tracker = MaterialSupplyTracker.__new__(MaterialSupplyTracker)
        new_tracker.ref_date = self.ref_date
        new_tracker.supplies = {
            mat: [{"avail_date": s["avail_date"], "delivery_date": s["delivery_date"], "qty": s["qty"]} for s in slist]
            for mat, slist in self.supplies.items()
        }
        return new_tracker
