
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

st.set_page_config(
    page_title="日本株分析 Ver.2",
    page_icon="📈",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
.block-container {padding-top: 1.0rem; padding-bottom: 4rem; max-width: 980px;}
h1 {font-size: 1.65rem !important;}
h2 {font-size: 1.3rem !important;}
div[data-testid="stMetric"] {
    border: 1px solid rgba(128,128,128,.25);
    padding: 10px;
    border-radius: 12px;
}
[data-testid="stTabs"] button {font-size: .95rem;}
@media (max-width: 640px) {
  .block-container {padding-left: .75rem; padding-right: .75rem;}
  h1 {font-size: 1.45rem !important;}
}
</style>
""", unsafe_allow_html=True)

st.title("📈 日本株分析 Ver.2")
st.caption("株価・業績・収益性・財務・CF・バリュエーションをまとめて確認")

# -------------------------
# Helpers
# -------------------------
def jp_ticker(code: str) -> str:
    code = code.strip().upper()
    if code.endswith(".T"):
        return code
    if code.isdigit():
        return f"{code}.T"
    return code

def get_row(df, keys):
    if df is None or df.empty:
        return pd.Series(dtype=float)
    for key in keys:
        if key in df.index:
            return df.loc[key]
    return pd.Series(dtype=float)

def num(v):
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None

def pct_fraction(v):
    """0.123 -> 12.30%"""
    v = num(v)
    return f"{v*100:.2f}%" if v is not None else "不明"

def pct_points(v):
    """0.98 -> 0.98%"""
    v = num(v)
    return f"{v:.2f}%" if v is not None else "不明"

def ratio(a, b):
    a, b = num(a), num(b)
    if a is None or b is None or b == 0:
        return None
    return a / b

def fmt_yen(v):
    v = num(v)
    if v is None:
        return "不明"
    a = abs(v)
    if a >= 1_000_000_000_000:
        return f"{v/1_000_000_000_000:,.2f}兆円"
    if a >= 100_000_000:
        return f"{v/100_000_000:,.1f}億円"
    if a >= 10_000:
        return f"{v/10_000:,.1f}万円"
    return f"{v:,.0f}円"

def fmt_plain(v, digits=2):
    v = num(v)
    return f"{v:,.{digits}f}" if v is not None else "不明"

def annual_dividend_yield(info, price):
    """Prefer annual dividend amount / current price."""
    div = num(info.get("dividendRate"))
    p = num(price)
    if div is not None and p not in (None, 0):
        return div / p
    # Fallback: yfinance's dividendYield can be either fraction or percentage-point
    dy = num(info.get("dividendYield"))
    if dy is None:
        return None
    if dy > 0.20:  # e.g. 0.98 means 0.98%, not 98%
        return dy / 100
    return dy

def compact_axis(x, pos):
    a = abs(x)
    if a >= 1_000_000_000_000:
        return f"{x/1_000_000_000_000:.1f}T"
    if a >= 100_000_000:
        return f"{x/100_000_000:.0f}B"
    if a >= 1_000_000:
        return f"{x/1_000_000:.0f}M"
    return f"{x:,.0f}"

def safe_year(col):
    try:
        return pd.to_datetime(col).year
    except Exception:
        return str(col)

def color_score(score):
    if score >= 75:
        return "🟢"
    if score >= 55:
        return "🟡"
    return "🔴"

def heuristic_scores(info, income, balance, cashflow):
    scores = {}
    notes = {}

    # Profitability
    roe = num(info.get("returnOnEquity"))
    roa = num(info.get("returnOnAssets"))
    profit = 50
    if roe is not None:
        profit += 15 if roe >= 0.12 else 8 if roe >= 0.08 else -8 if roe < 0 else 0
    if roa is not None:
        profit += 10 if roa >= 0.05 else 5 if roa >= 0.03 else -5 if roa < 0 else 0
    scores["収益性"] = max(0, min(100, profit))

    # Growth
    sales = get_row(income, ["Total Revenue", "Operating Revenue"]).dropna()
    net = get_row(income, ["Net Income", "Net Income Common Stockholders"]).dropna()
    growth = 50
    try:
        if len(sales) >= 2:
            old, new = float(sales.iloc[-1]), float(sales.iloc[0])
            if old != 0:
                g = new / old - 1
                growth += 18 if g >= 0.15 else 10 if g >= 0.05 else -12 if g < 0 else 0
        if len(net) >= 2:
            old, new = float(net.iloc[-1]), float(net.iloc[0])
            if old != 0:
                g = new / old - 1
                growth += 12 if g >= 0.15 else 6 if g >= 0.05 else -12 if g < 0 else 0
    except Exception:
        pass
    scores["成長性"] = max(0, min(100, growth))

    # Financial stability
    assets = get_row(balance, ["Total Assets"])
    equity = get_row(balance, ["Stockholders Equity", "Total Equity Gross Minority Interest"])
    debt = get_row(balance, ["Total Debt"])
    stability = 50
    try:
        if len(assets) and len(equity):
            er = ratio(equity.iloc[0], assets.iloc[0])
            if er is not None:
                stability += 20 if er >= 0.50 else 12 if er >= 0.30 else -10 if er < 0.15 else 0
        if len(debt) and len(equity):
            de = ratio(debt.iloc[0], equity.iloc[0])
            if de is not None:
                stability += 10 if de <= 0.5 else 5 if de <= 1 else -10 if de >= 2 else 0
    except Exception:
        pass
    scores["財務安全性"] = max(0, min(100, stability))

    # Shareholder return
    dy = annual_dividend_yield(info, info.get("currentPrice") or info.get("regularMarketPrice"))
    payout = num(info.get("payoutRatio"))
    shareholder = 45
    if dy is not None:
        shareholder += 18 if dy >= 0.03 else 10 if dy >= 0.015 else 2
    if payout is not None:
        shareholder += 10 if 0.25 <= payout <= 0.60 else -8 if payout > 0.90 else 0
    scores["株主還元"] = max(0, min(100, shareholder))

    # Valuation (very rough)
    pe = num(info.get("trailingPE"))
    pb = num(info.get("priceToBook"))
    val = 50
    if pe is not None:
        val += 12 if 0 < pe <= 15 else 5 if pe <= 25 else -12 if pe >= 40 else 0
    if pb is not None:
        val += 10 if 0 < pb <= 1.5 else 3 if pb <= 3 else -10 if pb >= 5 else 0
    scores["割安性"] = max(0, min(100, val))

    return scores

@st.cache_data(ttl=1800)
def load_data(symbol):
    t = yf.Ticker(symbol)
    try:
        info = t.info or {}
    except Exception:
        info = {}
    try:
        hist = t.history(period="10y", auto_adjust=False)
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

# -------------------------
# Input
# -------------------------
with st.form("analyze_form"):
    code = st.text_input("銘柄コード", value="8267", placeholder="例：8267")
    submitted = st.form_submit_button("分析する", use_container_width=True, type="primary")

if not submitted:
    st.info("まずは「8267」のまま「分析する」を押してください。")
    st.stop()

symbol = jp_ticker(code)

with st.spinner("データを取得しています…"):
    info, hist, income, balance, cashflow = load_data(symbol)

name = info.get("longName") or info.get("shortName") or symbol
price = info.get("currentPrice") or info.get("regularMarketPrice")
market_cap = info.get("marketCap")
pe = info.get("trailingPE")
pb = info.get("priceToBook")
dy = annual_dividend_yield(info, price)

st.subheader(name)

# -------------------------
# Headline metrics
# -------------------------
a, b = st.columns(2)
a.metric("株価", f"{num(price):,.1f}円" if num(price) is not None else "不明")
b.metric("時価総額", fmt_yen(market_cap))

a, b, c = st.columns(3)
a.metric("PER", f"{num(pe):.2f}倍" if num(pe) is not None else "不明")
b.metric("PBR", f"{num(pb):.2f}倍" if num(pb) is not None else "不明")
c.metric("配当利回り", f"{dy*100:.2f}%" if dy is not None else "不明")

# -------------------------
# Heuristic score
# -------------------------
scores = heuristic_scores(info, income, balance, cashflow)
overall = round(sum(scores.values()) / len(scores)) if scores else 0

st.markdown("### 総合スコア")
st.metric("総合評価（参考）", f"{overall}/100")
score_df = pd.DataFrame({
    "項目": list(scores.keys()),
    "点数": list(scores.values()),
    "判定": [color_score(v) for v in scores.values()]
})
st.dataframe(score_df, hide_index=True, use_container_width=True)
st.caption("スコアは機械的な参考指標です。業種差や将来予想を十分に反映しないため、投資判断そのものではありません。")

tabs = st.tabs(["株価", "業績", "収益性", "財務", "CF", "評価"])

# -------------------------
# Price
# -------------------------
with tabs[0]:
    st.markdown("#### 株価推移（最大10年）")
    if not hist.empty and "Close" in hist:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(hist.index, hist["Close"])
        ax.set_ylabel("JPY")
        ax.set_xlabel("")
        ax.grid(True, alpha=.25)
        st.pyplot(fig, clear_figure=True)
    else:
        st.warning("株価履歴を取得できませんでした。")

# -------------------------
# Income statement
# -------------------------
with tabs[1]:
    sales = get_row(income, ["Total Revenue", "Operating Revenue"])
    gross = get_row(income, ["Gross Profit"])
    op = get_row(income, ["Operating Income"])
    pretax = get_row(income, ["Pretax Income"])
    net = get_row(income, ["Net Income", "Net Income Common Stockholders"])
    eps = get_row(income, ["Diluted EPS", "Basic EPS"])

    cols = sorted(set(sales.index) | set(op.index) | set(net.index))
    raw = []
    view = []

    for col in cols:
        s, g, o, p, n, e = (
            sales.get(col), gross.get(col), op.get(col),
            pretax.get(col), net.get(col), eps.get(col)
        )
        raw.append({
            "年度": safe_year(col), "売上高": num(s), "営業利益": num(o), "純利益": num(n)
        })
        view.append({
            "年度": safe_year(col),
            "売上高": fmt_yen(s),
            "売上総利益": fmt_yen(g),
            "営業利益": fmt_yen(o),
            "税引前利益": fmt_yen(p),
            "純利益": fmt_yen(n),
            "EPS": fmt_plain(e),
            "営業利益率": pct_fraction(ratio(o, s)),
            "純利益率": pct_fraction(ratio(n, s)),
        })

    if view:
        dfv = pd.DataFrame(view).sort_values("年度")
        st.dataframe(dfv, hide_index=True, use_container_width=True)

        dfr = pd.DataFrame(raw).sort_values("年度").set_index("年度")
        fig, ax = plt.subplots(figsize=(8, 4))
        for key, label in [("売上高","Revenue"),("営業利益","Operating income"),("純利益","Net income")]:
            if key in dfr:
                ax.plot(dfr.index.astype(str), dfr[key], marker="o", label=label)
        ax.yaxis.set_major_formatter(FuncFormatter(compact_axis))
        ax.grid(True, alpha=.25)
        ax.legend(fontsize=8)
        ax.set_xlabel("")
        st.pyplot(fig, clear_figure=True)
    else:
        st.warning("業績データを取得できませんでした。")

# -------------------------
# Profitability
# -------------------------
with tabs[2]:
    st.markdown("#### 収益性")
    a, b = st.columns(2)
    a.metric("ROE", pct_fraction(info.get("returnOnEquity")))
    b.metric("ROA", pct_fraction(info.get("returnOnAssets")))
    a, b = st.columns(2)
    a.metric("営業利益率（直近）", pct_fraction(info.get("operatingMargins")))
    b.metric("純利益率（直近）", pct_fraction(info.get("profitMargins")))

    st.markdown("#### EPS・株主還元")
    a, b = st.columns(2)
    eps_ttm = num(info.get("trailingEps"))
    a.metric("EPS（TTM）", f"{eps_ttm:.2f}円" if eps_ttm is not None else "不明")
    b.metric("配当性向", pct_fraction(info.get("payoutRatio")))

# -------------------------
# Balance sheet
# -------------------------
with tabs[3]:
    assets = get_row(balance, ["Total Assets"])
    equity = get_row(balance, ["Stockholders Equity", "Total Equity Gross Minority Interest"])
    ca = get_row(balance, ["Current Assets", "Total Current Assets"])
    cl = get_row(balance, ["Current Liabilities", "Total Current Liabilities"])
    debt = get_row(balance, ["Total Debt"])
    cash = get_row(balance, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"])

    cols = sorted(set(assets.index) | set(equity.index))
    raw = []
    view = []

    for col in cols:
        A, E, CA, CL, D, C = (
            assets.get(col), equity.get(col), ca.get(col),
            cl.get(col), debt.get(col), cash.get(col)
        )
        raw.append({"年度": safe_year(col), "総資産": num(A), "自己資本": num(E), "有利子負債": num(D)})
        view.append({
            "年度": safe_year(col),
            "総資産": fmt_yen(A),
            "自己資本": fmt_yen(E),
            "自己資本比率": pct_fraction(ratio(E, A)),
            "流動資産": fmt_yen(CA),
            "流動負債": fmt_yen(CL),
            "流動比率": pct_fraction(ratio(CA, CL)),
            "有利子負債": fmt_yen(D),
            "現金等": fmt_yen(C),
        })

    if view:
        st.dataframe(pd.DataFrame(view).sort_values("年度"), hide_index=True, use_container_width=True)

        dfr = pd.DataFrame(raw).sort_values("年度").set_index("年度")
        fig, ax = plt.subplots(figsize=(8, 4))
        for key, label in [("総資産","Assets"),("自己資本","Equity"),("有利子負債","Debt")]:
            if key in dfr:
                ax.plot(dfr.index.astype(str), dfr[key], marker="o", label=label)
        ax.yaxis.set_major_formatter(FuncFormatter(compact_axis))
        ax.grid(True, alpha=.25)
        ax.legend(fontsize=8)
        ax.set_xlabel("")
        st.pyplot(fig, clear_figure=True)
    else:
        st.warning("財務データを取得できませんでした。")

# -------------------------
# Cash flow
# -------------------------
with tabs[4]:
    st.markdown("#### キャッシュフロー")

    ocf = get_row(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    icf = get_row(cashflow, ["Investing Cash Flow", "Total Cashflows From Investing Activities"])
    fincf = get_row(cashflow, ["Financing Cash Flow", "Total Cash From Financing Activities"])
    capex = get_row(cashflow, ["Capital Expenditure", "Capital Expenditures"])
    fcf_direct = get_row(cashflow, ["Free Cash Flow"])

    cols = sorted(set(ocf.index) | set(icf.index) | set(fincf.index))
    raw = []
    view = []

    for col in cols:
        O = num(ocf.get(col))
        I = num(icf.get(col))
        F = num(fincf.get(col))
        CAP = num(capex.get(col))
        FCF = num(fcf_direct.get(col))

        # If FCF isn't directly available, compute Operating CF - CapEx magnitude.
        if FCF is None and O is not None and CAP is not None:
            if CAP < 0:
                FCF = O + CAP
            else:
                FCF = O - CAP

        raw.append({
            "年度": safe_year(col),
            "営業CF": O,
            "投資CF": I,
            "財務CF": F,
            "FCF": FCF,
        })
        view.append({
            "年度": safe_year(col),
            "営業CF": fmt_yen(O),
            "投資CF": fmt_yen(I),
            "財務CF": fmt_yen(F),
            "FCF": fmt_yen(FCF),
        })

    if view:
        st.dataframe(pd.DataFrame(view).sort_values("年度"), hide_index=True, use_container_width=True)

        dfr = pd.DataFrame(raw).sort_values("年度").set_index("年度")

        st.markdown("##### 主要CF")
        fig, ax = plt.subplots(figsize=(8, 4))
        x = np.arange(len(dfr.index))
        width = 0.25
        series = [
            ("営業CF", "Operating CF"),
            ("投資CF", "Investing CF"),
            ("財務CF", "Financing CF"),
        ]
        for i, (key, label) in enumerate(series):
            vals = pd.to_numeric(dfr[key], errors="coerce").values
            ax.bar(x + (i-1)*width, vals, width=width, label=label)
        ax.axhline(0, linewidth=.8)
        ax.set_xticks(x)
        ax.set_xticklabels(dfr.index.astype(str))
        ax.yaxis.set_major_formatter(FuncFormatter(compact_axis))
        ax.grid(True, axis="y", alpha=.2)
        ax.legend(fontsize=8)
        st.pyplot(fig, clear_figure=True)

        st.markdown("##### フリーCF")
        fig, ax = plt.subplots(figsize=(8, 3.5))
        fcf_vals = pd.to_numeric(dfr["FCF"], errors="coerce").values
        ax.bar(dfr.index.astype(str), fcf_vals)
        ax.axhline(0, linewidth=.8)
        ax.yaxis.set_major_formatter(FuncFormatter(compact_axis))
        ax.grid(True, axis="y", alpha=.2)
        ax.set_xlabel("")
        st.pyplot(fig, clear_figure=True)

        st.caption("FCFは取得元に直接値がある場合はその値を使用し、ない場合は「営業CF − 設備投資額」で計算します。")
    else:
        st.warning("キャッシュフローデータを取得できませんでした。")

# -------------------------
# Valuation / summary
# -------------------------
with tabs[5]:
    st.markdown("#### バリュエーション")
    a, b = st.columns(2)
    a.metric("PER", f"{num(pe):.2f}倍" if num(pe) is not None else "不明")
    b.metric("PBR", f"{num(pb):.2f}倍" if num(pb) is not None else "不明")
    a, b = st.columns(2)
    a.metric("配当利回り", f"{dy*100:.2f}%" if dy is not None else "不明")
    b.metric("配当性向", pct_fraction(info.get("payoutRatio")))

    st.markdown("#### 参考判定")
    for k, v in scores.items():
        st.write(f"{color_score(v)} **{k}：{v}/100**")

    st.warning(
        "割安性スコアは現在のPER/PBR等による簡易判定です。"
        "過去レンジ、業種平均、会社予想、金利環境をまだ十分に反映していません。"
    )

st.divider()
st.caption(
    "データはYahoo Finance系データに依存します。日本企業では取得できる年数や項目に制限・定義差があります。"
    "重要な投資判断では、決算短信・有価証券報告書・会社開示など一次資料との照合が必要です。"
)
