import pandas as pd
import datetime

monate_de = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]

def get_target_months(horizon_months=3, start_date=None):
    """Berechnet den 1. der nächsten N Monate."""
    if start_date is None:
        start_date = datetime.date.today()
    
    current = start_date.replace(day=1)
    target_months = []
    for _ in range(horizon_months):
        current = (current + datetime.timedelta(days=32)).replace(day=1)
        target_months.append(current)
    return target_months

def calculate_order_deadline(target_date, lead_time, unit='days'):
    """
    Berechnet das späteste Bestelldatum zum 15. des Monats.
    unit kann 'days' oder 'weeks' sein.
    """
    if pd.isnull(target_date):
        return None
    
    # 1. Pandas übernimmt das fehleranfällige Type-Checking und Parsing für uns
    parsed_date = pd.to_datetime(target_date).date()
        
    safe_lead_time = 0 if pd.isna(lead_time) else int(lead_time)
    
    # 2. Vorlaufzeit abziehen (Tage oder Wochen)
    if unit == 'weeks':
        deadline = parsed_date - datetime.timedelta(weeks=safe_lead_time)
    else:
        deadline = parsed_date - datetime.timedelta(days=safe_lead_time)
    
    # 3. Rücklauf auf den 15. des Monats
    if deadline.day >= 15:
        return deadline.replace(day=15)
    else:
        # Erster Tag des aktuellen Monats minus 1 Tag = Letzter Tag des Vormonats
        prev_month_end = deadline.replace(day=1) - datetime.timedelta(days=1)
        return prev_month_end.replace(day=15)

def format_date_deadline(d, reference_date=None):
    """
    Formatiert das Datum. Liegt es in der Vergangenheit, wird das heutige (oder übergebene Referenz-)
    Datum als nächstmöglicher Aktionstag gesetzt und das alte Datum markiert.
    """
    if pd.isnull(d):
        return "-"
    
    if reference_date is None:
        ref = datetime.date.today()
    elif isinstance(reference_date, (datetime.datetime, pd.Timestamp)):
        ref = reference_date.date()
    elif isinstance(reference_date, datetime.date):
        ref = reference_date
    else:
        ref = pd.to_datetime(reference_date).date()

    if isinstance(d, (datetime.datetime, pd.Timestamp)):
        parsed_d = d.date()
    elif isinstance(d, datetime.date):
        parsed_d = d
    else:
        parsed_d = pd.to_datetime(d).date()
    
    if parsed_d < ref:
        # Ausgabe z.B.: "16.09.2026 🔴 (eig. 15.08.2026)"
        return f"{ref.strftime('%d.%m.%Y')} 🔴 (eig. {parsed_d.strftime('%d.%m.%Y')})"
    
    return parsed_d.strftime("%d.%m.%Y")