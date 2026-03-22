from tests.test_analyst import make_report_card_payload

# ── Scoring helpers ──────────────────────────────────────────────────────────


def score_growth_engine(payload: dict) -> tuple[int, list[str]]:
    """
    Returns (score 0-100, list of failure reasons).
    Penalises stale historical framing and rewards current-quarter grounding.
    """
    ge = payload.get("growth_engine", {})
    score = 100
    failures = []

    revenue_cagr = str(ge.get("revenue_cagr", "")).lower()
    eps_cagr = str(ge.get("eps_cagr", "")).lower()
    growth_score = int(ge.get("growth_score", 5))

    # Penalise: cagr fields reference historical periods without qualification
    stale_markers = ["fy24", "fy25", "3-year", "3 year", "historical", "past"]
    for marker in stale_markers:
        if marker in revenue_cagr:
            score -= 20
            failures.append(f"revenue_cagr references stale period: '{marker}'")
            break
    for marker in stale_markers:
        if marker in eps_cagr:
            score -= 20
            failures.append(f"eps_cagr references stale period: '{marker}'")
            break

    # Penalise: growth_score unrealistically high when recent decline is present
    # (simulated via the payload's own company_risks field)
    company_risks = payload.get("risk_matrix", {}).get("company_risks", [])
    has_recent_decline = any(
        any(kw in str(r).lower() for kw in ["decline", "drop", "fall", "negative", "compression"])
        for r in company_risks
    )
    if has_recent_decline and growth_score > 6:
        score -= 25
        failures.append(f"growth_score={growth_score} is too high given company_risks mention recent decline")

    return max(score, 0), failures


def score_risk_matrix(payload: dict) -> tuple[int, list[str]]:
    """
    Penalises missing competitive/regulatory risks; rewards specificity.
    """
    rm = payload.get("risk_matrix", {})
    score = 100
    failures = []

    all_risks = rm.get("structural_risks", []) + rm.get("cyclical_risks", []) + rm.get("company_risks", [])
    _ = " ".join(str(r).lower() for r in all_risks)

    if len(all_risks) < 3:
        score -= 30
        failures.append(f"risk_matrix has only {len(all_risks)} risks total — minimum 3 expected")

    # Penalise generic single-word risks
    generic_risks = [r for r in all_risks if len(str(r).split()) <= 2]
    if generic_risks:
        score -= 25
        failures.append(f"risk_matrix has {len(generic_risks)} overly generic risk(s): {generic_risks}")

    return max(score, 0), failures


def score_data_sources(payload: dict) -> tuple[int, list[str]]:
    """
    Checks sources are present, non-empty, and look like real citations.
    """
    sources = payload.get("data_sources", [])
    score = 100
    failures = []

    if len(sources) < 2:
        score -= 40
        failures.append(f"Only {len(sources)} data source(s) — minimum 2 expected")

    placeholder_markers = ["example.com", "placeholder", "todo", "unknown", "n/a"]
    bad = [s for s in sources if any(m in str(s).lower() for m in placeholder_markers)]
    if bad:
        score -= 40
        failures.append(f"Placeholder/invalid data sources found: {bad}")

    return max(score, 0), failures


def score_verdict_consistency(payload: dict) -> tuple[int, list[str]]:
    """
    Checks that verdict aligns with timing and risk signals.
    BUY with Risky timing + High risk = inconsistent.
    HOLD/EXIT with Favorable timing + Low risk = inconsistent.
    """
    verdict = payload.get("final_verdict", {}).get("verdict", "HOLD")
    timing_signal = payload.get("timing", {}).get("timing_signal", "Neutral")
    risk_level = payload.get("risk_matrix", {}).get("risk_level", "Medium")
    score = 100
    failures = []

    if verdict in ("BUY", "ADD") and timing_signal == "Risky" and risk_level == "High":
        score -= 40
        failures.append(f"Verdict={verdict} is inconsistent with timing={timing_signal} and risk={risk_level}")
    if verdict == "EXIT" and timing_signal == "Favorable" and risk_level == "Low":
        score -= 40
        failures.append(f"Verdict={verdict} is inconsistent with timing={timing_signal} and risk={risk_level}")

    return max(score, 0), failures


def eval_report_card(payload: dict) -> dict:
    """
    Runs all scorers. Returns a summary dict with per-section scores and
    an overall weighted score (0-100).
    """
    growth_score, growth_failures = score_growth_engine(payload)
    risk_score, risk_failures = score_risk_matrix(payload)
    sources_score, sources_failures = score_data_sources(payload)
    verdict_score, verdict_failures = score_verdict_consistency(payload)

    # Weighted: growth most important (catching the FY25 stale data bug), then risk
    weighted = int(growth_score * 0.35 + risk_score * 0.25 + sources_score * 0.20 + verdict_score * 0.20)
    return {
        "overall": weighted,
        "growth": {"score": growth_score, "failures": growth_failures},
        "risk": {"score": risk_score, "failures": risk_failures},
        "sources": {"score": sources_score, "failures": sources_failures},
        "verdict_consistency": {"score": verdict_score, "failures": verdict_failures},
    }


