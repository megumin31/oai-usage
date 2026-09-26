#!/usr/bin/env python3
"""Sync OpenAI text prices using official model IDs and models.dev prices."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import oai_price_catalog as catalog

CATALOG = ROOT / "prices.json"
RULES = ROOT / "scripts" / "pricing_rules.json"
MODELS_URL = catalog.MODEL_LIST_URL + ".md"
PRICES_URL = catalog.MODELS_DEV_URL
MODEL_URL = "https://developers.openai.com/api/docs/models/{}"
MODEL_ID = re.compile(r"gpt-(\d{1,3})(?:\.(\d{1,3}))?(?:-[a-z][a-z0-9-]*)?\Z")
MODEL_LINK = re.compile(r"\[[^\]\n]+\]\((?:https://developers\.openai\.com)?/api/docs/models/([a-z0-9][a-z0-9.-]{0,79}?)\)")
RATE_FIELDS = {"input": "input", "cached_input": "cache_read", "cache_write": "cache_write", "output": "output"}
COST_FIELDS = {"input", "output", "cache_read", "cache_write", "tiers", "context_over_200k"}


class UnsupportedPrice(ValueError):
    """A discovered model needs a reviewed rule before it can be priced."""


def supported_id(model: str) -> bool:
    match = MODEL_ID.fullmatch(model)
    return bool(match and (int(match[1]), int(match[2] or 0)) >= (5, 4))


def fetch(url: str) -> bytes:
    return catalog.fetch_https(url, limit=catalog.download_budget(url), total_timeout=20, socket_timeout=5)


def official_models(markdown: str) -> set[str]:
    if len(markdown.encode("utf-8")) > 2_000_000 or any(len(line) > 8192 for line in markdown.splitlines()):
        raise ValueError("Official model directory is too large")
    models = {match[1].removesuffix(".md") for match in MODEL_LINK.finditer(markdown)} - {"all", "compare"}
    if not models or len(models) > 1000 or not any(supported_id(model) for model in models):
        raise ValueError("Official model directory contains no supported model IDs or changed format")
    return models


def upstream_models(data: bytes) -> dict:
    raw = catalog.strict_json(data, limit=8_000_000, max_depth=16, decimal_numbers=True, max_string=16384)
    provider = raw.get("openai") if isinstance(raw, dict) else None
    models = provider.get("models") if isinstance(provider, dict) else None
    if not isinstance(models, dict) or not 1 <= len(models) <= 1000:
        raise ValueError("models.dev is missing the OpenAI model catalog")
    return models


def load_rules() -> dict:
    raw = catalog.strict_json(catalog.read_limited(RULES))
    catalog.exact_keys(raw, {"schema_version", "models"})
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1 or not isinstance(raw["models"], dict):
        raise ValueError("Invalid local pricing rules")
    for model, rule in raw["models"].items():
        catalog.exact_keys(rule, {"threshold", "scope", "source"})
        if (not supported_id(model) or type(rule["threshold"]) is not int or
                not 0 < rule["threshold"] <= 10_000_000 or rule["scope"] not in ("request", "session") or
                rule["source"] != MODEL_URL.format(model)):
            raise ValueError("Invalid local long-context rule")
    return raw["models"]


def rate(value) -> str:
    if type(value) not in (int, Decimal):
        raise ValueError("Upstream price must be a JSON number")
    number = Decimal(value)
    if not number.is_finite() or not 0 <= number <= Decimal("1e9") or number.as_tuple().exponent < -12:
        raise ValueError("Upstream price is out of range")
    return format(number.normalize(), "f")


def rates(cost: dict) -> dict:
    if any(cost.get(field) is None for field in ("input", "cache_read", "output")):
        raise UnsupportedPrice("input, cached-input, or output price is unavailable")
    return {target: rate(cost[field]) if cost.get(field) is not None else None
            for target, field in RATE_FIELDS.items()}


def model_price(model: str, row: dict, rules: dict) -> dict:
    if not isinstance(row, dict) or row.get("id") != model:
        raise ValueError("models.dev OpenAI model ID does not match its key")
    modalities = row.get("modalities", {})
    if (not isinstance(modalities, dict) or modalities.get("output") != ["text"] or
            not isinstance(modalities.get("input"), list) or "text" not in modalities["input"] or
            any(item not in ("text", "image", "pdf") for item in modalities["input"])):
        raise UnsupportedPrice("unsupported modalities for text-token accounting")
    cost = row.get("cost")
    if not isinstance(cost, dict):
        raise UnsupportedPrice("price data is unavailable")
    if set(cost) - COST_FIELDS:
        raise UnsupportedPrice("unsupported pricing dimensions; review the upstream schema")
    base = rates(cost)
    tiers = cost.get("tiers", [])
    if not isinstance(tiers, list):
        raise ValueError("Invalid price tiers")
    rule = rules.get(model)
    long = None
    if not tiers:
        if rule or "context_over_200k" in cost:
            raise UnsupportedPrice("explicit context tier is required")
    else:
        if len(tiers) != 1:
            raise UnsupportedPrice("multiple pricing tiers need explicit support")
        tier = tiers[0]
        if not isinstance(tier, dict) or set(tier) - (set(RATE_FIELDS.values()) | {"tier"}):
            raise UnsupportedPrice("unsupported context-tier price fields")
        metadata = tier.get("tier")
        if not isinstance(metadata, dict) or set(metadata) != {"type", "size"} or metadata["type"] != "context":
            raise UnsupportedPrice("unsupported tier type")
        threshold = metadata["size"]
        if type(threshold) is not int or not 0 < threshold <= 10_000_000:
            raise ValueError("Invalid context-tier threshold")
        if rule is None:
            raise UnsupportedPrice("long-context request/session rule needs official review in scripts/pricing_rules.json")
        if threshold != rule["threshold"]:
            raise ValueError(f"Review required: changed long-context threshold for {model}")
        long = {**rates(tier), **rule}
        if (base["cache_write"] is None) != (long["cache_write"] is None):
            raise ValueError("Inconsistent cache-write tier prices")
    return {**base, "long_context": long, "source": PRICES_URL, "model_source": MODEL_URL.format(model)}


def collect(markdown: str, data: bytes, rules: dict, warnings: Optional[list[str]] = None) -> dict[str, dict]:
    official = official_models(markdown)
    upstream = upstream_models(data)
    warnings = warnings if warnings is not None else []
    models = {}
    # Exact IDs prevent importing another provider's prices or guessing aliases.
    for model in sorted(official):
        if not supported_id(model):
            continue
        if model not in upstream:
            warnings.append(f"Pending {model}: no matching OpenAI price in models.dev.")
            continue
        try:
            models[model] = model_price(model, upstream[model], rules)
        except UnsupportedPrice as exc:
            warnings.append(f"Pending {model}: {exc}.")
    for model in sorted(upstream):
        if supported_id(model) and model not in official:
            warnings.append(f"Skipped {model}: not listed in the official model directory.")
    if not models:
        raise ValueError("No supported OpenAI models have complete prices and rules")
    return models


def updated_catalog(old: dict, observed: dict[str, dict], today: str) -> dict:
    catalog.parse_price_catalog(old, "baseline")
    previous = old["models"]
    models = {**previous, **observed}
    completely_checked = previous.keys() <= observed.keys()
    age = (date.fromisoformat(today) - date.fromisoformat(old["verified_at"])).days
    changed = models != previous
    if not changed and (age < 30 or not completely_checked):
        return old
    return {"schema_version": 2, "basis": "standard_api_equivalent", "provider": "openai",
            "verified_at": today if completely_checked else old["verified_at"], "source": PRICES_URL,
            "model_source": catalog.MODEL_LIST_URL, "models": dict(sorted(models.items()))}


def validate_transition(old: dict, new: dict, observed: Optional[set] = None) -> None:
    previous = catalog.parse_price_catalog(old, "baseline")
    incoming = catalog.parse_price_catalog(new, "candidate")
    if previous.prices.keys() - incoming.prices.keys():
        raise ValueError("Historical model prices must be retained")
    if observed is not None:
        missing = previous.prices.keys() - observed
        if len(missing) >= 2 and len(missing) * 4 >= len(previous.prices):
            raise ValueError("Review required: at least 25% of known models disappeared")
        if missing and new["verified_at"] != old["verified_at"]:
            raise ValueError("Unobserved prices cannot receive a fresh check date")
    rules = load_rules()
    rate_fields = ("input", "cached", "write", "output", "long_input", "long_cached", "long_write", "long_output")
    for model, price in incoming.prices.items():
        if not supported_id(model):
            raise ValueError("Unsupported model in OpenAI price catalog")
        if price.long_threshold:
            rule = rules.get(model)
            if rule is None or (price.long_threshold, price.long_scope, price.rule_source) != (rule["threshold"], rule["scope"], rule["source"]):
                raise ValueError(f"Review required: missing or conflicting local rule for {model}")
        prior = previous.prices.get(model)
        if prior is None:
            if any(getattr(price, name) == 0 for name in rate_fields):
                raise ValueError(f"Review required: zero price for new model {model}")
            continue
        if (price.long_threshold, price.long_scope) != (prior.long_threshold, prior.long_scope):
            raise ValueError(f"Review required: changed long-context rules for {model}")
        for name in rate_fields:
            before, after = getattr(prior, name), getattr(price, name)
            if (before is None) != (after is None):
                raise ValueError(f"Review required: changed availability of {model} {name} price")
            if before is None or before == after:
                continue
            if before == 0 or after == 0 or after > before * 3 or after * 3 < before:
                raise ValueError(f"Review required: unexpected change to {model} {name} price")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Check upstream sources without writing prices.json")
    parser.add_argument("--output", type=Path, default=CATALOG, help="Write the validated candidate to this path")
    parser.add_argument("--validate", type=Path, help="Validate a candidate without network access or writes")
    args = parser.parse_args()
    old = catalog.strict_json(catalog.read_limited(CATALOG))
    catalog.parse_price_catalog(old, "baseline")
    if args.validate:
        validate_transition(old, catalog.strict_json(catalog.read_limited(args.validate)))
        print("Price candidate is valid")
        return 0
    warnings = []
    observed = collect(fetch(MODELS_URL).decode("utf-8"), fetch(PRICES_URL), load_rules(), warnings)
    missing = old["models"].keys() - observed.keys()
    if missing:
        warnings.append("Retained unchecked models; source-check date will not advance: " + ", ".join(sorted(missing)))
    for warning in warnings:
        print("Price discovery note: " + warning, file=sys.stderr)
    today = datetime.now(timezone.utc).date().isoformat()
    new = updated_catalog(old, observed, today)
    validate_transition(old, new, set(observed))
    changed = sorted(model for model, value in new["models"].items() if value != old.get("models", {}).get(model))
    print("Updated models: " + ", ".join(changed) if changed else
          "Refreshed source-check date" if new != old else "No price changes")
    if not args.dry_run and (new != old or args.output != CATALOG):
        catalog.atomic_write(args.output, (json.dumps(new, ensure_ascii=False, indent=2) + "\n").encode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
