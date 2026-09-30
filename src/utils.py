"""Hilfsfunktionen für Datumsberechnungen, Monatssequenzen, Fristformatierungen und Material-Tracking."""

import datetime
import numpy as np
import pandas as pd

monate_de = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def to_date(val, default=None):
    """
    Konvertiert ein beliebiges Datum (date, datetime, Timestamp, String) in ein datetime.date.
    Gibt default zurück, falls val None, NaT oder ungültig ist.
    """
    if val is None or pd.isna(val):
        return default
    if isinstance(val, (datetime.datetime, pd.Timestamp)):
        return val.date()
    if isinstance(val, datetime.date):
        return val
    if isinstance(val, str):
        s = val.strip().split(" ")[0].strip()
        for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(s, fmt).date()
            except ValueError:
                pass
    try:
        dt = pd.to_datetime(val, dayfirst=True)
        return dt.date() if pd.notna(dt) else default
    except Exception:
        return default


def get_target_months(horizon_months=3, start_date=None):
    """
    Berechnet die Monatsersten der nächsten N Monate.

    Args:
        horizon_months (int): Anzahl der vorauszuplanenden Monate.
        start_date (datetime.date, optional): Basisdatum. Standard ist heute.

    Returns:
        list[datetime.date]: Liste der Monatsersten im Planungshorizont.
    """
    start = to_date(start_date, default=datetime.date.today())
    current = start.replace(day=1)
    target_months = []
    for _ in range(horizon_months):
        current = (current + datetime.timedelta(days=32)).replace(day=1)
        target_months.append(current)
    return target_months


def parse_weclapp_date(val):
    """
    Parst ein Datum aus Weclapp (unterstützt Millisekunden-Timestamps in Europe/Berlin,
    Datums-Strings, Timestamps und datetime.date).
    
    Weclapp speichert Mitternacht-Termine (00:00:00 Europe/Berlin) als UTC-Millisekunden
    des Vorabends (z.B. 22:00 UTC). Durch die Konvertierung nach Europe/Berlin wird der
    korrekte Kalendertag ermittelt.
    """
    if pd.isna(val) or val is None:
        return None
    if isinstance(val, (datetime.datetime, pd.Timestamp)):
        return val.date()
    if isinstance(val, datetime.date):
        return val
    if isinstance(val, (int, float, np.integer, np.floating)) or (isinstance(val, str) and str(val).isdigit()):
        return pd.to_datetime(int(val), unit="ms", utc=True).tz_convert("Europe/Berlin").date()
    try:
        dt = pd.to_datetime(val)
        if dt.tzinfo is not None:
            return dt.tz_convert("Europe/Berlin").date()
        return dt.date()
    except Exception:
        return None


def normalize_mat_nr(m):
    """Normalisiert Materialnummern unabhängig von Typ (int, float, string) und Suffixen (-C)."""
    if pd.isna(m) or m is None:
        return ""
    if isinstance(m, (int, np.integer)):
        return str(m)
    if isinstance(m, (float, np.floating)):
        return str(int(m)) if m.is_integer() else str(m)
    s = str(m).strip().split("-")[0].strip()
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except ValueError:
        pass
    return s


def calculate_order_deadline(target_date, lead_time, unit='days'):
    """
    Berechnet das spätestmögliche Bestelldatum (tagesgenau und flexibel).

    Zieht die Vorlaufzeit exakt vom Zieltermin ab, ohne starres Snapping auf den 15. des Monats.

    Args:
        target_date (date-like): Geplanter Bedarfs- oder Fertigstellungstermin.
        lead_time (int | float): Vorlaufzeit.
        unit (str): Einheit der Vorlaufzeit ('days' oder 'weeks').

    Returns:
        datetime.date | None: Spätestmögliches Bestelldatum oder None bei fehlendem target_date.
    """
    parsed_date = to_date(target_date)
    if parsed_date is None:
        return None
    
    safe_lead_time = 0 if pd.isna(lead_time) else int(lead_time)
    if unit == 'weeks':
        return parsed_date - datetime.timedelta(weeks=safe_lead_time)
    return parsed_date - datetime.timedelta(days=safe_lead_time)


def format_date_deadline(d, reference_date=None):
    """
    Formatiert ein Bestelldatum (TT.MM.JJJJ) für die Anzeige.

    Liegt das Datum vor dem Referenzdatum, wird der Verzug markiert: Das Referenzdatum
    wird als nächstmöglicher Aktionstag mit 🔴 und Angabe des Ursprungstermins dargestellt.
    """
    parsed_d = to_date(d)
    if parsed_d is None:
        return "-"
    
    ref = to_date(reference_date, default=datetime.date.today())
    if parsed_d < ref:
        return f"{ref.strftime('%d.%m.%Y')} 🔴 (eig. {parsed_d.strftime('%d.%m.%Y')})"
    return parsed_d.strftime("%d.%m.%Y")


def format_currency(val):
    """Formatiert einen Betrag als deutschen Währungsstring (z.B. 1.234,56 €)."""
    if pd.isna(val) or val is None:
        return "0,00 €"
    try:
        f = float(val)
    except (ValueError, TypeError):
        return "0,00 €"
    return f"{f:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def format_quantity(val, unit=""):
    """Formatiert eine Mengenangabe (z.B. 2.000 oder 2.000 Stk.)."""
    if pd.isna(val) or val is None:
        return "-"
    try:
        f = float(val)
        if f <= 0:
            return "-"
        formatted = f"{int(f):,}".replace(",", ".")
        return f"{formatted} {unit}".strip() if unit else formatted
    except (ValueError, TypeError):
        return "-"


