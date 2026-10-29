from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from io import BytesIO
from typing import Any
import xml.etree.ElementTree as ET

import pandas as pd
import yfinance as yf

PRICE_DISPLAY_FORMAT = "%.4f"


@dataclass(frozen=True)
class Instrument:
    ticker: str
    isin: str
    exchange: str
    currency: str


@dataclass
class InstrumentResult:
    instrument: Instrument
    symbol: str | None
    lookup_method: str
    status: str
    prices: pd.DataFrame


def parse_instruments(frame: pd.DataFrame) -> list[Instrument]:
    required = ("ticker", "isin", "exchange", "currency")
    columns = {str(column).strip().lower(): column for column in frame.columns}
    missing = [column for column in required if column not in columns]
    if missing:
        raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")

    instruments: list[Instrument] = []
    for row_number, (_, row) in enumerate(frame.iterrows(), start=1):
        values = {
            field: "" if pd.isna(row[columns[field]]) else str(row[columns[field]]).strip()
            for field in required
        }
        if not any(values.values()):
            continue
        if not values["ticker"] and not values["isin"]:
            raise ValueError(f"Row {row_number} needs at least a ticker or an ISIN.")
        instruments.append(Instrument(**values))

    if not instruments:
        raise ValueError("Add at least one instrument with a ticker or an ISIN.")
    return instruments


def _find_symbol(isin: str) -> str:
    search = yf.Search(isin, max_results=10, news_count=0)
    quotes: list[dict[str, Any]] = search.quotes
    for quote in quotes:
        symbol = quote.get("symbol")
        if isinstance(symbol, str) and symbol.strip():
            return symbol.strip()
    raise LookupError(f"Yahoo Finance returned no ticker for ISIN {isin}.")


def _download_histories(
    symbols: list[str], start: date, end: date
) -> dict[str, pd.DataFrame]:
    unique_symbols = list(dict.fromkeys(symbols))
    if not unique_symbols:
        return {}

    # yfinance treats `end` as exclusive; include the date selected by the user.
    history = yf.download(
        tickers=unique_symbols,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        auto_adjust=False,
        actions=False,
        progress=False,
        group_by="ticker",
        multi_level_index=True,
    )
    if history is None or history.empty:
        return {}

    if not isinstance(history.columns, pd.MultiIndex):
        if len(unique_symbols) == 1:
            return {unique_symbols[0]: history}
        raise ValueError("Yahoo Finance returned an unexpected batch price format.")

    available_symbols = {
        str(symbol).casefold(): str(symbol)
        for symbol in history.columns.get_level_values(0).unique()
    }
    histories: dict[str, pd.DataFrame] = {}
    for symbol in unique_symbols:
        actual_symbol = available_symbols.get(symbol.casefold())
        if actual_symbol is None:
            continue
        prices = history.xs(actual_symbol, axis=1, level=0, drop_level=True)
        if not prices.empty:
            histories[symbol] = prices
    return histories