# ── Fixtures ─────────────────────────────────────────────────────────────────


def make_stale_growth_payload() -> dict:
    """Simulates a Haiku output that used historical FY25 CAGR — the CDSL bug."""
    payload = make_report_card_payload("CDSL", name="CDSL", final_verdict="HOLD")
    payload["growth_engine"]["revenue_cagr"] = "32% (FY25 vs FY24)"
    payload["growth_engine"]["eps_cagr"] = "25% (FY25 net profit growth)"
    payload["growth_engine"]["growth_score"] = 8
    payload["risk_matrix"]["structural_risks"] = []
    payload["risk_matrix"]["cyclical_risks"] = []
    payload["risk_matrix"]["company_risks"] = [
        "Recent quarterly profit decline YoY",
        "Margin compression each quarter",
    ]
    return payload


def make_current_growth_payload() -> dict:
    """Simulates a correct output that uses recent quarter trajectory."""
    payload = make_report_card_payload("CDSL", name="CDSL", final_verdict="HOLD")
    payload["growth_engine"]["revenue_cagr"] = "Q3 FY26: +9% YoY (decelerating from 32% FY25)"
    payload["growth_engine"]["eps_cagr"] = "Q3 FY26: +2.5% YoY (sharp deceleration)"
    payload["growth_engine"]["growth_score"] = 3
    payload["risk_matrix"]["company_risks"] = [
        "Q1 FY26 net profit declined 23.7% YoY to ₹102Cr",
        "EBITDA margin compressed 280bps QoQ to 52.9%",
        "Employee cost growing 25% vs revenue growing 9%",
    ]
    payload["data_sources"] = [
        "Screener.in - CDSL consolidated financials",
        "MarketsMojo - Q3 FY26 result analysis Jan 31 2026",
        "AlphaSpread - Q2 FY26 earnings call transcript",
    ]
    return payload


def make_missing_competitor_payload() -> dict:
    """Simulates a payload with no mention of NSDL — the competitive blindspot."""
    payload = make_report_card_payload("CDSL", name="CDSL", final_verdict="HOLD")
    payload["risk_matrix"]["structural_risks"] = ["Regulatory changes"]
    payload["risk_matrix"]["cyclical_risks"] = ["Market downturn"]
    payload["risk_matrix"]["company_risks"] = ["Rising costs"]
    return payload


# ── Eval tests ────────────────────────────────────────────────────────────────


def test_eval_detects_stale_growth_cagr():
    """Eval must catch the FY25 CAGR anchoring bug (the exact CDSL failure)."""
    result = eval_report_card(make_stale_growth_payload())
    assert result["growth"]["score"] < 60, (
        f"Expected stale CAGR to be penalised. Got score={result['growth']['score']}, "
        f"failures={result['growth']['failures']}"
    )


def test_eval_passes_current_growth_trajectory():
    """A correctly written recent-quarter output should score well on growth."""
    result = eval_report_card(make_current_growth_payload())
    assert result["growth"]["score"] >= 80, (
        f"Expected current trajectory to score high. Got score={result['growth']['score']}, "
        f"failures={result['growth']['failures']}"
    )


def test_eval_penalises_thin_risk_matrix():
    """A risk matrix with only generic 1-2 word entries should lose points."""
    result = eval_report_card(make_missing_competitor_payload())
    assert result["risk"]["score"] < 80, (
        f"Expected thin risk matrix to be penalised. Got score={result['risk']['score']}, "
        f"failures={result['risk']['failures']}"
    )


def test_eval_penalises_placeholder_sources():
    """Placeholder data sources should fail the sources scorer."""
    payload = make_report_card_payload("TEST", final_verdict="HOLD")
    payload["data_sources"] = ["https://example.com/test", "placeholder source"]
    result = eval_report_card(payload)
    assert result["sources"]["score"] < 70, (
        f"Expected placeholder sources to be penalised. Got score={result['sources']['score']}"
    )


def test_eval_detects_verdict_inconsistency():
    """BUY verdict with Risky timing and High risk should be flagged."""
    payload = make_report_card_payload("TEST", final_verdict="BUY")
    payload["timing"]["timing_signal"] = "Risky"
    payload["risk_matrix"]["risk_level"] = "High"
    result = eval_report_card(payload)
    assert result["verdict_consistency"]["score"] < 70, (
        f"Expected BUY+Risky+High to be flagged. Got score={result['verdict_consistency']['score']}"
    )


def test_eval_overall_score_stale_output_is_low():
    """A stale, thin output (the CDSL bug case) should have overall < 65."""
    result = eval_report_card(make_stale_growth_payload())
    assert result["overall"] < 65, (
        f"Expected overall score to be low for stale output. Got {result['overall']}\nDetail: {result}"
    )


def test_eval_overall_score_good_output_is_high():
    """A well-constructed current-trajectory output should have overall >= 75."""
    result = eval_report_card(make_current_growth_payload())
    assert result["overall"] >= 75, (
        f"Expected overall score to be high for good output. Got {result['overall']}\nDetail: {result}"
    )
