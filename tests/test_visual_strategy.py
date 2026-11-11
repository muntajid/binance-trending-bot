from bot.visual_strategy import quality_checklist, select_visual_plan


def test_representative_topic_routes():
    assert select_visual_plan("Bitcoin news update").format == "THESIS"
    assert select_visual_plan("ETF flows", "inflows and outflows").format == "VERSUS"
    assert select_visual_plan("whale transaction report").format == "DATA"
    assert select_visual_plan("educational crypto security post").format == "EXPLAINER"
    assert select_visual_plan("BTC vs ETH market comparison").format == "VERSUS"


def test_outcome_precedes_general_price_analysis():
    plan = select_visual_plan("Bitcoin price analysis", "target hit result")
    assert plan.format == "RECEIPT"
    assert "verify every displayed number" in quality_checklist(plan)[-2]


def test_editorial_fallback_for_unmapped_topic():
    plan = select_visual_plan("community announcement")
    assert plan.format == "EDITORIAL"
    assert "no numeric data" in plan.evidence
