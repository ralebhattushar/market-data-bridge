import unittest
from datetime import date
from io import BytesIO
from unittest.mock import patch
import xml.etree.ElementTree as ET

import pandas as pd

from market_data import (
    Instrument,
    build_price_xml,
    build_price_table,
    fetch_instruments,
    parse_instruments,
)


class MarketDataTests(unittest.TestCase):
    def setUp(self):
        self.instrument = Instrument(
            ticker="MSFT",
            isin="US5949181045",
            exchange="NASDAQ",
            currency="USD",
        )
        self.history = pd.DataFrame(
            {
                "Open": [100.0],
                "High": [105.0],
                "Low": [99.0],
                "Close": [103.0],
                "Adj Close": [102.0],
                "Volume": [1000],
            },
            index=pd.DatetimeIndex(["2025-01-02"]),
        )

    def batch_history(self, **histories):
        return pd.concat(histories, axis=1)

    def test_parse_instruments_normalizes_headers_and_skips_blank_rows(self):
        frame = pd.DataFrame(
            [
                {" Ticker ": "MSFT", "ISIN": "US5949181045", "Exchange": "NASDAQ", "Currency": "USD"},
                {" Ticker ": "", "ISIN": "", "Exchange": "", "Currency": ""},
            ]
        )

        instruments = parse_instruments(frame)

        self.assertEqual(instruments, [self.instrument])

    def test_parse_instruments_rejects_rows_without_identifier(self):
        frame = pd.DataFrame(
            [{"ticker": "", "isin": "", "exchange": "NASDAQ", "currency": "USD"}]
        )

        with self.assertRaisesRegex(ValueError, "needs at least a ticker or an ISIN"):
            parse_instruments(frame)

    def test_build_price_xml_maps_grid_rows_to_instrument_elements(self):
        price_table = pd.DataFrame(
            [
                {
                    "Date": "2026-08-05",
                    "ISIN": "NO0010073232",
                    "Ticker": "EXAMPLE.OL",
                    "Exchange": "XXXX",
                    "Currency": "NOK",
                    "Close": 671.0565999999999,
                },
                {
                    "Date": "2026-08-06",
                    "ISIN": "NO0010073232",
                    "Ticker": "EXAMPLE.OL",
                    "Exchange": "XXXX",
                    "Currency": "NOK",
                    "Close": 672.25,
                },
            ]
        )

        root = ET.parse(BytesIO(build_price_xml(price_table))).getroot()

        self.assertEqual(root.tag, "Instruments")
        self.assertEqual(len(root), 2)
        first = root[0]
        self.assertEqual(first.findtext("VP_NO"), "NO0010073232")
        self.assertEqual(first.findtext("TS_EXCH_ID"), "XXXX")
        self.assertEqual(first.findtext("ISO_CURR"), "NOK")
        self.assertEqual(first.findtext("CLOSE_PRICE"), "671.0566")
        self.assertEqual(first.findtext("CLOSE_TIMESTAMP"), "20260805000000")
        self.assertEqual(first.findtext("LAST_RATE"), "671.0566")
        self.assertEqual(first.findtext("LAST_TIMESTAMP"), "20260805000000")
        self.assertEqual(root[1].findtext("CLOSE_PRICE"), "672.2500")

    def test_build_price_xml_falls_back_to_ticker_and_escapes_values(self):
        price_table = pd.DataFrame(
            [
                {
                    "Date": "2026-08-05",
                    "ISIN": "",
                    "Ticker": "ABC",
                    "Exchange": "A&B",
                    "Currency": "USD",
                    "Close": 12.5,
                }
            ]
        )

        root = ET.fromstring(build_price_xml(price_table))

        self.assertEqual(root[0].findtext("VP_NO"), "ABC")
        self.assertEqual(root[0].findtext("TS_EXCH_ID"), "A&B")

    @patch("market_data.yf.Search")
    @patch("market_data.yf.download")
    def test_batches_multiple_symbols_into_one_price_request(self, download, search):
        search.return_value.quotes = [{"symbol": "MSFT"}]
        apple_history = self.history.assign(Close=200.0)
        download.return_value = self.batch_history(
            MSFT=self.history,
            AAPL=apple_history,
        )

        instruments = [
            self.instrument,
            Instrument(ticker="AAPL", isin="", exchange="NASDAQ", currency="USD"),
        ]
        results = fetch_instruments(
            instruments, date(2025, 1, 1), date(2025, 1, 2)
        )
        table = build_price_table(results)

        search.assert_called_once_with("US5949181045", max_results=10, news_count=0)
        download.assert_called_once()
        self.assertEqual(download.call_args.kwargs["tickers"], ["MSFT", "AAPL"])
        self.assertEqual(results[0].lookup_method, "ISIN lookup")
        self.assertEqual(results[1].lookup_method, "Ticker")
        self.assertEqual(table.loc[0, "Exchange"], "NASDAQ")
        self.assertEqual(table.loc[0, "Currency"], "USD")
        self.assertEqual(table.loc[0, "Date"], "2025-01-02")

    @patch("market_data.yf.Search")
    @patch("market_data.yf.download")
    def test_falls_back_to_ticker_if_isin_lookup_fails(self, download, search):
        search.side_effect = RuntimeError("search unavailable")
        download.return_value = self.batch_history(MSFT=self.history)

        result = fetch_instruments(
            [self.instrument], date(2025, 1, 1), date(2025, 1, 2)
        )[0]

        download.assert_called_once()
        self.assertEqual(download.call_args.kwargs["tickers"], ["MSFT"])
        self.assertEqual(result.symbol, "MSFT")
        self.assertEqual(result.lookup_method, "Ticker fallback")
        self.assertIn("ISIN lookup failed", result.status)
        self.assertFalse(result.prices.empty)

    @patch("market_data.yf.Ticker")
    @patch("market_data.yf.download")
    def test_fills_missing_exchange_and_currency_from_yahoo_metadata(
        self, download, ticker
    ):
        download.return_value = self.batch_history(MSFT=self.history)
        ticker.return_value.get_info.return_value = {
            "exchange": "NMS",
            "currency": "USD",
        }
        instrument = Instrument(
            ticker="MSFT", isin="", exchange="", currency=""
        )

        result = fetch_instruments(
            [instrument], date(2025, 1, 1), date(2025, 1, 2)
        )[0]
        table = build_price_table([result])

        ticker.assert_called_once_with("MSFT")
        self.assertEqual(result.instrument.exchange, "NMS")
        self.assertEqual(result.instrument.currency, "USD")
        self.assertEqual(table.loc[0, "Exchange"], "NMS")
        self.assertEqual(table.loc[0, "Currency"], "USD")

    @patch("market_data.yf.Ticker")
    @patch("market_data.yf.download")
    def test_preserves_provided_metadata_and_keeps_prices_if_metadata_lookup_fails(
        self, download, ticker
    ):
        download.return_value = self.batch_history(MSFT=self.history)
        ticker.return_value.get_info.side_effect = RuntimeError("metadata unavailable")
        instrument = Instrument(
            ticker="MSFT", isin="", exchange="NASDAQ", currency=""
        )

        result = fetch_instruments(
            [instrument], date(2025, 1, 1), date(2025, 1, 2)
        )[0]

        self.assertEqual(result.instrument.exchange, "NASDAQ")
        self.assertEqual(result.instrument.currency, "")
        self.assertIn("Instrument metadata lookup failed", result.status)
        self.assertFalse(result.prices.empty)

    @patch("market_data.yf.Search")
    @patch("market_data.yf.download")
    def test_batches_ticker_fallback_when_isin_symbol_has_no_prices(
        self, download, search
    ):
        search.return_value.quotes = [{"symbol": "MSFT.L"}]
        download.side_effect = [pd.DataFrame(), self.batch_history(MSFT=self.history)]

        result = fetch_instruments(
            [self.instrument], date(2025, 1, 1), date(2025, 1, 2)
        )[0]

        self.assertEqual(download.call_count, 2)
        self.assertEqual(download.call_args_list[0].kwargs["tickers"], ["MSFT.L"])
        self.assertEqual(download.call_args_list[1].kwargs["tickers"], ["MSFT"])
        self.assertEqual(result.symbol, "MSFT")
        self.assertEqual(result.lookup_method, "Ticker fallback")
        self.assertFalse(result.prices.empty)


if __name__ == "__main__":
    unittest.main()
