from __future__ import annotations

from datetime import date


def get_fiscal_context(today: date | None = None) -> dict[str, str]:
    """
    Returns Indian fiscal year/quarter context for prompt injection.

    India FY: April 1 – March 31
      FY26 = April 2025 – March 2026

    Quarters:
      Q1: April – June
      Q2: July – September
      Q3: October – December
      Q4: January – March

    latest_quarter: The quarter whose results are most likely published.
    Results typically land 4–6 weeks after quarter end:
      - Q4 FY ends March 31 → results in May/June
      - Q1 FY ends June 30  → results in August
      - Q2 FY ends Sep 30   → results in November
      - Q3 FY ends Dec 31   → results in January/February

    So "latest available" = current_quarter - 1, with FY rollover handled.
    """
    if today is None:
        today = date.today()

    # Fiscal year: starts April 1
    # April 2025 → FY26, January 2026 → FY26
    fy = today.year + 1 if today.month >= 4 else today.year
    fy_label = f"FY{fy % 100:02d}"  # "FY26"

    # Current quarter
    m = today.month
    if m in (4, 5, 6):
        current_q = 1
    elif m in (7, 8, 9):
        current_q = 2
    elif m in (10, 11, 12):
        current_q = 3
    else:
        current_q = 4

    # Latest published results = one quarter behind current
    if current_q == 1:
        latest_q, latest_fy = 4, fy - 1
    else:
        latest_q, latest_fy = current_q - 1, fy

    # The quarter before latest (for YoY comparison references)
    if latest_q == 1:
        prev_q, prev_fy = 4, latest_fy - 1
    else:
        prev_q, prev_fy = latest_q - 1, latest_fy

    latest_fy_label = f"FY{latest_fy % 100:02d}"
    prev_fy_label = f"FY{prev_fy % 100:02d}"

    return {
        "current_fy": fy_label,  # "FY26"
        "current_quarter": f"Q{current_q} {fy_label}",  # "Q4 FY26"
        "latest_quarter": f"Q{latest_q} {latest_fy_label}",  # "Q3 FY26"
        "prev_quarter": f"Q{prev_q} {prev_fy_label}",  # "Q2 FY26"
        "search_year": str(today.year),  # "2026"
        "today_date": today.strftime("%B %d, %Y"),  # "March 21, 2026"
    }
