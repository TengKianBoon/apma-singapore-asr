"""Deterministic, server-owned pricing for APMA's commercial pilot.

This module calculates quotes only. It does not call a payment processor,
transcription provider, or persist customer information.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from typing import Any, Mapping, Optional


PRICE_BOOK_VERSION = "apma-sg-pilot-2026-09-11-v1"
QUOTE_SCHEMA_VERSION = "apma.commercial-quote.v1"

PLAN_DIALECT_DRAFT = "dialect_draft"
PLAN_COMPARE_AND_FLAG = "compare_and_flag"
PLAN_HUMAN_REVIEW = "human_review_budget"
PLAN_IDS = {PLAN_DIALECT_DRAFT, PLAN_COMPARE_AND_FLAG, PLAN_HUMAN_REVIEW}

PAYMENT_PAYNOW = "paynow"
PAYMENT_CARD = "card"
PAYMENT_METHODS = {PAYMENT_PAYNOW, PAYMENT_CARD}

CENT = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")
ZERO = Decimal("0")
ONE = Decimal("1")


class CommercialPricingError(ValueError):
    """Raised when a quote request or pricing policy is invalid."""


def _decimal(value: Any, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise CommercialPricingError(f"{field_name} must be numeric") from exc
    if not parsed.is_finite():
        raise CommercialPricingError(f"{field_name} must be finite")
    return parsed


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _money_up(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_CEILING)


def _format(value: Decimal, places: Decimal = CENT) -> str:
    return format(value.quantize(places, rounding=ROUND_HALF_UP), "f")


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise CommercialPricingError("APMA_GST_REGISTERED must be true or false")


@dataclass(frozen=True)
class CommercialPricingConfig:
    """Versioned cost and margin assumptions for pilot quotes."""

    currency: str = "SGD"
    price_book_version: str = PRICE_BOOK_VERSION
    quote_valid_minutes: int = 30
    gst_registered: bool = False
    gst_rate: Decimal = Decimal("0.09")
    target_after_tax_margin: Decimal = Decimal("0.15")
    income_tax_rate: Decimal = Decimal("0.17")
    usd_to_sgd_rate: Decimal = Decimal("1.40")
    provider_contingency_rate: Decimal = Decimal("0.25")

    qwen_price_per_audio_minute_usd: Decimal = Decimal("0.0021")
    openai_price_per_audio_minute_usd: Decimal = Decimal("0.0045")

    draft_list_price_per_audio_hour_sgd: Decimal = Decimal("5.90")
    draft_minimum_order_sgd: Decimal = Decimal("5.90")
    draft_infrastructure_per_audio_hour_sgd: Decimal = Decimal("0.60")
    draft_support_reserve_per_order_sgd: Decimal = Decimal("1.50")
    draft_product_operations_reserve_per_order_sgd: Decimal = Decimal("1.25")

    compare_list_price_per_audio_hour_sgd: Decimal = Decimal("9.90")
    compare_minimum_order_sgd: Decimal = Decimal("9.90")
    compare_infrastructure_per_audio_hour_sgd: Decimal = Decimal("0.90")
    compare_support_reserve_per_order_sgd: Decimal = Decimal("2.50")
    compare_product_operations_reserve_per_order_sgd: Decimal = Decimal("2.00")

    review_list_price_per_active_minute_sgd: Decimal = Decimal("0.90")
    review_minimum_minutes: int = 30
    reviewer_cost_per_hour_sgd: Decimal = Decimal("35.00")
    review_platform_per_hour_sgd: Decimal = Decimal("0.50")
    review_support_reserve_per_order_sgd: Decimal = Decimal("1.50")
    review_product_operations_reserve_per_order_sgd: Decimal = Decimal("0.75")

    stripe_card_variable_rate: Decimal = Decimal("0.034")
    stripe_card_fixed_fee_sgd: Decimal = Decimal("0.50")
    stripe_paynow_variable_rate: Decimal = Decimal("0.013")
    stripe_paynow_fixed_fee_sgd: Decimal = Decimal("0.00")

    @classmethod
    def from_environment(
        cls, env: Optional[Mapping[str, str]] = None
    ) -> "CommercialPricingConfig":
        """Load bounded operator overrides without reading or storing secrets."""

        values = os.environ if env is None else env
        cfg = cls(
            price_book_version=values.get("APMA_PRICE_BOOK_VERSION", PRICE_BOOK_VERSION),
            quote_valid_minutes=int(values.get("APMA_QUOTE_VALID_MINUTES", "30")),
            gst_registered=_parse_bool(values.get("APMA_GST_REGISTERED"), False),
            gst_rate=_decimal(values.get("APMA_GST_RATE", "0.09"), "APMA_GST_RATE"),
            target_after_tax_margin=_decimal(
                values.get("APMA_TARGET_AFTER_TAX_MARGIN", "0.15"),
                "APMA_TARGET_AFTER_TAX_MARGIN",
            ),
            income_tax_rate=_decimal(
                values.get("APMA_INCOME_TAX_RATE", "0.17"),
                "APMA_INCOME_TAX_RATE",
            ),
            usd_to_sgd_rate=_decimal(
                values.get("APMA_USD_TO_SGD_RATE", "1.40"),
                "APMA_USD_TO_SGD_RATE",
            ),
            provider_contingency_rate=_decimal(
                values.get("APMA_PROVIDER_CONTINGENCY_RATE", "0.25"),
                "APMA_PROVIDER_CONTINGENCY_RATE",
            ),
            qwen_price_per_audio_minute_usd=_decimal(
                values.get("QWEN_FILETRANS_PRICE_PER_MINUTE_USD", "0.0021"),
                "QWEN_FILETRANS_PRICE_PER_MINUTE_USD",
            ),
            openai_price_per_audio_minute_usd=_decimal(
                values.get("OPENAI_PRICE_GPT_TRANSCRIBE", "0.0045"),
                "OPENAI_PRICE_GPT_TRANSCRIBE",
            ),
            reviewer_cost_per_hour_sgd=_decimal(
                values.get("APMA_REVIEWER_COST_PER_HOUR_SGD", "35.00"),
                "APMA_REVIEWER_COST_PER_HOUR_SGD",
            ),
        )
        validate_config(cfg)
        return cfg


def validate_config(cfg: CommercialPricingConfig) -> None:
    if cfg.currency != "SGD":
        raise CommercialPricingError("The pilot price book supports SGD only")
    if not cfg.price_book_version or len(cfg.price_book_version) > 80:
        raise CommercialPricingError("price_book_version is required")
    if not 1 <= cfg.quote_valid_minutes <= 1440:
        raise CommercialPricingError("quote_valid_minutes must be between 1 and 1440")
    for name, rate in {
        "gst_rate": cfg.gst_rate,
        "target_after_tax_margin": cfg.target_after_tax_margin,
        "income_tax_rate": cfg.income_tax_rate,
        "provider_contingency_rate": cfg.provider_contingency_rate,
        "stripe_card_variable_rate": cfg.stripe_card_variable_rate,
        "stripe_paynow_variable_rate": cfg.stripe_paynow_variable_rate,
    }.items():
        if rate < ZERO or rate >= ONE:
            raise CommercialPricingError(f"{name} must be at least 0 and below 1")
    if cfg.usd_to_sgd_rate <= ZERO:
        raise CommercialPricingError("usd_to_sgd_rate must be positive")
    if cfg.review_minimum_minutes < 1:
        raise CommercialPricingError("review_minimum_minutes must be positive")

    nonnegative_fields = {
        "qwen_price_per_audio_minute_usd": cfg.qwen_price_per_audio_minute_usd,
        "openai_price_per_audio_minute_usd": cfg.openai_price_per_audio_minute_usd,
        "draft_list_price_per_audio_hour_sgd": cfg.draft_list_price_per_audio_hour_sgd,
        "draft_minimum_order_sgd": cfg.draft_minimum_order_sgd,
        "draft_infrastructure_per_audio_hour_sgd": cfg.draft_infrastructure_per_audio_hour_sgd,
        "draft_support_reserve_per_order_sgd": cfg.draft_support_reserve_per_order_sgd,
        "draft_product_operations_reserve_per_order_sgd": cfg.draft_product_operations_reserve_per_order_sgd,
        "compare_list_price_per_audio_hour_sgd": cfg.compare_list_price_per_audio_hour_sgd,
        "compare_minimum_order_sgd": cfg.compare_minimum_order_sgd,
        "compare_infrastructure_per_audio_hour_sgd": cfg.compare_infrastructure_per_audio_hour_sgd,
        "compare_support_reserve_per_order_sgd": cfg.compare_support_reserve_per_order_sgd,
        "compare_product_operations_reserve_per_order_sgd": cfg.compare_product_operations_reserve_per_order_sgd,
        "review_list_price_per_active_minute_sgd": cfg.review_list_price_per_active_minute_sgd,
        "reviewer_cost_per_hour_sgd": cfg.reviewer_cost_per_hour_sgd,
        "review_platform_per_hour_sgd": cfg.review_platform_per_hour_sgd,
        "review_support_reserve_per_order_sgd": cfg.review_support_reserve_per_order_sgd,
        "review_product_operations_reserve_per_order_sgd": cfg.review_product_operations_reserve_per_order_sgd,
        "stripe_card_fixed_fee_sgd": cfg.stripe_card_fixed_fee_sgd,
        "stripe_paynow_fixed_fee_sgd": cfg.stripe_paynow_fixed_fee_sgd,
    }
    for name, value in nonnegative_fields.items():
        if value < ZERO:
            raise CommercialPricingError(f"{name} must be non-negative")

    for payment_method in PAYMENT_METHODS:
        variable_rate, _ = _payment_fees(cfg, payment_method)
        denominator = (
            ONE
            - variable_rate * (ONE + (cfg.gst_rate if cfg.gst_registered else ZERO))
            - cfg.target_after_tax_margin / (ONE - cfg.income_tax_rate)
        )
        if denominator <= ZERO:
            raise CommercialPricingError(
                "Payment, tax, and margin assumptions leave no valid selling price"
            )


def _payment_fees(
    cfg: CommercialPricingConfig, payment_method: str
) -> tuple[Decimal, Decimal]:
    if payment_method == PAYMENT_CARD:
        return cfg.stripe_card_variable_rate, cfg.stripe_card_fixed_fee_sgd
    if payment_method == PAYMENT_PAYNOW:
        return cfg.stripe_paynow_variable_rate, cfg.stripe_paynow_fixed_fee_sgd
    raise CommercialPricingError(f"Unsupported payment method: {payment_method}")


def _ceil_positive(value: Any, field_name: str) -> int:
    parsed = _decimal(value, field_name)
    if parsed <= ZERO:
        raise CommercialPricingError(f"{field_name} must be positive")
    return int(parsed.to_integral_value(rounding=ROUND_CEILING))


def _audio_plan_costs(
    cfg: CommercialPricingConfig, plan_id: str, billed_audio_minutes: int
) -> tuple[Decimal, dict[str, Decimal], list[str]]:
    audio_hours = Decimal(billed_audio_minutes) / Decimal(60)
    if plan_id == PLAN_DIALECT_DRAFT:
        provider_usd = (
            Decimal(billed_audio_minutes) * cfg.qwen_price_per_audio_minute_usd
        )
        list_price = max(
            cfg.draft_minimum_order_sgd,
            audio_hours * cfg.draft_list_price_per_audio_hour_sgd,
        )
        infrastructure = audio_hours * cfg.draft_infrastructure_per_audio_hour_sgd
        support = cfg.draft_support_reserve_per_order_sgd
        product_operations = cfg.draft_product_operations_reserve_per_order_sgd
        provider_routes = ["qwen_filetrans"]
    elif plan_id == PLAN_COMPARE_AND_FLAG:
        provider_usd = Decimal(billed_audio_minutes) * (
            cfg.qwen_price_per_audio_minute_usd
            + cfg.openai_price_per_audio_minute_usd
        )
        list_price = max(
            cfg.compare_minimum_order_sgd,
            audio_hours * cfg.compare_list_price_per_audio_hour_sgd,
        )
        infrastructure = audio_hours * cfg.compare_infrastructure_per_audio_hour_sgd
        support = cfg.compare_support_reserve_per_order_sgd
        product_operations = cfg.compare_product_operations_reserve_per_order_sgd
        provider_routes = ["qwen_filetrans", "gpt_transcribe"]
    else:
        raise CommercialPricingError(f"Unsupported audio plan: {plan_id}")

    provider = provider_usd * cfg.usd_to_sgd_rate
    contingency = provider * cfg.provider_contingency_rate
    return list_price, {
        "provider_estimate_sgd": provider,
        "provider_contingency_sgd": contingency,
        "infrastructure_storage_sgd": infrastructure,
        "support_exception_reserve_sgd": support,
        "product_operations_reserve_sgd": product_operations,
        "reviewer_labor_sgd": ZERO,
    }, provider_routes


def _review_plan_costs(
    cfg: CommercialPricingConfig, requested_review_minutes: Any
) -> tuple[int, Decimal, dict[str, Decimal], list[str]]:
    requested = _ceil_positive(requested_review_minutes, "requested_review_minutes")
    billed = max(requested, cfg.review_minimum_minutes)
    review_hours = Decimal(billed) / Decimal(60)
    list_price = Decimal(billed) * cfg.review_list_price_per_active_minute_sgd
    return billed, list_price, {
        "provider_estimate_sgd": ZERO,
        "provider_contingency_sgd": ZERO,
        "infrastructure_storage_sgd": review_hours * cfg.review_platform_per_hour_sgd,
        "support_exception_reserve_sgd": cfg.review_support_reserve_per_order_sgd,
        "product_operations_reserve_sgd": cfg.review_product_operations_reserve_per_order_sgd,
        "reviewer_labor_sgd": review_hours * cfg.reviewer_cost_per_hour_sgd,
    }, []


def _price_floor(
    *,
    delivery_cost: Decimal,
    variable_payment_rate: Decimal,
    fixed_payment_fee: Decimal,
    cfg: CommercialPricingConfig,
) -> Decimal:
    gst_rate = cfg.gst_rate if cfg.gst_registered else ZERO
    denominator = (
        ONE
        - variable_payment_rate * (ONE + gst_rate)
        - cfg.target_after_tax_margin / (ONE - cfg.income_tax_rate)
    )
    return (delivery_cost + fixed_payment_fee) / denominator


def quote_commercial_order(
    *,
    plan_id: str,
    payment_method: str,
    duration_seconds: Optional[Any] = None,
    requested_review_minutes: Optional[Any] = None,
    config: Optional[CommercialPricingConfig] = None,
    now: Optional[datetime] = None,
    quote_id: Optional[str] = None,
) -> dict[str, Any]:
    """Calculate a privacy-minimised commercial quote with a hard margin floor."""

    cfg = config or CommercialPricingConfig()
    validate_config(cfg)
    if plan_id not in PLAN_IDS:
        raise CommercialPricingError(f"Unsupported plan: {plan_id}")
    if payment_method not in PAYMENT_METHODS:
        raise CommercialPricingError(f"Unsupported payment method: {payment_method}")

    billed_audio_minutes: Optional[int] = None
    billed_review_minutes: Optional[int] = None
    requested_review_minutes_rounded: Optional[int] = None
    if plan_id == PLAN_HUMAN_REVIEW:
        if requested_review_minutes is None:
            raise CommercialPricingError("requested_review_minutes is required")
        requested_review_minutes_rounded = _ceil_positive(
            requested_review_minutes, "requested_review_minutes"
        )
        billed_review_minutes, list_price, cost_parts, provider_routes = (
            _review_plan_costs(cfg, requested_review_minutes_rounded)
        )
    else:
        if duration_seconds is None:
            raise CommercialPricingError("duration_seconds is required")
        seconds = _decimal(duration_seconds, "duration_seconds")
        if seconds <= ZERO:
            raise CommercialPricingError("duration_seconds must be positive")
        billed_audio_minutes = int(
            (seconds / Decimal(60)).to_integral_value(rounding=ROUND_CEILING)
        )
        list_price, cost_parts, provider_routes = _audio_plan_costs(
            cfg, plan_id, billed_audio_minutes
        )

    delivery_cost = sum(cost_parts.values(), ZERO)
    variable_rate, fixed_fee = _payment_fees(cfg, payment_method)
    floor_unrounded = _price_floor(
        delivery_cost=delivery_cost,
        variable_payment_rate=variable_rate,
        fixed_payment_fee=fixed_fee,
        cfg=cfg,
    )
    pre_gst_price = _money_up(max(list_price, floor_unrounded))
    gst_rate = cfg.gst_rate if cfg.gst_registered else ZERO

    def realised_values(price: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
        tax_amount = _money(price * gst_rate)
        total = price + tax_amount
        fee = _money(total * variable_rate + fixed_fee)
        pretax = price - delivery_cost - fee
        after_tax = (
            pretax * (ONE - cfg.income_tax_rate) if pretax >= ZERO else pretax
        )
        margin = after_tax / price
        return tax_amount, total, fee, pretax, margin

    gst_amount, customer_total, payment_fee, pretax_profit, after_tax_margin = (
        realised_values(pre_gst_price)
    )
    # Stripe and GST settle in cents. A rounded fee can put the realised margin
    # a fraction below the algebraic floor, so raise by cents until the actual
    # quoted amounts meet the policy.
    for _ in range(100):
        if after_tax_margin >= cfg.target_after_tax_margin:
            break
        pre_gst_price += CENT
        gst_amount, customer_total, payment_fee, pretax_profit, after_tax_margin = (
            realised_values(pre_gst_price)
        )
    if after_tax_margin < cfg.target_after_tax_margin:
        raise CommercialPricingError("Calculated price does not meet the margin floor")
    after_tax_profit = (
        pretax_profit * (ONE - cfg.income_tax_rate)
        if pretax_profit >= ZERO
        else pretax_profit
    )

    created = now or datetime.now(timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    created = created.astimezone(timezone.utc)
    expires = created + timedelta(minutes=cfg.quote_valid_minutes)

    calculation = {
        "price_book_version": cfg.price_book_version,
        "plan_id": plan_id,
        "payment_method": payment_method,
        "currency": cfg.currency,
        "billed_audio_minutes": billed_audio_minutes,
        "requested_review_minutes": requested_review_minutes_rounded,
        "billed_review_minutes": billed_review_minutes,
        "provider_routes": provider_routes,
        "list_price_pre_gst_sgd": _format(list_price),
        "price_floor_pre_gst_sgd": _format(floor_unrounded),
        "price_pre_gst_sgd": _format(pre_gst_price),
        "gst_amount_sgd": _format(gst_amount),
        "customer_total_sgd": _format(customer_total),
        "costs": {key: _format(value) for key, value in cost_parts.items()},
        "delivery_cost_sgd": _format(delivery_cost),
        "payment_fee_estimate_sgd": _format(payment_fee),
        "pretax_operating_profit_sgd": _format(pretax_profit),
        "after_tax_operating_profit_sgd": _format(after_tax_profit),
        "after_tax_margin": _format(after_tax_margin, FOUR_PLACES),
        "target_after_tax_margin": _format(
            cfg.target_after_tax_margin, FOUR_PLACES
        ),
        "income_tax_rate_assumption": _format(cfg.income_tax_rate, FOUR_PLACES),
        "gst_registered": cfg.gst_registered,
        "gst_rate": _format(gst_rate, FOUR_PLACES),
        "usd_to_sgd_rate_assumption": _format(cfg.usd_to_sgd_rate, FOUR_PLACES),
    }
    canonical = json.dumps(
        calculation, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    calculation_hash = hashlib.sha256(canonical).hexdigest()

    return {
        "schema_version": QUOTE_SCHEMA_VERSION,
        "quote_id": quote_id or f"quote_{uuid.uuid4().hex}",
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
        "status": "quoted_not_paid",
        "calculation_hash_sha256": calculation_hash,
        "calculation": calculation,
        "disclosures": [
            "AI output is not a human accuracy guarantee.",
            "Corporate income tax is a conservative pricing assumption, not tax advice.",
            "Temporary grants and free provider quotas are excluded from the price floor.",
        ],
    }


def with_overrides(
    cfg: CommercialPricingConfig, **changes: Any
) -> CommercialPricingConfig:
    """Typed convenience wrapper used by operators and tests."""

    updated = replace(cfg, **changes)
    validate_config(updated)
    return updated
