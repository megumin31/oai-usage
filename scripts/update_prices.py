#!/usr/bin/env python3
"""Sync supported Standard text-token prices from OpenAI's public pricing page."""
from __future__ import annotations

import argparse
import json
import re
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "prices.json"
PRICING_URL = "https://developers.openai.com/api/docs/pricing.md"
MODEL_URL = "https://developers.openai.com/api/docs/models/{}.md"
TABLE_MARKER = "### Standard pricing data"
MODEL_ID = re.compile(r"gpt-(\d+)(?:\.(\d+))?(?:-(?:[a-z][a-z0-9-]*|\d{4}-\d{2}-\d{2}|\d{8}))?\Z")
ALIAS_SUFFIX = re.compile(r"-(?:preview|latest|\d{4}-\d{2}-\d{2}|\d{8})\Z")
LONG_RULE = re.compile(r"(?:more than|>)\s*(\d+)K input tokens.*full (request|session)", re.I)
HEADERS = ["Model", "Short context input", "Short context cached input", "Short context cache writes",
           "Short context output", "Long context input", "Long context cached input",
           "Long context cache writes", "Long context output"]


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "oai-usage-price-sync/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError(f"Official page is too large: {url}")
    return data.decode("utf-8")


def cells(line: str) -> list[str]:
    return [part.strip() for part in line.strip().strip("|").split("|")]


def rate(value: str) -> Optional[Decimal]:
    if value == "-":
        return None
    if not re.fullmatch(r"\$\d+(?:\.\d+)?", value):
        raise ValueError(f"Unexpected price cell: {value!r}")
    number = Decimal(value[1:])
    if number > Decimal("1e9"):
        raise ValueError(f"Price is out of range: {value!r}")
    return number


def standard_rows(markdown: str) -> list[list[str]]:
    if TABLE_MARKER not in markdown:
        raise ValueError("Standard pricing table is missing")
    section = markdown.split(TABLE_MARKER, 1)[1].splitlines()
    start = next((i for i, line in enumerate(section) if line.startswith("| Model |")), None)
    if start is None or cells(section[start]) != HEADERS:
        raise ValueError("Standard pricing table columns changed")
    if start + 1 >= len(section) or not section[start + 1].startswith("| ---"):
        raise ValueError("Standard pricing table separator is missing")
    rows = []
    for line in section[start + 2:]:
        if not line.startswith("|"):
            break
        row = cells(line)
        if len(row) != len(HEADERS):
            raise ValueError(f"Unexpected Standard pricing row: {line!r}")
        rows.append(row)
    if not rows:
        raise ValueError("Standard pricing table is empty")
    return rows


def long_context(model: str, base: list[Optional[Decimal]], long: list[Optional[Decimal]],
                 fetch_page=fetch) -> Optional[dict]:
    if all(value is None for value in long):
        return None
    if any(long[i] is None for i in (0, 1, 3)) or (base[2] is None) != (long[2] is None):
        raise ValueError(f"Incomplete long-context prices for {model}")
    page = fetch_page(MODEL_URL.format(model))
    matches = [LONG_RULE.search(line) for line in page.splitlines()]
    matches = [match for match in matches if match]
    if len({(match.group(1), match.group(2).lower()) for match in matches}) != 1:
        raise ValueError(f"Long-context threshold or scope is unclear for {model}")
    match = matches[0]
    return {"threshold": int(match.group(1)) * 1000, "scope": match.group(2).lower(),
            "input": str(long[0]), "cached_input": str(long[1]),
            "cache_write": str(long[2]) if long[2] is not None else None,
            "output": str(long[3])}


def collect(markdown: str, fetch_page=fetch) -> dict[str, dict]:
    models = {}
    for row in standard_rows(markdown):
        model = re.sub(r"\s+\([^)]*\)\Z", "", row[0])
        match = MODEL_ID.fullmatch(model)
        if not match or (int(match.group(1)), int(match.group(2) or 0)) < (5, 4):
            continue
        amounts = [rate(cell) for cell in row[1:]]
        base, long = amounts[:4], amounts[4:]
        if any(base[i] is None for i in (0, 1, 3)):
            continue  # This calculator requires input, cached-input, and output prices.
        rule = long_context(model, base, long, fetch_page)
        canonical = ALIAS_SUFFIX.sub("", model)
        entry = {
            "input": str(base[0]), "cached_input": str(base[1]),
            "cache_write": str(base[2]) if base[2] is not None else None,
            "output": str(base[3]), "long_context": rule,
            "source": MODEL_URL.format(model).removesuffix(".md") if rule else PRICING_URL.removesuffix(".md"),
        }
        previous = models.get(canonical)
        if previous is not None and any(previous[key] != entry[key]
                                        for key in ("input", "cached_input", "cache_write", "output", "long_context")):
            raise ValueError(f"Conflicting Standard prices for aliases of {canonical}")
        if previous is None or model == canonical:
            models[canonical] = entry
    if not {"gpt-6-astra", "gpt-6-sol", "gpt-6-luna"} <= models.keys():
        raise ValueError("Expected flagship models are missing from the Standard table")
    return models


def updated_catalog(old: dict, observed: dict[str, dict], today: str) -> dict:
    if old and (old.get("schema_version") != 1 or old.get("basis") != "standard_api_equivalent"):
        raise ValueError("Existing price catalog has an unexpected schema or basis")
    previous = old.get("models", {})
    if not isinstance(previous, dict):
        raise ValueError("Existing catalog has invalid models")
    models = {**previous, **observed}
    if models == previous:
        return old
    return {"schema_version": 1, "basis": "standard_api_equivalent",
            "verified_at": today, "source": PRICING_URL.removesuffix(".md"),
            "models": dict(sorted(models.items()))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Check official prices without writing prices.json")
    args = parser.parse_args()
    old = json.loads(CATALOG.read_text()) if CATALOG.exists() else {}
    observed = collect(fetch(PRICING_URL))
    today = datetime.now(timezone.utc).date().isoformat()
    new = updated_catalog(old, observed, today)
    changed = sorted(model for model, value in new["models"].items()
                     if value != old.get("models", {}).get(model))
    if changed:
        print("Updated models: " + ", ".join(changed))
        if not args.dry_run:
            CATALOG.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n")
    else:
        print("No price changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
