"""Parse Toobit GET /api/v1/exchangeInfo into SymbolMetadata."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from aegis.schemas.common import MarketType
from aegis.schemas.metadata import SymbolMetadata


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _filter_map(filters: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("filterType")): item for item in filters if "filterType" in item}


def parse_spot_symbol(raw: dict[str, Any]) -> SymbolMetadata:
    filters = list(raw.get("filters") or [])
    fmap = _filter_map(filters)
    price = fmap.get("PRICE_FILTER", {})
    lot = fmap.get("LOT_SIZE", {})
    notional = fmap.get("MIN_NOTIONAL", {})
    return SymbolMetadata(
        market_type=MarketType.SPOT,
        symbol=str(raw["symbol"]),
        status=str(raw.get("status", "")),
        base_asset=str(raw.get("baseAsset", "")),
        quote_asset=str(raw.get("quoteAsset", "")),
        base_asset_precision=raw.get("baseAssetPrecision"),
        quote_precision=raw.get("quotePrecision") or raw.get("quoteAssetPrecision"),
        tick_size=_decimal_or_none(price.get("tickSize")),
        step_size=_decimal_or_none(lot.get("stepSize")),
        min_qty=_decimal_or_none(lot.get("minQty")),
        min_notional=_decimal_or_none(notional.get("minNotional")),
        filters=filters,
        raw=raw,
    )


def parse_futures_contract(raw: dict[str, Any]) -> SymbolMetadata:
    filters = list(raw.get("filters") or [])
    fmap = _filter_map(filters)
    price = fmap.get("PRICE_FILTER", {})
    lot = fmap.get("LOT_SIZE", {})
    notional = fmap.get("MIN_NOTIONAL", {})
    return SymbolMetadata(
        market_type=MarketType.FUTURES,
        symbol=str(raw["symbol"]),
        status=str(raw.get("status", "")),
        base_asset=str(raw.get("baseAsset", "")),
        quote_asset=str(raw.get("quoteAsset", "")),
        base_asset_precision=raw.get("baseAssetPrecision"),
        quote_precision=raw.get("quoteAssetPrecision") or raw.get("quotePrecision"),
        tick_size=_decimal_or_none(price.get("tickSize")),
        step_size=_decimal_or_none(lot.get("stepSize")),
        min_qty=_decimal_or_none(lot.get("minQty")),
        min_notional=_decimal_or_none(notional.get("minNotional")),
        contract_multiplier=_decimal_or_none(raw.get("contractMultiplier")),
        inverse=bool(raw["inverse"]) if "inverse" in raw else None,
        filters=filters,
        raw=raw,
    )


def parse_exchange_info(payload: dict[str, Any]) -> list[SymbolMetadata]:
    symbols: list[SymbolMetadata] = []
    for item in payload.get("symbols") or []:
        symbols.append(parse_spot_symbol(item))
    for item in payload.get("contracts") or []:
        symbols.append(parse_futures_contract(item))
    return symbols
