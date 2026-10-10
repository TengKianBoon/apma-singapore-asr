from datetime import datetime, timezone
from decimal import Decimal

import pytest

from services.commercial_pricing import (
    PLAN_COMPARE_AND_FLAG,
    PLAN_DIALECT_DRAFT,
    PLAN_HUMAN_REVIEW,
    CommercialPricingConfig,
    CommercialPricingError,
    quote_commercial_order,
    with_overrides,
)


NOW = datetime(2026, 9, 11, 4, 0, tzinfo=timezone.utc)


def _quote(**kwargs):
    return quote_commercial_order(now=NOW, quote_id="quote_test", **kwargs)


def test_dialect_draft_uses_minimum_order_and_qwen_only():
    quote = _quote(
        plan_id=PLAN_DIALECT_DRAFT,
        payment_method="paynow",
        duration_seconds=60,
    )

    calculation = quote["calculation"]
    assert calculation["billed_audio_minutes"] == 1
    assert calculation["provider_routes"] == ["qwen_filetrans"]
    assert calculation["list_price_pre_gst_sgd"] == "5.90"
    assert calculation["price_pre_gst_sgd"] == "5.90"
    assert quote["status"] == "quoted_not_paid"


def test_audio_duration_rounds_up_to_next_minute():
    quote = _quote(
        plan_id=PLAN_DIALECT_DRAFT,
        payment_method="paynow",
        duration_seconds="3600.1",
    )

    calculation = quote["calculation"]
    assert calculation["billed_audio_minutes"] == 61
    assert calculation["list_price_pre_gst_sgd"] == "6.00"


def test_compare_plan_uses_both_provider_costs_and_card_fee():
    quote = _quote(
        plan_id=PLAN_COMPARE_AND_FLAG,
        payment_method="card",
        duration_seconds=3600,
    )

    calculation = quote["calculation"]
    assert calculation["provider_routes"] == ["qwen_filetrans", "gpt_transcribe"]
    assert calculation["price_pre_gst_sgd"] == "9.90"
    assert calculation["costs"]["product_operations_reserve_sgd"] == "2.00"
    assert calculation["delivery_cost_sgd"] == "6.09"
    assert Decimal(calculation["payment_fee_estimate_sgd"]) > Decimal("0.50")
    assert Decimal(calculation["after_tax_margin"]) >= Decimal("0.15")


def test_review_budget_bills_minimum_active_minutes_and_meets_margin():
    quote = _quote(
        plan_id=PLAN_HUMAN_REVIEW,
        payment_method="card",
        requested_review_minutes=12,
    )

    calculation = quote["calculation"]
    assert calculation["requested_review_minutes"] == 12
    assert calculation["billed_review_minutes"] == 30
    assert calculation["list_price_pre_gst_sgd"] == "27.00"
    assert calculation["delivery_cost_sgd"] == "20.00"
    assert Decimal(calculation["after_tax_margin"]) >= Decimal("0.15")


def test_cost_floor_overrides_list_price_when_reviewer_cost_increases():
    config = with_overrides(
        CommercialPricingConfig(), reviewer_cost_per_hour_sgd=Decimal("60")
    )
    quote = _quote(
        plan_id=PLAN_HUMAN_REVIEW,
        payment_method="card",
        requested_review_minutes=30,
        config=config,
    )

    calculation = quote["calculation"]
    assert Decimal(calculation["price_pre_gst_sgd"]) > Decimal(
        calculation["list_price_pre_gst_sgd"]
    )
    assert Decimal(calculation["after_tax_margin"]) >= Decimal("0.15")


def test_gst_is_added_only_when_registered_and_fee_uses_customer_total():
    base = CommercialPricingConfig()
    gst_config = with_overrides(base, gst_registered=True)
    without_gst = _quote(
        plan_id=PLAN_COMPARE_AND_FLAG,
        payment_method="card",
        duration_seconds=3600,
        config=base,
    )
    with_gst = _quote(
        plan_id=PLAN_COMPARE_AND_FLAG,
        payment_method="card",
        duration_seconds=3600,
        config=gst_config,
    )

    assert without_gst["calculation"]["gst_amount_sgd"] == "0.00"
    assert with_gst["calculation"]["gst_amount_sgd"] == "0.89"
    assert with_gst["calculation"]["customer_total_sgd"] == "10.79"
    assert Decimal(
        with_gst["calculation"]["payment_fee_estimate_sgd"]
    ) > Decimal(without_gst["calculation"]["payment_fee_estimate_sgd"])


def test_quote_is_versioned_expiring_and_calculation_hash_is_deterministic():
    first = _quote(
        plan_id=PLAN_DIALECT_DRAFT,
        payment_method="paynow",
        duration_seconds=1800,
    )
    second = _quote(
        plan_id=PLAN_DIALECT_DRAFT,
        payment_method="paynow",
        duration_seconds=1800,
    )

    assert first["quote_id"] == "quote_test"
    assert first["created_at"] == "2026-09-11T04:00:00Z"
    assert first["expires_at"] == "2026-09-11T04:30:00Z"
    assert first["calculation"]["price_book_version"]
    assert first["calculation_hash_sha256"] == second["calculation_hash_sha256"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"plan_id": "unknown", "payment_method": "paynow", "duration_seconds": 60},
        {
            "plan_id": PLAN_DIALECT_DRAFT,
            "payment_method": "cash",
            "duration_seconds": 60,
        },
        {
            "plan_id": PLAN_DIALECT_DRAFT,
            "payment_method": "paynow",
            "duration_seconds": 0,
        },
        {
            "plan_id": PLAN_HUMAN_REVIEW,
            "payment_method": "paynow",
            "requested_review_minutes": None,
        },
    ],
)
def test_invalid_quote_requests_are_rejected(kwargs):
    with pytest.raises(CommercialPricingError):
        _quote(**kwargs)


def test_environment_overrides_are_bounded_and_contain_no_secret_fields():
    cfg = CommercialPricingConfig.from_environment(
        {
            "APMA_GST_REGISTERED": "true",
            "APMA_GST_RATE": "0.09",
            "APMA_INCOME_TAX_RATE": "0.10",
            "APMA_TARGET_AFTER_TAX_MARGIN": "0.18",
            "APMA_USD_TO_SGD_RATE": "1.36",
            "APMA_REVIEWER_COST_PER_HOUR_SGD": "42",
        }
    )

    assert cfg.gst_registered is True
    assert cfg.target_after_tax_margin == Decimal("0.18")
    assert cfg.reviewer_cost_per_hour_sgd == Decimal("42")
    assert "api" not in " ".join(cfg.__dataclass_fields__).lower()


def test_impossible_margin_configuration_is_rejected():
    with pytest.raises(CommercialPricingError):
        with_overrides(
            CommercialPricingConfig(),
            target_after_tax_margin=Decimal("0.90"),
        )


def test_quote_contract_has_no_customer_or_content_fields():
    quote = _quote(
        plan_id=PLAN_COMPARE_AND_FLAG,
        payment_method="paynow",
        duration_seconds=1200,
    )
    serialized = str(quote).lower()

    for forbidden in ("customer_name", "email", "phone", "transcript", "audio_path"):
        assert forbidden not in serialized
