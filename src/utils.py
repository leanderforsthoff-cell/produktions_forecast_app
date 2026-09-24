import pandas as pd
import datetime

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