from datetime import date, timedelta

import pandas as pd
import streamlit as st

from market_data import (
    InstrumentResult,
    PRICE_DISPLAY_FORMAT,
    build_price_xml,
    build_price_table,
    fetch_instruments,
    parse_instruments,
)


st.set_page_config(
    page_title="Market Data Bridge",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    .stApp { background: #f5f7fb; }
    .block-container { max-width: 1440px; padding-top: 2rem; }
    .hero {
        padding: 1.6rem 1.8rem; border-radius: 18px; margin-bottom: 1.4rem;
        color: #f8fafc; background: linear-gradient(115deg, #13233f, #174a62);
    }
    .hero h1 { margin: 0 0 .35rem; font-size: 2rem; }
    .hero p { margin: 0; color: #d3e2ea; font-size: 1rem; }
    [data-testid="stMetric"] {
        background: white; border: 1px solid #e6eaf0; padding: 1rem;
        border-radius: 14px;
    }
    .stButton > button[kind="primary"] {
        background: #176b7e; border-color: #176b7e; color: white;
    }
    .stButton > button[kind="primary"]:hover {
        background: #125667; border-color: #125667; color: white;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="hero">
      <h1>Market Data Bridge</h1>
      <p>Bring daily market history together by ISIN or ticker, with your exchange
      and currency labels kept intact.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.container(border=True):
    st.subheader("1. Choose a date range")
    date_col, help_col = st.columns([1, 2])
    with date_col:
        date_range = st.date_input(
            "Historical period",
            value=(date.today() - timedelta(days=365), date.today()),
            max_value=date.today(),
        )
    with help_col:
        st.caption(
            "End date is inclusive. Prices are fetched from Yahoo Finance through "
            "yfinance; ISIN lookup is attempted first, then the supplied ticker."
        )

with st.container(border=True):
    st.subheader("2. Add instruments")
    st.caption("Choose how to provide your instrument records.")
    template = pd.DataFrame(
        [{"ticker": "MSFT", "isin": "US5949181045", "exchange": "NASDAQ", "currency": "USD"}]
    )
    input_method = st.radio(
        "Instrument input method",
        ["Enter manually", "Upload CSV"],
        horizontal=True,
        label_visibility="collapsed",
    )

    input_frame = pd.DataFrame(columns=["ticker", "isin", "exchange", "currency"])
    editor_key = "instrument_editor_manual"
    if input_method == "Enter manually":
        st.caption("Add records directly in the table. Use **Add row** to include more instruments.")
        input_frame = pd.DataFrame(
            [{"ticker": "", "isin": "", "exchange": "", "currency": ""}]
        )
    else:
        st.caption("Upload a CSV with the columns ticker, isin, exchange, and currency.")
        st.download_button(
            "Download CSV template",
            data=template.to_csv(index=False).encode("utf-8"),
            file_name="market-data-instruments.csv",
            mime="text/csv",
        )
        uploaded_file = st.file_uploader("Upload instrument CSV", type=["csv"])
        editor_key = "instrument_editor_upload"
        if uploaded_file is not None:
            try:
                input_frame = pd.read_csv(uploaded_file, dtype=str).fillna("")
            except (pd.errors.ParserError, UnicodeDecodeError) as exc:
                st.error(f"Could not read the uploaded CSV: {exc}")

    edited_frame = st.data_editor(
        input_frame,
        key=editor_key,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "ticker": st.column_config.TextColumn("Ticker", help="Yahoo Finance ticker fallback"),
            "isin": st.column_config.TextColumn("ISIN", help="Used for the first Yahoo Finance lookup"),
            "exchange": st.column_config.TextColumn("Exchange"),
            "currency": st.column_config.TextColumn("Currency"),
        },
    )

    if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = end_date = date_range

    download = st.button("Fetch historical prices", type="primary", use_container_width=True)
    if download:
        if start_date > end_date:
            st.error("The start date must be on or before the end date.")
        else:
            try:
                instruments = parse_instruments(edited_frame)
            except ValueError as exc:
                st.error(str(exc))
            else:
                progress = st.progress(0.1, text="Resolving ISINs and requesting prices in batches...")
                results: list[InstrumentResult] = fetch_instruments(
                    instruments, start_date, end_date
                )
                progress.progress(1.0, text="Price download complete")
                st.session_state["market_data_results"] = results
                progress.empty()

results = st.session_state.get("market_data_results", [])
if results:
    price_table = build_price_table(results)
    successful = [result for result in results if not result.prices.empty]
    failed = [result for result in results if result.prices.empty]

    st.subheader("3. Price history")
    metric_cols = st.columns(3)
    metric_cols[0].metric("Instruments", len(results))
    metric_cols[1].metric("Downloaded", len(successful))
    metric_cols[2].metric("Needs attention", len(failed))

    if failed:
        with st.expander(f"Lookup or download messages ({len(failed)})", expanded=True):
            for result in failed:
                label = result.instrument.isin or result.instrument.ticker or "Instrument"
                st.warning(f"{label}: {result.status}")
    elif any(result.status != "Downloaded" for result in successful):
        with st.expander("Lookup messages", expanded=False):
            for result in successful:
                if result.status != "Downloaded":
                    label = result.instrument.isin or result.instrument.ticker
                    st.info(f"{label}: {result.status}")

    if successful:
        instrument_options = {
            f"{result.instrument.isin or result.instrument.ticker} · {result.symbol}": result
            for result in successful
        }
        with st.expander("Instrument chart", expanded=False):
            selected_label = st.selectbox(
                "Instrument", list(instrument_options), label_visibility="collapsed"
            )
            selected = instrument_options[selected_label]
            selected_table = price_table.loc[
                (price_table["ISIN"] == selected.instrument.isin)
                & (price_table["Ticker"] == selected.instrument.ticker)
            ]
            chart_column = "Adj Close" if "Adj Close" in selected_table.columns else "Close"
            chart_data = selected_table.set_index("Date")[[chart_column]].dropna()
            if not chart_data.empty:
                st.line_chart(chart_data, y=chart_column, use_container_width=True)
            st.caption(
                f"Yahoo symbol: {selected.symbol} · Exchange: {selected.instrument.exchange or '—'} "
                f"· Currency: {selected.instrument.currency or '—'} · Lookup: {selected.lookup_method}"
            )

        st.markdown("#### All downloaded prices")
        toolbar_spacer, csv_download, xml_download = st.columns([8, 1, 1])
        with csv_download:
            st.download_button(
                "CSV",
                data=price_table.to_csv(index=False).encode("utf-8"),
                file_name="market-data-prices.csv",
                mime="text/csv",
                type="tertiary",
                icon=":material/download:",
                help="Download all price rows as CSV",
                use_container_width=True,
            )
        with xml_download:
            st.download_button(
                "XML",
                data=build_price_xml(price_table),
                file_name="market-data-prices.xml",
                mime="application/xml",
                type="tertiary",
                icon=":material/download:",
                help="Download all price rows as XML",
                use_container_width=True,
            )
        st.dataframe(
            price_table,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Close": st.column_config.NumberColumn(
                    "Close", format=PRICE_DISPLAY_FORMAT
                )
            },
        )
else:
    st.info("Add at least one instrument and fetch prices to see the results here.")
