# Market Data Bridge

A Streamlit app for downloading historical prices from Yahoo Finance using
`yfinance`. Choose **Enter manually** to add records directly in the editable
table, or **Upload CSV** to load a file with the columns
`ticker`, `isin`, `exchange`, and `currency`. The app tries to resolve each ISIN
through Yahoo Finance search first and falls back to the supplied ticker when
lookup or price retrieval fails. Exchange and currency values supplied in the
input are retained; when either is blank, the app uses that symbol's Yahoo
Finance metadata when available. They are not used to convert prices.

When fetching multiple instruments, the app requests their historical prices
with a single `yfinance.download` batch wherever possible. ISIN-to-ticker
resolution still uses one Yahoo Finance search per ISIN. If an ISIN-resolved
symbol has no prices, ticker fallbacks are requested together in a second batch.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Push this project to a GitHub repository. The app entrypoint is `app.py` and
   dependencies are listed in the root `requirements.txt`.
2. Sign in at [share.streamlit.io](https://share.streamlit.io) and choose
   **Create app**.
3. Select the repository, branch, and `app.py` as the app file, then choose
   **Deploy**.
4. Share the generated `*.streamlit.app` URL.

This app does not currently require API keys or Streamlit secrets. It does
require an internet connection to retrieve data from Yahoo Finance. Community
Cloud resources and Yahoo Finance request availability may be limited, so
large or frequent downloads can take longer or be throttled.

Select an inclusive start and end date, choose an instrument input method, add
records, and choose **Fetch historical prices**. The resulting grid contains all
downloaded price rows and can be exported as CSV or XML. Each XML grid row is
serialized as an `<Instrument>` with its ISIN (or ticker if ISIN is blank),
exchange, currency, and closing price. The close value populates
`CLOSE_PRICE`, `LAST_RATE`, and `THEORETICAL_PRICE`; the row date, at midnight,
populates their corresponding timestamp fields. Close prices are displayed and
exported to XML with four decimal places to keep the values consistent. Use the
per-instrument chart to inspect adjusted closing prices when available.

The grid supports its built-in search and sorting. The CSV and XML download
links above the grid export all downloaded price rows.

## CSV format

```csv
ticker,isin,exchange,currency
MSFT,US5949181045,NASDAQ,USD
```
