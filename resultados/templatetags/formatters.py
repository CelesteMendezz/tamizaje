# resultados/templatetags/formatters.py
from django import template

register = template.Library()

@register.filter
def pct(value, decimals=0):
    """
    0.6849 -> '68.49' si decimals=2
    """
    if value is None:
        return ""
    try:
        v = float(value) * 100.0
        d = int(decimals)
        return f"{v:.{d}f}"
    except Exception:
        return ""


