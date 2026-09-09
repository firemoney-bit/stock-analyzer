
import streamlit as st
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
import plotly.graph_objects as go
from curl_cffi import requests as curl_requests

st.set_page_config(
    page_title="日本株分析 Ver.5",
    page_icon="📈",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
.block-container {
    padding-top: 1rem;
    padding-bottom: 4rem;
    max-width: 980px;
}
h1 {font-size: 1.65rem !important;}
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

st.title("📈 日本株分析 Ver.5")
st.caption("Yahoo Financeのcrumb依存を避け、EDINET公式開示も確認できる版")

# -------------------------
# 基本関数
# -------------------------
def jp_ticker(code: str) -> str:
    code = code.strip().upper()
    if code.endswith(".T"):
        return code
    return f"{code}.T" if code.isdigit() else code

def stock_code4(code: str) -> str:
    return code.replace(".T", "").strip()[:4]

def n(v):
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except:
        return None

def fmt_yen(v):
    v = n(v)
    if v is None:
        return "取得不可"
    a = abs(v)
    if a >= 1_000_000_000_000:
        return f"{v/1_000_000_000_000:,.2f}兆円"
    if a >= 100_000_000:
        return f"{v/100_000_000:,.1f}億円"
    if a >= 10_000:
        return f"{v/10_000:,.1f}万円"
    return f"{v:,.0f}円"

def fmt_ratio(v, suffix="倍"):
    v = n(v)
    return f"{v:,.2f}{suffix}" if v is not None else "取得不可"

def fmt_pct(v):
    v = n(v)
    return f"{v*100:,.2f}%" if v is not None else "取得不可"

def safe_raw(obj):
    if isinstance(obj, dict):
        if "raw" in obj:
            return obj.get("raw")
        if "reportedValue" in obj and isinstance(obj["reportedValue"], dict):
            return obj["reportedValue"].get("raw")
    return obj

# -------------------------
# Yahoo: crumb不要のエンドポイントを直接利用
# -------------------------
YH = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
        "Mobile/15E148 Safari/604.1"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.7,en;q=0.5",
}

def yahoo_get_json(url, params=None, timeout=20):
    last_error = None
    # query1 -> query2 の順に試す
    urls = [url]
    if "query1.finance.yahoo.com" in url:
        urls.append(url.replace("query1.finance.yahoo.com", "query2.finance.yahoo.com"))
    elif "query2.finance.yahoo.com" in url:
        urls.append(url.replace("query2.finance.yahoo.com", "query1.finance.yahoo.com"))

    for u in urls:
        try:
            r = curl_requests.get(
                u,
                params=params or {},
                headers=YH,
                impersonate="chrome",
                timeout=timeout,
            )
            if r.status_code == 200:
                return r.json(), None
            last_error = f"Yahoo HTTP {r.status_code}"
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
    return None, last_error