def fetch_instruments(
    instruments: list[Instrument], start: date, end: date
) -> list[InstrumentResult]:
    symbols: list[str] = []
    methods: list[str] = []
    messages: list[list[str]] = []
    fallback_symbols: list[str | None] = []

    for instrument in instruments:
        instrument_messages: list[str] = []
        symbol: str | None = None
        method = "Ticker"

        if instrument.isin:
            try:
                symbol = _find_symbol(instrument.isin)
                method = "ISIN lookup"
            except Exception as exc:
                instrument_messages.append(f"ISIN lookup failed: {exc}")

        if not symbol and instrument.ticker:
            symbol = instrument.ticker
            method = "Ticker fallback" if instrument.isin else "Ticker"

        symbols.append(symbol or "")
        methods.append(method)
        messages.append(instrument_messages)
        fallback_symbols.append(
            instrument.ticker
            if symbol and symbol != instrument.ticker and instrument.ticker
            else None
        )

    try:
        primary_histories = _download_histories(
            [symbol for symbol in symbols if symbol], start, end
        )
    except Exception as exc:
        primary_histories = {}
        for instrument_messages in messages:
            instrument_messages.append(f"Price download failed: {exc}")

    pending_fallbacks = [
        fallback
        for symbol, fallback in zip(symbols, fallback_symbols)
        if symbol and not _has_history(primary_histories.get(symbol)) and fallback
    ]
    fallback_histories: dict[str, pd.DataFrame] = {}
    if pending_fallbacks:
        try:
            fallback_histories = _download_histories(pending_fallbacks, start, end)
        except Exception as exc:
            for index, (symbol, fallback) in enumerate(zip(symbols, fallback_symbols)):
                if (
                    symbol
                    and fallback in pending_fallbacks
                    and not _has_history(primary_histories.get(symbol))
                ):
                    messages[index].append(f"Ticker fallback download failed: {exc}")

    results: list[InstrumentResult] = []
    metadata_by_symbol: dict[str, dict[str, Any]] = {}
    for index, instrument in enumerate(instruments):
        symbol = symbols[index] or None
        prices = primary_histories.get(symbol, pd.DataFrame()) if symbol else pd.DataFrame()
        fallback = fallback_symbols[index]
        if not _has_history(prices) and fallback:
            messages[index].append(
                f"No historical prices were returned for {symbol}."
            )
            fallback_prices = fallback_histories.get(fallback, pd.DataFrame())
            if _has_history(fallback_prices):
                symbol = fallback
                prices = fallback_prices
                methods[index] = "Ticker fallback"
            elif not any("fallback download failed" in message for message in messages[index]):
                messages[index].append(
                    f"No historical prices were returned for {fallback}."
                )
        elif not _has_history(prices) and symbol:
            messages[index].append(
                f"No historical prices were returned for {symbol}."
            )

        if symbol and (not instrument.exchange or not instrument.currency):
            try:
                cache_key = symbol.casefold()
                if cache_key not in metadata_by_symbol:
                    metadata_by_symbol[cache_key] = yf.Ticker(symbol).get_info()
                metadata = metadata_by_symbol[cache_key]
            except Exception as exc:
                messages[index].append(f"Instrument metadata lookup failed: {exc}")
            else:
                instrument = replace(
                    instrument,
                    exchange=instrument.exchange or _metadata_text(metadata.get("exchange")),
                    currency=instrument.currency or _metadata_text(metadata.get("currency")),
                )

        results.append(
            InstrumentResult(
                instrument=instrument,
                symbol=symbol,
                lookup_method=methods[index],
                status="; ".join(messages[index]) or "Downloaded",
                prices=prices if _has_history(prices) else pd.DataFrame(),
            )
        )
    return results


def _has_history(prices: pd.DataFrame | None) -> bool:
    return (
        prices is not None
        and not prices.empty
        and "Close" in prices.columns
        and prices["Close"].notna().any()
    )


def _metadata_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def build_price_table(results: list[InstrumentResult]) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for result in results:
        if result.prices.empty:
            continue
        history = result.prices.copy()
        history.index = pd.to_datetime(history.index).strftime("%Y-%m-%d")
        history.index.name = "Date"
        history = history.reset_index()
        history.insert(1, "ISIN", result.instrument.isin)
        history.insert(2, "Ticker", result.instrument.ticker)
        history.insert(3, "Yahoo symbol", result.symbol or "")
        history.insert(4, "Exchange", result.instrument.exchange)
        history.insert(5, "Currency", result.instrument.currency)
        history.insert(6, "Lookup", result.lookup_method)
        rows.append(history)

    if not rows:
        return pd.DataFrame(
            columns=[
                "Date",
                "ISIN",
                "Ticker",
                "Yahoo symbol",
                "Exchange",
                "Currency",
                "Lookup",
                "Open",
                "High",
                "Low",
                "Close",
                "Adj Close",
                "Volume",
            ]
        )
    return pd.concat(rows, ignore_index=True)


def build_price_xml(price_table: pd.DataFrame) -> bytes:
    required = ("Date", "ISIN", "Ticker", "Exchange", "Currency", "Close")
    missing = [column for column in required if column not in price_table.columns]
    if missing:
        raise ValueError(f"Price table is missing required columns: {', '.join(missing)}")

    root = ET.Element("Instruments")
    for _, row in price_table.iterrows():
        close = row["Close"]
        if pd.isna(close):
            continue

        timestamp = pd.Timestamp(row["Date"]).strftime("%Y%m%d000000")
        close_price = PRICE_DISPLAY_FORMAT % float(close)
        instrument = ET.SubElement(root, "Instrument")
        fields = (
            ("VP_NO", row["ISIN"] or row["Ticker"]),
            ("TS_EXCH_ID", row["Exchange"]),
            ("ISO_CURR", row["Currency"]),
            ("CLOSE_PRICE", close_price),
            ("CLOSE_TIMESTAMP", timestamp),
            ("LAST_RATE", close_price),
            ("LAST_TIMESTAMP", timestamp),
            # ("THEORETICAL_PRICE", close_price),
            # ("THEORETICAL_TIMESTAMP", timestamp),
        )
        for tag, value in fields:
            ET.SubElement(instrument, tag).text = str(value)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="\t")
    output = BytesIO()
    tree.write(output, encoding="utf-8", xml_declaration=True)
    return output.getvalue()
