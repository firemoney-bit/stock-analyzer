
import streamlit as st
import pandas as pd
import yfinance as yf
import matplotlib.pyplot as plt

st.set_page_config(
    page_title="日本株分析",
    page_icon="📈",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
.block-container {padding-top: 1.2rem; padding-bottom: 3rem; max-width: 920px;}
h1 {font-size: 1.8rem !important;}
div[data-testid="stMetric"] {
    border: 1px solid rgba(128,128,128,.25);
    padding: 10px;
    border-radius: 12px;
}
@media (max-width: 640px) {
  .block-container {padding-left: .75rem; padding-right: .75rem;}
  h1 {font-size: 1.55rem !important;}
}
</style>
""", unsafe_allow_html=True)

st.title("📈 日本株分析")
st.caption("銘柄コードを入力するだけで、株価・業績・財務・CFを確認できます。")

def jp_ticker(code: str) -> str:
    code = code.strip().upper()
    if code.endswith(".T"):
        return code
    return f"{code}.T" if code.isdigit() else code

def get_row(df, keys):
    if df is None or df.empty:
        return pd.Series(dtype=float)
    for key in keys:
        if key in df.index:
            return df.loc[key]
    return pd.Series(dtype=float)

def fmt_num(v):
    try:
        if v is None or pd.isna(v):
            return "不明"
        v = float(v)
        a = abs(v)
        if a >= 1_000_000_000_000:
            return f"{v/1_000_000_000_000:,.2f}兆円"
        if a >= 100_000_000:
            return f"{v/100_000_000:,.1f}億円"
        if a >= 10_000:
            return f"{v/10_000:,.1f}万円"
        return f"{v:,.2f}円"
    except Exception:
        return "不明"

def pct(v):
    """ROE・ROA・配当性向など、0.123 = 12.3% の項目用"""
    return f"{v*100:.2f}%" if isinstance(v, (int, float)) and not pd.isna(v) else "不明"

def dividend_pct(v):
    """yfinance の dividendYield は日本株では 0.98 = 0.98% の形で返ることがあるため、そのまま%表示"""
    return f"{v:.2f}%" if isinstance(v, (int, float)) and not pd.isna(v) else "不明"

def ratio(a, b):
    try:
        if a is None or b is None or pd.isna(a) or pd.isna(b) or b == 0:
            return None
        return float(a) / float(b)
    except Exception:
        return None

@st.cache_data(ttl=1800)
def load_data(symbol):
    t = yf.Ticker(symbol)
    try:
        info = t.info or {}
    except Exception:
        info = {}
    try:
        hist = t.history(period="5y", auto_adjust=False)
    except Exception:
        hist = pd.DataFrame()
    try:
        income = t.financials
    except Exception:
        income = pd.DataFrame()
    try:
        balance = t.balance_sheet
    except Exception:
        balance = pd.DataFrame()
    try:
        cashflow = t.cashflow
    except Exception:
        cashflow = pd.DataFrame()
    return info, hist, income, balance, cashflow

with st.form("analyze_form"):
    code = st.text_input("銘柄コード", value="8267", placeholder="例：8267")
    submitted = st.form_submit_button("分析する", use_container_width=True, type="primary")

if submitted:
    symbol = jp_ticker(code)

    with st.spinner("分析データを取得しています…"):
        info, hist, income, balance, cashflow = load_data(symbol)

    name = info.get("longName") or info.get("shortName") or symbol
    st.subheader(name)

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    market_cap = info.get("marketCap")
    per = info.get("trailingPE")
    pbr = info.get("priceToBook")
    dy = info.get("dividendYield")

    a, b = st.columns(2)
    a.metric("株価", f"{price:,.1f}円" if isinstance(price, (int, float)) else "不明")
    b.metric("時価総額", fmt_num(market_cap) if market_cap else "不明")

    a, b, c = st.columns(3)
    a.metric("PER", f"{per:.2f}倍" if isinstance(per, (int, float)) else "不明")
    b.metric("PBR", f"{pbr:.2f}倍" if isinstance(pbr, (int, float)) else "不明")
    c.metric("配当利回り", dividend_pct(dy))

    tab1, tab2, tab3, tab4 = st.tabs(["株価", "業績", "財務", "CF"])

    with tab1:
        if not hist.empty:
            st.markdown("#### 5年株価")
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.plot(hist.index, hist["Close"])
            ax.grid(True, alpha=.2)
            ax.set_xlabel("")
            ax.set_ylabel("JPY")
            st.pyplot(fig, clear_figure=True)
        else:
            st.warning("株価履歴を取得できませんでした。")

    with tab2:
        sales = get_row(income, ["Total Revenue", "Operating Revenue"])
        gross = get_row(income, ["Gross Profit"])
        op = get_row(income, ["Operating Income"])
        pretax = get_row(income, ["Pretax Income"])
        net = get_row(income, ["Net Income", "Net Income Common Stockholders"])
        eps = get_row(income, ["Diluted EPS", "Basic EPS"])

        cols = sorted(set(sales.index) | set(op.index) | set(net.index))
        rows = []
        for col in cols:
            s, g, o, p, n, e = (
                sales.get(col), gross.get(col), op.get(col),
                pretax.get(col), net.get(col), eps.get(col)
            )
            rows.append({
                "年度": pd.to_datetime(col).year,
                "売上高": fmt_num(s),
                "売上総利益": fmt_num(g),
                "営業利益": fmt_num(o),
                "税引前利益": fmt_num(p),
                "純利益": fmt_num(n),
                "EPS": f"{e:.2f}" if isinstance(e, (int, float)) else "不明",
                "営業利益率": pct(ratio(o, s)),
                "純利益率": pct(ratio(n, s)),
            })
        if rows:
            st.dataframe(pd.DataFrame(rows).sort_values("年度"), hide_index=True, use_container_width=True)
        else:
            st.warning("業績データを取得できませんでした。")

    with tab3:
        assets = get_row(balance, ["Total Assets"])
        equity = get_row(balance, ["Stockholders Equity", "Total Equity Gross Minority Interest"])
        ca = get_row(balance, ["Current Assets", "Total Current Assets"])
        cl = get_row(balance, ["Current Liabilities", "Total Current Liabilities"])
        debt = get_row(balance, ["Total Debt"])

        cols = sorted(set(assets.index) | set(equity.index))
        rows = []
        for col in cols:
            A, E, CA, CL, D = assets.get(col), equity.get(col), ca.get(col), cl.get(col), debt.get(col)
            rows.append({
                "年度": pd.to_datetime(col).year,
                "総資産": fmt_num(A),
                "自己資本": fmt_num(E),
                "自己資本比率": pct(ratio(E, A)),
                "流動資産": fmt_num(CA),
                "流動負債": fmt_num(CL),
                "流動比率": pct(ratio(CA, CL)),
                "有利子負債": fmt_num(D),
            })
        if rows:
            st.dataframe(pd.DataFrame(rows).sort_values("年度"), hide_index=True, use_container_width=True)
        else:
            st.warning("財務データを取得できませんでした。")

        st.markdown("#### 収益性・還元")
        a, b = st.columns(2)
        a.metric("ROE", pct(info.get("returnOnEquity")))
        b.metric("ROA", pct(info.get("returnOnAssets")))
        a, b = st.columns(2)
        a.metric("配当性向", pct(info.get("payoutRatio")))
        eps_ttm = info.get("trailingEps")
        b.metric("EPS（TTM）", f"{eps_ttm:.2f}" if isinstance(eps_ttm, (int, float)) else "不明")

    with tab4:
        ocf = get_row(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
        icf = get_row(cashflow, ["Investing Cash Flow", "Total Cashflows From Investing Activities"])
        fcf_fin = get_row(cashflow, ["Financing Cash Flow", "Total Cash From Financing Activities"])
        free_cf = get_row(cashflow, ["Free Cash Flow"])
        capex = get_row(cashflow, ["Capital Expenditure", "Capital Expenditures"])

        cols = sorted(set(ocf.index) | set(icf.index) | set(fcf_fin.index))
        raw_rows, rows = [], []
        for col in cols:
            O, I, F, FREE, CAP = ocf.get(col), icf.get(col), fcf_fin.get(col), free_cf.get(col), capex.get(col)
            if (FREE is None or pd.isna(FREE)) and O is not None and CAP is not None and not pd.isna(O) and not pd.isna(CAP):
                FREE = O + CAP if CAP < 0 else O - CAP
            year = pd.to_datetime(col).year
            raw_rows.append({"年度": year, "営業CF": O, "投資CF": I, "財務CF": F, "フリーCF": FREE})
            rows.append({"年度": year, "営業CF": fmt_num(O), "投資CF": fmt_num(I), "財務CF": fmt_num(F), "フリーCF": fmt_num(FREE)})

        if rows:
            df = pd.DataFrame(rows).sort_values("年度")
            st.dataframe(df, hide_index=True, use_container_width=True)
            plot = pd.DataFrame(raw_rows).sort_values("年度").set_index("年度")
            numeric_plot = plot.apply(pd.to_numeric, errors="coerce")
            if not numeric_plot.dropna(how="all").empty:
                fig, ax = plt.subplots(figsize=(8, 4))
                numeric_plot.plot(kind="bar", ax=ax)
                ax.set_xlabel("")
                ax.set_ylabel("JPY")
                ax.grid(True, axis="y", alpha=.2)
                ax.legend(loc="best", fontsize=8)
                st.pyplot(fig, clear_figure=True)
        else:
            st.warning("キャッシュフローデータを取得できませんでした。")

    st.caption(
        "データはYahoo Finance系データに依存します。日本企業では未取得項目や定義差があります。"
        "配当利回りなどは取得元の表記仕様に合わせて表示しています。"
        "重要な投資判断では決算短信・有価証券報告書など一次資料との照合が必要です。"
    )
else:
    st.info("まずは「8267」のまま『分析する』を押して試せます。")