@st.cache_data(ttl=900)
def load_chart(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
    data, err = yahoo_get_json(
        url,
        params={
            "range": "10y",
            "interval": "1d",
            "events": "div,splits",
            "includeAdjustedClose": "true",
        },
    )
    if not data:
        return {}, pd.DataFrame(), [], err

    try:
        result = data["chart"]["result"][0]
        meta = result.get("meta", {}) or {}
        ts = result.get("timestamp", []) or []
        q = (result.get("indicators", {}).get("quote") or [{}])[0]

        hist = pd.DataFrame({
            "日時": pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Tokyo"),
            "始値": q.get("open", []),
            "高値": q.get("high", []),
            "安値": q.get("low", []),
            "終値": q.get("close", []),
            "出来高": q.get("volume", []),
        })
        hist = hist.dropna(subset=["終値"]).set_index("日時")

        divs = []
        events = result.get("events", {}) or {}
        for _, d in (events.get("dividends", {}) or {}).items():
            divs.append({
                "date": pd.to_datetime(d.get("date"), unit="s", utc=True).tz_convert("Asia/Tokyo"),
                "amount": n(d.get("amount")),
            })

        return meta, hist, divs, None
    except Exception as e:
        return {}, pd.DataFrame(), [], f"Yahoo応答解析エラー: {type(e).__name__}"

# Yahoo fundamentals-timeseries は quoteSummary/crumb を使わない
FUND_KEYS = [
    # valuation
    "trailingMarketCap",
    "trailingPeRatio",
    "trailingPbRatio",
    # income
    "annualTotalRevenue",
    "annualGrossProfit",
    "annualOperatingIncome",
    "annualPretaxIncome",
    "annualNetIncome",
    "annualDilutedEPS",
    # balance sheet
    "annualTotalAssets",
    "annualStockholdersEquity",
    "annualCurrentAssets",
    "annualCurrentLiabilities",
    "annualTotalDebt",
    # cash flow
    "annualOperatingCashFlow",
    "annualInvestingCashFlow",
    "annualFinancingCashFlow",
    "annualCapitalExpenditure",
    "annualFreeCashFlow",
]

@st.cache_data(ttl=1800)
def load_fundamentals(symbol):
    now = int(datetime.now(timezone.utc).timestamp())
    start = int((datetime.now(timezone.utc) - timedelta(days=365 * 6)).timestamp())
    url = (
        f"https://query1.finance.yahoo.com/ws/fundamentals-timeseries/"
        f"v1/finance/timeseries/{quote(symbol, safe='')}"
    )
    data, err = yahoo_get_json(
        url,
        params={
            "symbol": symbol,
            "type": ",".join(FUND_KEYS),
            "period1": start,
            "period2": now,
        },
        timeout=25,
    )
    if not data:
        return {}, err

    try:
        root = data.get("timeseries") or {}
        if root.get("error"):
            return {}, str(root.get("error"))

        out = {}
        for item in root.get("result", []) or []:
            meta = item.get("meta", {}) or {}
            typ = meta.get("type")
            if not typ:
                # 応答によってはデータキーから推定
                typ = next((k for k in item.keys() if k in FUND_KEYS), None)
            if not typ:
                continue

            values = item.get(typ, []) or []
            parsed = []
            for row in values:
                raw = safe_raw(row)
                if isinstance(row, dict):
                    raw = safe_raw(row.get("reportedValue", row))
                    date = row.get("asOfDate") or row.get("date")
                    period = row.get("periodType")
                else:
                    date, period = None, None
                parsed.append({"date": date, "period": period, "value": n(raw)})
            out[typ] = parsed

        return out, None
    except Exception as e:
        return {}, f"財務データ解析エラー: {type(e).__name__}: {e}"

def latest_value(fund, key):
    rows = fund.get(key, []) or []
    valid = [r for r in rows if n(r.get("value")) is not None]
    if not valid:
        return None
    valid.sort(key=lambda x: str(x.get("date") or ""))
    return n(valid[-1]["value"])

def annual_table(fund, mapping):
    years = {}
    for key, label in mapping.items():
        for row in fund.get(key, []) or []:
            if n(row.get("value")) is None:
                continue
            d = str(row.get("date") or "")
            year = d[:4] if len(d) >= 4 else "不明"
            years.setdefault(year, {})[label] = n(row.get("value"))
    if not years:
        return pd.DataFrame()
    df = pd.DataFrame([{"年度": y, **vals} for y, vals in years.items()])
    return df.sort_values("年度")

def plot_lines(df, columns, title, unit_kind="yen"):
    if df.empty:
        st.info("グラフ化できるデータがありません。")
        return

    fig = go.Figure()
    vals = []
    for col in columns:
        if col not in df.columns:
            continue
        vals += [abs(v) for v in df[col].dropna().tolist()]
    maxv = max(vals) if vals else 0

    if unit_kind == "yen":
        if maxv >= 1_000_000_000_000:
            scale, unit = 1_000_000_000_000, "兆円"
        elif maxv >= 100_000_000:
            scale, unit = 100_000_000, "億円"
        else:
            scale, unit = 1, "円"
    else:
        scale, unit = 1, ""

    for col in columns:
        if col not in df.columns:
            continue
        fig.add_trace(go.Scatter(
            x=df["年度"].astype(str),
            y=df[col] / scale,
            mode="lines+markers",
            name=col,
            hovertemplate=f"%{{x}}年<br>{col}: %{{y:,.2f}} {unit}<extra></extra>",
        ))

    fig.update_layout(
        title=title,
        yaxis_title=unit,
        xaxis_title="",
        height=360,
        margin=dict(l=10, r=10, t=45, b=10),
        legend_title_text="",
        hovermode="x unified",
    )
    st.plotly_chart(fig, width="stretch")

# -------------------------
# EDINET
# -------------------------
def get_edinet_key():
    try:
        return st.secrets["EDINET_API_KEY"]
    except Exception:
        return None

@st.cache_data(ttl=21600)
def latest_edinet_filings(code4, api_key, days=45):
    if not api_key:
        return [], "EDINET APIキー未設定"

    endpoint = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
    results = []

    for i in range(days):
        d = (datetime.now(JST).date() - timedelta(days=i)).isoformat()
        try:
            r = requests.get(
                endpoint,
                params={"date": d, "type": 2, "Subscription-Key": api_key},
                timeout=12,
            )
            if r.status_code != 200:
                continue
            js = r.json()
            for row in js.get("results", []) or []:
                sec = str(row.get("secCode") or "")
                if sec.startswith(code4):
                    results.append({
                        "提出日時": row.get("submitDateTime", ""),
                        "書類名": row.get("docDescription", ""),
                        "書類種別": row.get("docTypeCode", ""),
                        "EDINETコード": row.get("edinetCode", ""),
                        "書類ID": row.get("docID", ""),
                    })
            if len(results) >= 5:
                break
        except Exception:
            pass

    return results[:5], None

# -------------------------
# UI
# -------------------------
with st.form("stock_form"):
    code = st.text_input("銘柄コード", value="8267", placeholder="例：8267")
    submitted = st.form_submit_button("分析する", width="stretch", type="primary")

if not submitted:
    st.info("銘柄コードを入力して「分析する」を押してください。")
    st.stop()

symbol = jp_ticker(code)
code4 = stock_code4(code)

with st.spinner("株価・財務データを取得しています…"):
    meta, hist, dividends, chart_err = load_chart(symbol)
    fund, fund_err = load_fundamentals(symbol)

name = meta.get("longName") or meta.get("shortName") or meta.get("symbol") or symbol
price = n(meta.get("regularMarketPrice"))
if price is None and not hist.empty:
    price = n(hist["終値"].iloc[-1])

market_cap = latest_value(fund, "trailingMarketCap")
pe = latest_value(fund, "trailingPeRatio")
pb = latest_value(fund, "trailingPbRatio")

# 直近12か月配当利回りを株価から計算
dy = None
if price and dividends:
    cutoff = pd.Timestamp.now(tz="Asia/Tokyo") - pd.Timedelta(days=365)
    annual_div = sum(
        d["amount"] or 0
        for d in dividends
        if d["date"] >= cutoff and d["amount"] is not None
    )
    if annual_div > 0:
        dy = annual_div / price

last_market_date = (
    hist.index[-1].strftime("%Y/%m/%d")
    if not hist.empty else "取得不可"
)
now_jst = datetime.now(JST).strftime("%Y/%m/%d %H:%M")

st.subheader(name)

st.markdown(
    f"""
<div class="sourcebox">
<b>アプリ取得日時：</b>{now_jst}<br>
<b>株価データ基準日：</b>{last_market_date}<br>
<b>株価：</b>Yahoo Finance v8/chart（crumb不要エンドポイント）<br>
<b>財務・バリュエーション：</b>Yahoo Finance fundamentals-timeseries<br>
<b>公式開示：</b>金融庁 EDINET API
</div>
""",
    unsafe_allow_html=True,
)

# エラーは隠さない
if chart_err or fund_err:
    with st.expander("データ取得状況", expanded=(price is None)):
        if chart_err:
            st.error(f"株価取得: {chart_err}")
        else:
            st.success("株価取得: 成功")
        if fund_err:
            st.warning(f"財務データ取得: {fund_err}")
        else:
            st.success("財務データ取得: 成功")

c1, c2 = st.columns(2)
c1.metric("株価", f"{price:,.1f}円" if price is not None else "取得不可")
c2.metric("時価総額", fmt_yen(market_cap))

c1, c2, c3 = st.columns(3)
c1.metric("PER", fmt_ratio(pe))
c2.metric("PBR", fmt_ratio(pb))
c3.metric("配当利回り", fmt_pct(dy))

tabs = st.tabs(["株価", "業績", "財務", "CF", "公式開示", "データ情報"])

# 株価
with tabs[0]:
    if hist.empty:
        st.error("株価履歴を取得できませんでした。")
    else:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=hist.index,
            y=hist["終値"],
            mode="lines",
            name="終値",
            hovertemplate="%{x|%Y/%m/%d}<br>終値: %{y:,.1f}円<extra></extra>",
        ))
        fig.update_layout(
            title="株価推移（最大10年）",
            yaxis_title="株価（円）",
            xaxis_title="",
            height=400,
            margin=dict(l=10, r=10, t=45, b=10),
        )
        st.plotly_chart(fig, width="stretch")
        st.caption(f"Yahoo Finance / 最終データ: {last_market_date}")

