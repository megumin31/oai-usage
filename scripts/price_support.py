"""Bounded upstream downloads and catalog checks for price maintenance."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from http.client import HTTPException
from pathlib import Path
from typing import Any, Optional

UTC = timezone.utc
MODELS_DEV_URL = "https://models.dev/api.json"
MODEL_LIST_URL = "https://developers.openai.com/api/docs/models/all"
MAX_BYTES = 1_000_000
RATE = re.compile(r"[0-9]{1,10}(?:\.[0-9]{1,12})?\Z")


@dataclass(frozen=True)
class Price:
    input: Decimal
    cached: Optional[Decimal]
    output: Decimal
    write: Optional[Decimal] = None
    long_threshold: Optional[int] = None
    source: str = "custom"
    long_input: Optional[Decimal] = None
    long_cached: Optional[Decimal] = None
    long_write: Optional[Decimal] = None
    long_output: Optional[Decimal] = None
    model_source: Optional[str] = None
    rule_source: Optional[str] = None


@dataclass(frozen=True)
class PriceCatalog:
    prices: dict
    verified_at: str
    origin: str


class DownloadError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DownloadError("Price download redirects are not allowed")


def validate_download_url(url: str) -> None:
    if url not in (MODELS_DEV_URL, MODEL_LIST_URL + ".md"):
        raise DownloadError("Price download URL is not allowed")


def download_budget(url: str) -> int:
    validate_download_url(url)
    return 8_000_000 if url == MODELS_DEV_URL else 2_000_000


def _download(url: str, limit: int, socket_timeout: float) -> bytes:
    validate_download_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "oai-usage-prices/1.0", "Accept-Encoding": "identity"})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=socket_timeout) as response:
        if response.status != 200 or response.geturl() != url:
            raise DownloadError("Unexpected price download response")
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise DownloadError("Compressed price responses are not accepted")
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isdecimal() or len(length) > 10 or int(length) > limit):
            raise DownloadError("Price response is too large or has invalid length")
        data = bytearray()
        while len(data) <= limit:
            chunk = response.read(min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > limit or (length is not None and len(data) != int(length)):
            raise DownloadError("Price response is oversized or incomplete")
        return bytes(data)


def fetch_https(url: str, limit: int = MAX_BYTES, total_timeout: float = 20,
                socket_timeout: float = 5) -> bytes:
    """Download from an allowed upstream in a worker with a total deadline."""
    if (type(limit) is not int or not 0 < limit <= download_budget(url) or
            not 0 < total_timeout <= 20 or not 0 < socket_timeout <= min(5, total_timeout)):
        raise ValueError("Invalid price download limits")
    command = [sys.executable, str(Path(__file__).resolve()), "--download", url, str(limit), str(socket_timeout)]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=total_timeout)
    except subprocess.TimeoutExpired:
        raise DownloadError("Price download exceeded its total time limit")
    except OSError:
        raise DownloadError("Price download worker could not start")
    if result.returncode or len(result.stdout) > limit:
        raise DownloadError("Price download failed")
    return result.stdout


def strict_json(data: bytes, limit: int = MAX_BYTES, max_depth: int = 8,
                decimal_numbers: bool = False, max_string: int = 4096) -> Any:
    if len(data) > limit:
        raise ValueError("Price data is too large")
    text = data.decode("utf-8")
    depth, quoted, escaped, width = 0, False, False, 0
    for char in text:
        if quoted:
            width += 1
            if width > max_string:
                raise ValueError("Price JSON string is too long")
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted, width = True, 0
        elif char in "[{":
            depth += 1
            if depth > max_depth:
                raise ValueError("Price JSON is nested too deeply")
        elif char in "]}":
            depth -= 1

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate price JSON key")
            result[key] = value
        return result

    def integer(value):
        if len(value) > 12:
            raise ValueError("Price JSON integer is too long")
        return int(value)

    def reject_number(value):
        raise ValueError("Price JSON rates must be decimal strings")

    def decimal_number(value):
        if len(value) > 40:
            raise ValueError("Price JSON number is too long")
        number = Decimal(value)
        if not number.is_finite() or abs(number.adjusted()) > 100 or number.as_tuple().exponent < -100:
            raise ValueError("Price JSON number is out of range")
        return number

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_int=integer,
                          parse_float=decimal_number if decimal_numbers else reject_number,
                          parse_constant=reject_number)
    except RecursionError:
        raise ValueError("Price JSON is nested too deeply")


def exact_keys(value: Any, expected: set) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Unexpected price data fields")


def parse_price_catalog(raw: Any, origin: str, today: Optional[date] = None) -> PriceCatalog:
    exact_keys(raw, {"basis", "verified_at", "source", "models", "provider", "model_source"})
    if raw["basis"] != "standard_api_equivalent":
        raise ValueError("Invalid price catalog basis")
    if raw["provider"] != "openai" or raw["model_source"] != MODEL_LIST_URL:
        raise ValueError("Invalid price catalog model provider")
    if raw["source"] != MODELS_DEV_URL:
        raise ValueError("Invalid price catalog upstream source")
    verified_at = raw["verified_at"]
    today = today or datetime.now(UTC).date()
    try:
        if not isinstance(verified_at, str) or len(verified_at) != 10:
            raise ValueError()
        verified = date.fromisoformat(verified_at)
        if verified.isoformat() != verified_at or verified > today:
            raise ValueError()
    except ValueError:
        raise ValueError("Invalid or future price verification date")

    def amount(value, optional=False):
        if value is None and optional:
            return None
        if not isinstance(value, str) or len(value) > 24 or not RATE.fullmatch(value):
            raise ValueError("Invalid price catalog rate")
        number = Decimal(value)
        if number > Decimal("1e9"):
            raise ValueError("Price catalog rate is out of range")
        return number

    models = raw["models"]
    if not isinstance(models, dict) or not 1 <= len(models) <= 200:
        raise ValueError("Invalid price catalog models")
    prices = {}
    for model, row in models.items():
        if not isinstance(model, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", model):
            raise ValueError("Invalid price catalog model")
        exact_keys(row, {"input", "cached_input", "cache_write", "output", "long_context", "source", "model_source"})
        expected_source = "https://developers.openai.com/api/docs/models/" + model
        if row["model_source"] != expected_source:
            raise ValueError("Invalid model identity source")
        if row["source"] != MODELS_DEV_URL:
            raise ValueError("Invalid model price source")
        long = row["long_context"]
        threshold = None
        if long is not None:
            exact_keys(long, {"threshold", "input", "cached_input", "cache_write", "output", "source"})
            threshold = long["threshold"]
            if type(threshold) is not int or not 0 < threshold <= 10_000_000:
                raise ValueError("Invalid long-context rule")
            if long["source"] != MODELS_DEV_URL:
                raise ValueError("Invalid long-context rule source")
        write = amount(row["cache_write"], True)
        long_write = amount(long["cache_write"], True) if long else None
        if long and (write is None) != (long_write is None):
            raise ValueError("Inconsistent cache-write rates")
        prices[model] = Price(amount(row["input"]), amount(row["cached_input"], True), amount(row["output"]),
                              write, threshold, row["source"],
                              amount(long["input"]) if long else None,
                              amount(long["cached_input"], True) if long else None,
                              long_write, amount(long["output"]) if long else None,
                              row["model_source"], long["source"] if long else None)
    return PriceCatalog(prices, verified_at, origin)


def read_limited(path: Path, limit: int = MAX_BYTES) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Price file is too large")
    return data


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".prices-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def download_main() -> None:
    if len(sys.argv) != 5 or sys.argv[1] != "--download":
        raise SystemExit("This module is a price-maintenance download worker")
    try:
        download_limit, read_timeout = int(sys.argv[3]), float(sys.argv[4])
        if not 0 < download_limit <= download_budget(sys.argv[2]) or not 0 < read_timeout <= 5:
            raise ValueError("Invalid download limits")
        sys.stdout.buffer.write(_download(sys.argv[2], download_limit, read_timeout))
    except (OSError, ValueError, HTTPException):
        raise SystemExit("Price download failed")


if __name__ == "__main__":
    download_main()
