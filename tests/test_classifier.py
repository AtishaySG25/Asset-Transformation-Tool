"""Classification regressions."""
from smart_ads.classifier import name_hint
from smart_ads.element import ElementType


def test_short_keywords_are_whole_word():
    # Regression: 'cta' must NOT match inside 're[cta]ngle'.
    assert name_hint("Rectangle 4") is None
    assert name_hint("cta") is ElementType.CTA
    assert name_hint("Invest Now") is ElementType.CTA


def test_subheadline_is_subtext_not_headline():
    # 'subheadline' contains 'headline'; SUBTEXT must win.
    assert name_hint("subheadline") is ElementType.SUBTEXT
    assert name_hint("headline") is ElementType.HEADLINE


def test_rating_band_not_logo():
    # 'app-rating-logo' contains 'logo' but should read as decorative.
    assert name_hint("app-rating-logo") is ElementType.DECORATIVE


def test_common_roles():
    assert name_hint("background") is ElementType.BACKGROUND
    assert name_hint("Axis Logo") is ElementType.LOGO
    assert name_hint("Riskometer") is ElementType.GRAPH
    assert name_hint("scheme-name") is ElementType.HEADLINE
    assert name_hint("Mutual Fund investments are subject to market risks") \
        is ElementType.DISCLAIMER
    assert name_hint("random layer 12") is None