# 業績
with tabs[1]:
    income_map = {
        "annualTotalRevenue": "売上高",
        "annualGrossProfit": "売上総利益",
        "annualOperatingIncome": "営業利益",
        "annualPretaxIncome": "税引前利益",
        "annualNetIncome": "純利益",
        "annualDilutedEPS": "EPS",
    }
    inc = annual_table(fund, income_map)
    if inc.empty:
        st.warning("業績データを取得できませんでした。")
    else:
        show = inc.copy()
        for col in ["売上高","売上総利益","営業利益","税引前利益","純利益"]:
            if col in show:
                show[col] = show[col].apply(fmt_yen)
        if "EPS" in show:
            show["EPS"] = show["EPS"].apply(lambda x: f"{x:,.2f}円" if n(x) is not None else "取得不可")
        st.dataframe(show, hide_index=True, width="stretch")
        plot_lines(inc, ["売上高"], "売上高")
        plot_lines(inc, ["営業利益","純利益"], "利益推移")

# 財務
with tabs[2]:
    bs_map = {
        "annualTotalAssets": "総資産",
        "annualStockholdersEquity": "自己資本",
        "annualCurrentAssets": "流動資産",
        "annualCurrentLiabilities": "流動負債",
        "annualTotalDebt": "有利子負債",
    }
    bs = annual_table(fund, bs_map)
    if bs.empty:
        st.warning("財務データを取得できませんでした。")
    else:
        raw = bs.copy()
        if "自己資本" in raw and "総資産" in raw:
            raw["自己資本比率"] = raw["自己資本"] / raw["総資産"]
        if "流動資産" in raw and "流動負債" in raw:
            raw["流動比率"] = raw["流動資産"] / raw["流動負債"]

        show = raw.copy()
        for col in ["総資産","自己資本","流動資産","流動負債","有利子負債"]:
            if col in show:
                show[col] = show[col].apply(fmt_yen)
        for col in ["自己資本比率","流動比率"]:
            if col in show:
                show[col] = show[col].apply(fmt_pct)

        st.dataframe(show, hide_index=True, width="stretch")
        plot_lines(raw, ["総資産","自己資本","有利子負債"], "財務推移")

