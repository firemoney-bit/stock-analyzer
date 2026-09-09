
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import requests
from datetime import datetime, timedelta, timezone
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

st.set_page_config(
    page_title="日本株分析 Ver.4",
    page_icon="📈",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
.block-container {padding-top: 1rem; padding-bottom: 4rem; max-width: 980px;}
h1 {font-size: 1.6rem !important;}
div[data-testid="stMetric"] {
    border: 1px solid rgba(128,128,128,.25);
    padding: 10px;
    border-radius: 12px;
}
.sourcebox {
    border: 1px solid rgba(128,128,128,.25);
    border-radius: 12px;
    padding: 12px;
    margin: 8px 0 14px 0;
}
</style>
""", unsafe_allow_html=True)

JST = timezone(timedelta(hours=9))

st.title("📈 日本株分析 Ver.4")
st.caption("数値だけでなく、データ元・基準日も確認できる日本株分析")

# -------------------------
# Utilities
# -------------------------
def jp_ticker(code):
    code = code.strip().upper()
    if code.endswith(".T"):
        return code
    return f"{code}.T" if code.isdigit() else code

def stock_code4(code):
    code = code.replace(".T", "").strip()
    return code[:4] if len(code) >= 4 else code

def get_row(df, keys):
    if df is None or df.empty:
        return pd.Series(dtype=float)
    for k in keys:
        if k in df.index:
            return df.loc[k]
    return pd.Series(dtype=float)

def num(v):
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except:
        return None

def ratio(a, b):
    a, b = num(a), num(b)
    if a is None or b is None or b == 0:
        return None
    return a / b

def pct(v):
    v = num(v)
    return f"{v*100:.2f}%" if v is not None else "不明"

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

def safe_year(v):
    try:
        return pd.to_datetime(v).year
    except:
        return str(v)

def choose_unit(vals):
    vals = [abs(float(v)) for v in vals if v is not None and not pd.isna(v)]
    if not vals:
        return 1, "円"
    m = max(vals)
    if m >= 1_000_000_000_000:
        return 1_000_000_000_000, "兆円"
    if m >= 100_000_000:
        return 100_000_000, "億円"
    return 1, "円"

def axis_fmt(scale):
    return FuncFormatter(lambda x, pos: f"{x/scale:,.1f}")

def div_yield(info, price):
    d = num(info.get("dividendRate"))
    p = num(price)
    if d is not None and p not in (None, 0):
        return d / p
    dy = num(info.get("dividendYield"))
    if dy is None:
        return None
    return dy / 100 if dy > 0.20 else dy

@st.cache_data(ttl=1800)
def load_yahoo(symbol):
    t = yf.Ticker(symbol)
    try:
        info = t.info or {}
    except:
        info = {}
    try:
        hist = t.history(period="10y", auto_adjust=False)
    except:
        hist = pd.DataFrame()
    try:
        income = t.financials
    except:
        income = pd.DataFrame()
    try:
        balance = t.balance_sheet
    except:
        balance = pd.DataFrame()
    try:
        cashflow = t.cashflow
    except:
        cashflow = pd.DataFrame()
    return info, hist, income, balance, cashflow

# -------------------------
# EDINET
# -------------------------
def get_edinet_key():
    try:
        return st.secrets["EDINET_API_KEY"]
    except:
        return None

@st.cache_data(ttl=21600)
def latest_edinet_filings(code4, api_key, days=45):
    if not api_key:
        return []
    endpoint = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
    results = []

    # Recent filings first. Stop once enough relevant filings are found.
    for i in range(days):
        d = (datetime.now(JST).date() - timedelta(days=i)).isoformat()
        try:
            r = requests.get(
                endpoint,
                params={"date": d, "type": 2, "Subscription-Key": api_key},
                timeout=10
            )
            if r.status_code != 200:
                continue
            js = r.json()
            for row in js.get("results", []):
                sec = str(row.get("secCode") or "")
                if not sec.startswith(code4):
                    continue
                results.append({
                    "提出日時": row.get("submitDateTime", ""),
                    "書類名": row.get("docDescription", ""),
                    "書類種別": row.get("docTypeCode", ""),
                    "EDINETコード": row.get("edinetCode", ""),
                    "書類ID": row.get("docID", ""),
                })
            if len(results) >= 5:
                break
        except:
            continue
    return results[:5]

# -------------------------
# Input
# -------------------------
with st.form("form"):
    code = st.text_input("銘柄コード", value="8267", placeholder="例：8267")
    submitted = st.form_submit_button("分析する", use_container_width=True, type="primary")

if not submitted:
    st.info("銘柄コードを入力して「分析する」を押してください。")
    st.stop()

symbol = jp_ticker(code)
code4 = stock_code4(code)

with st.spinner("データを取得しています…"):
    info, hist, income, balance, cashflow = load_yahoo(symbol)

name = info.get("longName") or info.get("shortName") or symbol
price = info.get("currentPrice") or info.get("regularMarketPrice")
market_cap = info.get("marketCap")
pe = info.get("trailingPE")
pb = info.get("priceToBook")
dy = div_yield(info, price)

# Market date
if not hist.empty:
    last_market_date = pd.to_datetime(hist.index[-1]).strftime("%Y/%m/%d")
else:
    last_market_date = "不明"

now_jst = datetime.now(JST).strftime("%Y/%m/%d %H:%M")

st.subheader(name)

# -------------------------
# Source panel
# -------------------------
st.markdown("### データ基準")
st.markdown(
    f"""
<div class="sourcebox">
<b>アプリ取得日時：</b>{now_jst}<br>
<b>株価データ基準日：</b>{last_market_date}<br>
<b>株価・市場指標：</b>Yahoo Finance / yfinance<br>
<b>業績・財務・CF：</b>Yahoo Finance / yfinance（最新反映済みデータ）<br>
<b>公式開示確認：</b>EDINET（APIキー設定時）
</div>
""",
    unsafe_allow_html=True,
)

a, b = st.columns(2)
a.metric("株価", f"{num(price):,.1f}円" if num(price) is not None else "不明")
b.metric("時価総額", fmt_yen(market_cap))
a, b, c = st.columns(3)
a.metric("PER", f"{num(pe):.2f}倍" if num(pe) is not None else "不明")
b.metric("PBR", f"{num(pb):.2f}倍" if num(pb) is not None else "不明")
c.metric("配当利回り", f"{dy*100:.2f}%" if dy is not None else "不明")

tabs = st.tabs(["株価", "業績", "財務", "CF", "公式開示", "注意事項"])

# -------------------------
# Price
# -------------------------
with tabs[0]:
    st.markdown("#### 株価推移（最大10年）")
    if not hist.empty and "Close" in hist:
        fig, ax = plt.subplots(figsize=(8,4))
        ax.plot(hist.index, hist["Close"])
        ax.set_ylabel("株価（円）")
        ax.set_xlabel("")
        ax.grid(True, alpha=.25)
        st.pyplot(fig, clear_figure=True)
        st.caption(f"データ元：Yahoo Finance / 基準日：{last_market_date}")
    else:
        st.warning("株価履歴を取得できませんでした。")

# -------------------------
# Income
# -------------------------
with tabs[1]:
    sales = get_row(income, ["Total Revenue", "Operating Revenue"])
    gross = get_row(income, ["Gross Profit"])
    op = get_row(income, ["Operating Income"])
    pretax = get_row(income, ["Pretax Income"])
    net = get_row(income, ["Net Income", "Net Income Common Stockholders"])
    eps = get_row(income, ["Diluted EPS", "Basic EPS"])

    cols = sorted(set(sales.index) | set(op.index) | set(net.index))
    rows, raw = [], []

    for col in cols:
        s, g, o, p, n, e = sales.get(col), gross.get(col), op.get(col), pretax.get(col), net.get(col), eps.get(col)
        rows.append({
            "年度": safe_year(col),
            "売上高": fmt_yen(s),
            "売上総利益": fmt_yen(g),
            "営業利益": fmt_yen(o),
            "税引前利益": fmt_yen(p),
            "純利益": fmt_yen(n),
            "EPS": f"{num(e):,.2f}" if num(e) is not None else "不明",
            "営業利益率": pct(ratio(o,s)),
            "純利益率": pct(ratio(n,s)),
        })
        raw.append({"年度": safe_year(col), "売上高": num(s), "営業利益": num(o), "純利益": num(n)})

    if rows:
        st.dataframe(pd.DataFrame(rows).sort_values("年度"), hide_index=True, use_container_width=True)
        dfr = pd.DataFrame(raw).sort_values("年度").set_index("年度")

        st.markdown("##### 売上高")
        scale, unit = choose_unit(dfr["売上高"].dropna().values)
        fig, ax = plt.subplots(figsize=(8,3.5))
        ax.plot(dfr.index.astype(str), dfr["売上高"], marker="o")
        ax.yaxis.set_major_formatter(axis_fmt(scale))
        ax.set_ylabel(unit)
        ax.grid(True, alpha=.25)
        st.pyplot(fig, clear_figure=True)

        st.markdown("##### 利益")
        vals = pd.concat([dfr["営業利益"], dfr["純利益"]]).dropna().values
        scale, unit = choose_unit(vals)
        fig, ax = plt.subplots(figsize=(8,3.5))
        ax.plot(dfr.index.astype(str), dfr["営業利益"], marker="o", label="営業利益")
        ax.plot(dfr.index.astype(str), dfr["純利益"], marker="o", label="純利益")
        ax.yaxis.set_major_formatter(axis_fmt(scale))
        ax.set_ylabel(unit)
        ax.grid(True, alpha=.25)
        ax.legend()
        st.pyplot(fig, clear_figure=True)

        st.caption("データ元：Yahoo Finance。決算発表直後は公式開示より反映が遅れる可能性があります。")

# -------------------------
# Balance
# -------------------------
with tabs[2]:
    assets = get_row(balance, ["Total Assets"])
    equity = get_row(balance, ["Stockholders Equity", "Total Equity Gross Minority Interest"])
    ca = get_row(balance, ["Current Assets", "Total Current Assets"])
    cl = get_row(balance, ["Current Liabilities", "Total Current Liabilities"])
    debt = get_row(balance, ["Total Debt"])

    cols = sorted(set(assets.index) | set(equity.index))
    rows = []
    for col in cols:
        A,E,CA,CL,D = assets.get(col),equity.get(col),ca.get(col),cl.get(col),debt.get(col)
        rows.append({
            "年度": safe_year(col),
            "総資産": fmt_yen(A),
            "自己資本": fmt_yen(E),
            "自己資本比率": pct(ratio(E,A)),
            "流動資産": fmt_yen(CA),
            "流動負債": fmt_yen(CL),
            "流動比率": pct(ratio(CA,CL)),
            "有利子負債": fmt_yen(D),
        })
    if rows:
        st.dataframe(pd.DataFrame(rows).sort_values("年度"), hide_index=True, use_container_width=True)
        st.caption("データ元：Yahoo Finance。公式数値との照合は「公式開示」タブで確認してください。")

# -------------------------
# CF
# -------------------------
with tabs[3]:
    ocf = get_row(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    icf = get_row(cashflow, ["Investing Cash Flow", "Total Cashflows From Investing Activities"])
    fincf = get_row(cashflow, ["Financing Cash Flow", "Total Cash From Financing Activities"])
    capex = get_row(cashflow, ["Capital Expenditure", "Capital Expenditures"])
    fcf_direct = get_row(cashflow, ["Free Cash Flow"])

    cols = sorted(set(ocf.index) | set(icf.index) | set(fincf.index))
    rows, raw = [], []

    for col in cols:
        O,I,F,CAP = num(ocf.get(col)),num(icf.get(col)),num(fincf.get(col)),num(capex.get(col))
        FCF = num(fcf_direct.get(col))
        if FCF is None and O is not None and CAP is not None:
            FCF = O + CAP if CAP < 0 else O - CAP
        rows.append({
            "年度": safe_year(col),
            "営業CF": fmt_yen(O),
            "投資CF": fmt_yen(I),
            "財務CF": fmt_yen(F),
            "FCF": fmt_yen(FCF),
        })
        raw.append({"年度": safe_year(col),"営業CF":O,"投資CF":I,"財務CF":F,"FCF":FCF})

    if rows:
        st.dataframe(pd.DataFrame(rows).sort_values("年度"), hide_index=True, use_container_width=True)
        dfr = pd.DataFrame(raw).sort_values("年度").set_index("年度")
        vals = pd.concat([dfr["営業CF"],dfr["投資CF"],dfr["財務CF"]]).dropna().values
        scale, unit = choose_unit(vals)

        fig, ax = plt.subplots(figsize=(8,4))
        x=np.arange(len(dfr))
        w=.25
        for i,key in enumerate(["営業CF","投資CF","財務CF"]):
            ax.bar(x+(i-1)*w,dfr[key],width=w,label=key)
        ax.axhline(0,linewidth=.8)
        ax.set_xticks(x)
        ax.set_xticklabels(dfr.index.astype(str))
        ax.yaxis.set_major_formatter(axis_fmt(scale))
        ax.set_ylabel(unit)
        ax.grid(True,axis="y",alpha=.2)
        ax.legend()
        st.pyplot(fig,clear_figure=True)

        st.caption("FCFは取得元に値があればその値を使用し、なければ営業CF−設備投資額で計算します。")

# -------------------------
# Official disclosures
# -------------------------
with tabs[4]:
    st.markdown("#### EDINET公式開示")
    key = get_edinet_key()

    if not key:
        st.warning("EDINET APIキーがまだ設定されていません。設定すると、金融庁EDINETの最新提出書類をこの画面で確認できます。")
        st.code('EDINET_API_KEY = "あなたのAPIキー"', language="toml")
        st.caption("APIキーはGitHubに直接書かず、StreamlitのSecretsに保存してください。")
    else:
        with st.spinner("EDINETの最新提出書類を確認しています…"):
            filings = latest_edinet_filings(code4, key, days=45)
        if filings:
            st.dataframe(pd.DataFrame(filings), hide_index=True, use_container_width=True)
            st.success("上記は金融庁EDINET APIから取得した公式提出書類です。")
        else:
            st.info("直近45日で該当するEDINET提出書類は見つかりませんでした。")

    st.markdown("#### 最新決算・適時開示について")
    st.info(
        "TDnetの公式APIは契約が必要な有料サービスです。"
        "そのため、この無料版ではTDnetデータを自動取得していません。"
        "将来、有料APIまたはJ-Quants等を契約した場合に接続できる設計にします。"
    )

# -------------------------
# Notes
# -------------------------
with tabs[5]:
    st.markdown("#### データの読み方")
    st.write("• 株価：Yahoo Finance系データ。日本株では遅延する場合があります。")
    st.write("• 業績・財務・CF：Yahoo Financeに反映済みの財務データです。")
    st.write("• EDINET：金融庁の公式提出書類。APIキー設定後に最新書類を表示します。")
    st.write("• TDnet：最新の決算短信・適時開示に最適ですが、公式APIは契約制です。")
    st.warning("重要な投資判断では、会社IR・決算短信・有価証券報告書など一次資料との照合が必要です。")

st.divider()
st.caption(f"最終取得：{now_jst} / 株価基準日：{last_market_date}")