# CF
with tabs[3]:
    cf_map = {
        "annualOperatingCashFlow": "営業CF",
        "annualInvestingCashFlow": "投資CF",
        "annualFinancingCashFlow": "財務CF",
        "annualCapitalExpenditure": "設備投資",
        "annualFreeCashFlow": "FCF",
    }
    cf = annual_table(fund, cf_map)
    if cf.empty:
        st.warning("キャッシュフローデータを取得できませんでした。")
    else:
        # FCFがない場合のみ 営業CF - 設備投資額（符号を考慮）
        if "FCF" not in cf.columns and "営業CF" in cf.columns and "設備投資" in cf.columns:
            cap = cf["設備投資"]
            cf["FCF"] = np.where(cap < 0, cf["営業CF"] + cap, cf["営業CF"] - cap)

        show = cf.copy()
        for col in ["営業CF","投資CF","財務CF","設備投資","FCF"]:
            if col in show:
                show[col] = show[col].apply(fmt_yen)
        st.dataframe(show, hide_index=True, width="stretch")
        plot_lines(cf, ["営業CF","投資CF","財務CF"], "キャッシュフロー推移")
        if "FCF" in cf:
            plot_lines(cf, ["FCF"], "フリーキャッシュフロー")
        st.caption("FCFは取得元の値を優先し、取得できない場合のみ営業CFと設備投資から計算します。")

# EDINET
with tabs[4]:
    st.markdown("#### 金融庁EDINET 公式提出書類")
    key = get_edinet_key()
    if not key:
        st.error("Streamlit SecretsにEDINET_API_KEYが見つかりません。")
    else:
        if st.button("直近45日の公式開示を確認", width="stretch"):
            with st.spinner("EDINETを確認しています…"):
                filings, ed_err = latest_edinet_filings(code4, key, days=45)
            if ed_err:
                st.error(ed_err)
            elif filings:
                st.dataframe(pd.DataFrame(filings), hide_index=True, width="stretch")
                st.success("金融庁EDINET APIから取得した公式データです。")
            else:
                st.info("直近45日では該当するEDINET提出書類が見つかりませんでした。")

# データ情報
with tabs[5]:
    st.markdown("#### このVer.5で変えたこと")
    st.write("・yfinanceの `Ticker.info` / quoteSummary を使わない")
    st.write("・株価はYahoo Financeのv8/chartを直接利用")
    st.write("・財務はfundamentals-timeseriesを直接利用")
    st.write("・ブラウザに近い通信方式でアクセスし、HTTP 429対策を強化")
    st.write("・Yahoo取得失敗時は「不明」で隠さず、エラー内容を表示")
    st.write("・Matplotlibを廃止し、Plotlyで日本語グラフを表示")
    st.write("・EDINET APIキーはStreamlit Secretsから読み込む")
    st.warning(
        "Yahoo Financeのエンドポイントは非公式で、将来仕様変更やアクセス制限が起こる可能性があります。"
        "重要な投資判断ではEDINET・会社IR等の一次資料を確認してください。"
    )

st.divider()
st.caption(f"取得日時: {now_jst} / 株価基準日: {last_market_date}")
