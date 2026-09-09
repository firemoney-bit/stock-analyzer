
import streamlit as st
import pandas as pd
import numpy as np
import requests
import io
import zipfile
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
import plotly.graph_objects as go
from curl_cffi import requests as curl_requests

st.set_page_config(
    page_title="日本株分析 Ver.11",
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

st.title("📈 日本株分析 Ver.11")
st.caption("公開情報を整理・可視化し、あらかじめ定めた計算ルールで指標を評価する情報提供ツールです。個別の売買を推奨するものではありません。")


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

            # Yahooの応答によって meta["type"] が文字列ではなく
            # リストで返ることがあるため、データキーから確実に判定する
            candidate_keys = [k for k in item.keys() if k in FUND_KEYS]

            if isinstance(typ, list):
                typ = next((x for x in typ if isinstance(x, str) and x in FUND_KEYS), None)
            elif not isinstance(typ, str):
                typ = None

            if typ not in FUND_KEYS:
                typ = candidate_keys[0] if candidate_keys else None

            if not typ:
                continue

            values = item.get(typ, []) or []
            if not isinstance(values, list):
                values = [values]

            parsed = []
            for row in values:
                if isinstance(row, dict):
                    raw = safe_raw(row.get("reportedValue", row))
                    date = row.get("asOfDate") or row.get("date")
                    period = row.get("periodType")
                else:
                    raw = safe_raw(row)
                    date, period = None, None

                parsed.append({
                    "date": date,
                    "period": period,
                    "value": n(raw)
                })

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
        height=400,
        margin=dict(l=10, r=10, t=50, b=15),
        legend_title_text="",
        hovermode="x unified",
    )
    st.plotly_chart(fig, width="stretch")


# -------------------------
# 分析指標・スコア
# -------------------------
def latest_two(df, col):
    if df is None or df.empty or col not in df.columns:
        return None, None
    tmp = df[["年度", col]].dropna().copy()
    if len(tmp) == 0:
        return None, None
    tmp = tmp.sort_values("年度")
    latest = n(tmp.iloc[-1][col])
    prev = n(tmp.iloc[-2][col]) if len(tmp) >= 2 else None
    return latest, prev

def growth_rate(latest, prev):
    latest, prev = n(latest), n(prev)
    if latest is None or prev is None or prev == 0:
        return None
    return latest / prev - 1

def avg_balance(latest, prev):
    latest, prev = n(latest), n(prev)
    if latest is None:
        return None
    if prev is None:
        return latest
    return (latest + prev) / 2

def clamp(x, lo=0, hi=100):
    return max(lo, min(hi, x))

def score_range(value, rules, default=50):
    """
    rules: [(threshold, score), ...] evaluated descending by threshold.
    """
    value = n(value)
    if value is None:
        return default
    for threshold, score in rules:
        if value >= threshold:
            return score
    return rules[-1][1] if rules else default

def score_lower_better(value, rules, default=50):
    """
    rules: [(threshold, score), ...] evaluated ascending by threshold.
    """
    value = n(value)
    if value is None:
        return default
    for threshold, score in rules:
        if value <= threshold:
            return score
    return rules[-1][1] if rules else default

def score_label(score):
    if score >= 80:
        return "非常に良い"
    if score >= 65:
        return "良い"
    if score >= 50:
        return "標準"
    if score >= 35:
        return "注意"
    return "弱い"

def build_analysis(inc, bs, dividends, price, pe, pb):
    metrics = {}

    # 最新・前期
    rev, rev_prev = latest_two(inc, "売上高")
    op, op_prev = latest_two(inc, "営業利益")
    net, net_prev = latest_two(inc, "純利益")
    eps, eps_prev = latest_two(inc, "EPS")

    assets, assets_prev = latest_two(bs, "総資産")
    equity, equity_prev = latest_two(bs, "自己資本")
    debt, _ = latest_two(bs, "有利子負債")

    # 指標
    metrics["売上成長率"] = growth_rate(rev, rev_prev)
    metrics["営業利益成長率"] = growth_rate(op, op_prev)
    metrics["EPS成長率"] = growth_rate(eps, eps_prev)
    metrics["営業利益率"] = (op / rev) if op is not None and rev not in (None, 0) else None
    metrics["純利益率"] = (net / rev) if net is not None and rev not in (None, 0) else None

    avg_eq = avg_balance(equity, equity_prev)
    avg_assets = avg_balance(assets, assets_prev)
    metrics["ROE"] = (net / avg_eq) if net is not None and avg_eq not in (None, 0) else None
    metrics["ROA"] = (net / avg_assets) if net is not None and avg_assets not in (None, 0) else None
    metrics["自己資本比率"] = (equity / assets) if equity is not None and assets not in (None, 0) else None
    metrics["負債自己資本倍率"] = (debt / equity) if debt is not None and equity not in (None, 0) else None

    # 配当・配当性向
    annual_div = None
    if dividends:
        cutoff = pd.Timestamp.now(tz="Asia/Tokyo") - pd.Timedelta(days=365)
        annual_div = sum(
            d["amount"] or 0
            for d in dividends
            if d["date"] >= cutoff and d["amount"] is not None
        )
    metrics["年間配当"] = annual_div
    metrics["配当利回り"] = (annual_div / price) if annual_div and price else None
    metrics["配当性向"] = (annual_div / eps) if annual_div is not None and eps not in (None, 0) else None

    # 収益性 0-100
    profitability = np.mean([
        score_range(metrics["ROE"], [(0.15, 95), (0.10, 80), (0.07, 65), (0.04, 50), (0.00, 35), (-999, 15)]),
        score_range(metrics["ROA"], [(0.08, 95), (0.05, 80), (0.03, 65), (0.01, 50), (0.00, 35), (-999, 15)]),
        score_range(metrics["営業利益率"], [(0.20, 95), (0.12, 80), (0.08, 65), (0.04, 50), (0.00, 35), (-999, 15)]),
    ])

    # 成長性
    growth = np.mean([
        score_range(metrics["売上成長率"], [(0.15, 95), (0.08, 80), (0.03, 65), (0.00, 50), (-0.05, 35), (-999, 15)]),
        score_range(metrics["営業利益成長率"], [(0.20, 95), (0.10, 80), (0.03, 65), (0.00, 50), (-0.10, 35), (-999, 15)]),
        score_range(metrics["EPS成長率"], [(0.20, 95), (0.10, 80), (0.03, 65), (0.00, 50), (-0.10, 35), (-999, 15)]),
    ])

    # 財務安全性
    safety = np.mean([
        score_range(metrics["自己資本比率"], [(0.60, 95), (0.45, 80), (0.30, 65), (0.20, 50), (0.10, 35), (-999, 15)]),
        score_lower_better(metrics["負債自己資本倍率"], [(0.3, 95), (0.7, 80), (1.2, 65), (2.0, 50), (3.0, 35), (999, 15)]),
    ])

    # 株主還元
    payout = metrics["配当性向"]
    if payout is None:
        payout_score = 50
    elif payout < 0:
        payout_score = 15
    elif 0.25 <= payout <= 0.50:
        payout_score = 90
    elif 0.15 <= payout < 0.25 or 0.50 < payout <= 0.70:
        payout_score = 70
    elif payout <= 1.0:
        payout_score = 50
    else:
        payout_score = 25

    shareholder = np.mean([
        score_range(metrics["配当利回り"], [(0.04, 90), (0.03, 80), (0.02, 65), (0.01, 50), (0.00, 35), (-999, 15)]),
        payout_score,
    ])

    # 割安性（業種差が大きいため参考値）
    valuation = np.mean([
        score_lower_better(pe, [(10, 90), (15, 80), (20, 65), (30, 50), (45, 35), (9999, 20)]),
        score_lower_better(pb, [(0.8, 90), (1.2, 80), (2.0, 65), (3.0, 50), (5.0, 35), (9999, 20)]),
    ])

    scores = {
        "収益性": round(float(profitability)),
        "成長性": round(float(growth)),
        "財務安全性": round(float(safety)),
        "株主還元": round(float(shareholder)),
        "割安性": round(float(valuation)),
    }
    overall = round(
        scores["収益性"] * 0.25
        + scores["成長性"] * 0.25
        + scores["財務安全性"] * 0.20
        + scores["株主還元"] * 0.10
        + scores["割安性"] * 0.20
    )

    return metrics, scores, overall

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
# Ver.9 高度分析
# -------------------------
def cagr_from_df(df, col):
    if df is None or df.empty or col not in df.columns:
        return None
    tmp = df[["年度", col]].dropna().copy()
    if len(tmp) < 2:
        return None
    tmp = tmp.sort_values("年度")
    first = n(tmp.iloc[0][col])
    last = n(tmp.iloc[-1][col])
    periods = len(tmp) - 1
    if first is None or last is None or first <= 0 or last <= 0 or periods <= 0:
        return None
    return (last / first) ** (1 / periods) - 1

def latest_margin_trend(inc):
    if inc is None or inc.empty or "売上高" not in inc.columns:
        return None
    tmp = inc.sort_values("年度").copy()
    if "営業利益" not in tmp.columns or len(tmp) < 2:
        return None
    tmp["営業利益率"] = tmp["営業利益"] / tmp["売上高"]
    vals = tmp["営業利益率"].replace([np.inf, -np.inf], np.nan).dropna()
    if len(vals) < 2:
        return None
    return float(vals.iloc[-1] - vals.iloc[-2])

def calc_price_stats(hist):
    out = {
        "52週高値": None,
        "52週安値": None,
        "52週高値乖離": None,
        "25日線": None,
        "75日線": None,
        "25日線乖離": None,
        "75日線乖離": None,
        "年率ボラ": None,
        "最大ドローダウン": None,
        "1か月騰落率": None,
        "3か月騰落率": None,
        "1年騰落率": None,
    }
    if hist is None or hist.empty or "終値" not in hist.columns:
        return out

    close = hist["終値"].dropna()
    if close.empty:
        return out

    latest = float(close.iloc[-1])

    def ret(days):
        if len(close) <= days:
            return None
        prev = float(close.iloc[-days-1])
        return latest / prev - 1 if prev != 0 else None

    last_252 = close.tail(252)
    out["52週高値"] = float(last_252.max()) if not last_252.empty else None
    out["52週安値"] = float(last_252.min()) if not last_252.empty else None
    if out["52週高値"]:
        out["52週高値乖離"] = latest / out["52週高値"] - 1

    if len(close) >= 25:
        out["25日線"] = float(close.tail(25).mean())
        out["25日線乖離"] = latest / out["25日線"] - 1
    if len(close) >= 75:
        out["75日線"] = float(close.tail(75).mean())
        out["75日線乖離"] = latest / out["75日線"] - 1

    daily = close.pct_change().dropna()
    if len(daily) >= 20:
        out["年率ボラ"] = float(daily.std() * np.sqrt(252))

    running_max = close.cummax()
    dd = close / running_max - 1
    if not dd.empty:
        out["最大ドローダウン"] = float(dd.min())

    out["1か月騰落率"] = ret(21)
    out["3か月騰落率"] = ret(63)
    out["1年騰落率"] = ret(252)
    return out

def build_risk_flags(metrics, scores, price_stats, cf):
    flags = []

    if scores.get("財務安全性", 50) < 35:
        flags.append(("高", "財務安全性", "財務安全性スコアが低水準です。自己資本比率や有利子負債の確認が必要です。"))

    roe = metrics.get("ROE")
    if roe is not None and roe < 0:
        flags.append(("高", "収益性", "ROEがマイナスです。赤字または自己資本に対して利益が不足しています。"))

    payout = metrics.get("配当性向")
    if payout is not None and payout > 1:
        flags.append(("中", "配当", "配当性向が100%を超えています。配当の持続可能性を確認してください。"))

    if price_stats.get("最大ドローダウン") is not None and price_stats["最大ドローダウン"] <= -0.40:
        flags.append(("中", "株価", f"過去データで最大ドローダウンが{price_stats['最大ドローダウン']*100:.1f}%です。値動きが大きい銘柄です。"))

    if price_stats.get("年率ボラ") is not None and price_stats["年率ボラ"] >= 0.40:
        flags.append(("中", "株価", f"年率換算ボラティリティが{price_stats['年率ボラ']*100:.1f}%と高めです。"))

    if cf is not None and not cf.empty and "FCF" in cf.columns:
        f = cf.sort_values("年度")["FCF"].dropna()
        if len(f) >= 2 and (f.tail(2) < 0).all():
            flags.append(("高", "CF", "FCFが2期連続でマイナスです。投資負担や資金流出の要因確認が必要です。"))
        elif len(f) >= 1 and f.iloc[-1] < 0:
            flags.append(("中", "CF", "直近期のFCFがマイナスです。設備投資や一時要因を確認してください。"))

    if metrics.get("売上成長率") is not None and metrics["売上成長率"] < -0.05:
        flags.append(("中", "成長性", f"直近売上高が前年比{metrics['売上成長率']*100:.1f}%減少しています。"))

    if metrics.get("EPS成長率") is not None and metrics["EPS成長率"] < -0.10:
        flags.append(("中", "成長性", f"直近EPSが前年比{metrics['EPS成長率']*100:.1f}%減少しています。"))

    if not flags:
        flags.append(("低", "総合", "現時点の自動チェックでは大きな警戒シグナルは検出されませんでした。"))

    rank = {"高": 0, "中": 1, "低": 2}
    return sorted(flags, key=lambda x: rank.get(x[0], 9))

def investment_summary_text(overall, scores, metrics, price_stats):
    strengths = []
    weaknesses = []

    for k, v in scores.items():
        if v >= 65:
            strengths.append(f"{k}（{v}点）")
        elif v < 50:
            weaknesses.append(f"{k}（{v}点）")

    momentum = None
    if price_stats.get("75日線乖離") is not None:
        if price_stats["75日線乖離"] >= 0.05:
            momentum = "株価は75日移動平均を上回っており、中期モメンタムは強めです。"
        elif price_stats["75日線乖離"] <= -0.05:
            momentum = "株価は75日移動平均を下回っており、中期モメンタムは弱めです。"
        else:
            momentum = "株価は75日移動平均付近で推移しています。"

    if overall >= 80:
        headline = "長期保有候補として非常に高い評価"
    elif overall >= 65:
        headline = "長期保有候補として良好"
    elif overall >= 50:
        headline = "中立。強みと弱みの確認が必要"
    elif overall >= 35:
        headline = "注意。弱点の改善確認が必要"
    else:
        headline = "慎重判断。現状では弱い評価"

    return {
        "headline": headline,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "momentum": momentum,
    }

# -------------------------
# EDINET 書類本文の取得・重要度分析
# -------------------------
def disclosure_priority(title):
    t = str(title or "")
    high = [
        "業績予想", "配当予想", "自己株", "公開買付", "臨時報告",
        "有価証券報告書", "四半期報告書", "半期報告書", "訂正"
    ]
    medium = ["大量保有", "変更報告", "確認書", "内部統制"]
    if any(k in t for k in high):
        return "高"
    if any(k in t for k in medium):
        return "中"
    return "低"

def disclosure_category(title):
    t = str(title or "")
    if "自己株" in t:
        return "自己株式"
    if "臨時報告" in t:
        return "臨時報告"
    if "有価証券報告書" in t:
        return "有価証券報告書"
    if "四半期報告書" in t or "半期報告書" in t:
        return "決算・業績"
    if "大量保有" in t or "変更報告" in t:
        return "大株主"
    if "訂正" in t:
        return "訂正"
    return "その他"

def disclosure_meaning(title):
    cat = disclosure_category(title)
    meanings = {
        "自己株式": "自己株買いの実施状況を確認できます。取得規模や進捗は需給・1株価値に影響する可能性があります。",
        "臨時報告": "重要な企業イベントが発生した可能性があります。内容によって株価影響が大きく異なります。",
        "有価証券報告書": "業績・財務・事業リスク・設備投資などの一次情報を確認する中核資料です。",
        "決算・業績": "売上・利益・財務・事業進捗を確認する重要資料です。",
        "大株主": "大株主の持分変化を確認できます。需給や経営への影響を見る材料になります。",
        "訂正": "過去開示の修正です。修正内容が業績・財務数値に関係する場合は要確認です。",
        "その他": "確認上の重要度は書類内容によります。"
    }
    return meanings.get(cat, meanings["その他"])

@st.cache_data(ttl=21600)
def fetch_edinet_document_text(doc_id, api_key):
    if not api_key:
        return "", "EDINET APIキー未設定"
    if not doc_id:
        return "", "書類IDがありません"

    url = f"https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}"
    try:
        r = requests.get(
            url,
            params={"type": 1, "Subscription-Key": api_key},
            timeout=30,
        )
        if r.status_code != 200:
            return "", f"EDINET書類取得エラー: HTTP {r.status_code}"

        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = z.namelist()

        # 本文に近いファイルを優先
        preferred = [
            n for n in names
            if ("PublicDoc" in n or "XBRL/PublicDoc" in n)
            and n.lower().endswith((".htm", ".html"))
        ]
        if not preferred:
            preferred = [n for n in names if n.lower().endswith((".htm", ".html"))]

        texts = []
        for name in preferred[:8]:
            try:
                raw = z.read(name)
                soup = BeautifulSoup(raw, "html.parser")
                txt = soup.get_text(" ", strip=True)
                if txt:
                    texts.append(txt)
            except Exception:
                continue

        text = "\n".join(texts)
        if not text:
            return "", "本文テキストを抽出できませんでした"

        # Streamlit上で扱いやすいよう上限
        return text[:250000], None

    except zipfile.BadZipFile:
        return "", "EDINETから取得した書類を展開できませんでした"
    except Exception as e:
        return "", f"EDINET本文取得エラー: {type(e).__name__}: {e}"

def keyword_context(text, keyword, radius=90):
    if not text:
        return None
    idx = text.find(keyword)
    if idx < 0:
        return None
    s = max(0, idx - radius)
    e = min(len(text), idx + len(keyword) + radius)
    snippet = text[s:e].replace("\n", " ")
    return "…" + snippet + "…"

def analyze_edinet_text(title, text):
    title = str(title or "")
    t = text or ""

    findings = []
    impact = "中立"
    importance = disclosure_priority(title)

    # 株価インパクトに直結しやすい表現
    positive_words = ["上方修正", "増配", "自己株式取得", "自己株式の取得", "過去最高", "増益"]
    negative_words = ["下方修正", "減配", "赤字", "減損", "特別損失", "業績悪化", "債務超過"]

    pos_hits = [w for w in positive_words if w in t]
    neg_hits = [w for w in negative_words if w in t]

    if pos_hits and not neg_hits:
        impact = "ポジティブ寄り"
    elif neg_hits and not pos_hits:
        impact = "ネガティブ寄り"
    elif pos_hits and neg_hits:
        impact = "材料混在"

    topics = [
        ("業績予想", ["業績予想", "通期予想", "連結業績予想"]),
        ("配当", ["配当予想", "配当金", "増配", "減配"]),
        ("自己株式", ["自己株式取得", "自己株式の取得", "取得した株式"]),
        ("特別損益", ["特別利益", "特別損失", "減損"]),
        ("資本政策", ["株式分割", "新株予約権", "第三者割当"]),
        ("M&A・事業再編", ["合併", "会社分割", "株式交換", "事業譲渡", "公開買付"]),
    ]

    for label, kws in topics:
        for kw in kws:
            snip = keyword_context(t, kw)
            if snip:
                findings.append((label, kw, snip))
                break

    if not findings:
        # タイトルベースで最低限の意味付け
        findings.append(("書類種別", disclosure_category(title), disclosure_meaning(title)))

    return {
        "重要度": importance,
        "株価影響": impact,
        "カテゴリ": disclosure_category(title),
        "要点": findings[:6],
        "ポジティブ語": pos_hits,
        "ネガティブ語": neg_hits,
    }


# -------------------------
# Ver.10 同業他社比較
# -------------------------
@st.cache_data(ttl=1800)
def load_peer_snapshot(code):
    symbol = jp_ticker(code)
    meta, hist, dividends, chart_err = load_chart(symbol)
    fund, fund_err = load_fundamentals(symbol)

    price = n(meta.get("regularMarketPrice"))
    if price is None and hist is not None and not hist.empty:
        price = n(hist["終値"].iloc[-1])

    pe = latest_value(fund, "trailingPeRatio")
    pb = latest_value(fund, "trailingPbRatio")
    market_cap = latest_value(fund, "trailingMarketCap")

    inc = annual_table(fund, {
        "annualTotalRevenue": "売上高",
        "annualOperatingIncome": "営業利益",
        "annualNetIncome": "純利益",
        "annualDilutedEPS": "EPS",
    })
    bs = annual_table(fund, {
        "annualTotalAssets": "総資産",
        "annualStockholdersEquity": "自己資本",
        "annualTotalDebt": "有利子負債",
    })

    metrics, scores, overall = build_analysis(
        inc, bs, dividends, price, pe, pb
    )

    name = (
        meta.get("longName")
        or meta.get("shortName")
        or meta.get("symbol")
        or symbol
    )

    return {
        "コード": code.replace(".T", ""),
        "銘柄名": name,
        "株価": price,
        "時価総額": market_cap,
        "PER": pe,
        "PBR": pb,
        "配当利回り": metrics.get("配当利回り"),
        "ROE": metrics.get("ROE"),
        "ROA": metrics.get("ROA"),
        "営業利益率": metrics.get("営業利益率"),
        "売上成長率": metrics.get("売上成長率"),
        "EPS成長率": metrics.get("EPS成長率"),
        "自己資本比率": metrics.get("自己資本比率"),
        "総合スコア": overall,
        "収益性": scores.get("収益性"),
        "成長性": scores.get("成長性"),
        "財務安全性": scores.get("財務安全性"),
        "株主還元": scores.get("株主還元"),
        "割安性": scores.get("割安性"),
        "error": chart_err or fund_err,
    }

def format_peer_df(rows):
    out = []
    for r in rows:
        out.append({
            "コード": r["コード"],
            "銘柄名": r["銘柄名"],
            "株価": f"{r['株価']:,.1f}円" if r["株価"] is not None else "取得不可",
            "時価総額": fmt_yen(r["時価総額"]),
            "PER": f"{r['PER']:.2f}倍" if r["PER"] is not None else "取得不可",
            "PBR": f"{r['PBR']:.2f}倍" if r["PBR"] is not None else "取得不可",
            "配当利回り": f"{r['配当利回り']*100:.2f}%" if r["配当利回り"] is not None else "取得不可",
            "ROE": f"{r['ROE']*100:.2f}%" if r["ROE"] is not None else "取得不可",
            "営業利益率": f"{r['営業利益率']*100:.2f}%" if r["営業利益率"] is not None else "取得不可",
            "売上成長率": f"{r['売上成長率']*100:.2f}%" if r["売上成長率"] is not None else "取得不可",
            "EPS成長率": f"{r['EPS成長率']*100:.2f}%" if r["EPS成長率"] is not None else "取得不可",
            "総合スコア": r["総合スコア"],
        })
    return pd.DataFrame(out)

# -------------------------
# UI
# -------------------------
if "analyzed" not in st.session_state:
    st.session_state.analyzed = False
if "stock_code" not in st.session_state:
    st.session_state.stock_code = "8267"

with st.form("stock_form"):
    code_input = st.text_input(
        "銘柄コード",
        value=st.session_state.stock_code,
        placeholder="例：8267"
    )
    submitted = st.form_submit_button("分析する", width="stretch", type="primary")

if submitted:
    st.session_state.stock_code = code_input.strip()
    st.session_state.analyzed = True

if not st.session_state.analyzed:
    st.info("銘柄コードを入力して「分析する」を押してください。")
    st.stop()

code = st.session_state.stock_code
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

tabs = st.tabs(["サマリー", "株価", "業績", "財務", "CF", "分析", "比較", "公式開示", "利用・法務"])


# Ver.9 共通分析データ
income_map_summary = {
    "annualTotalRevenue": "売上高",
    "annualOperatingIncome": "営業利益",
    "annualNetIncome": "純利益",
    "annualDilutedEPS": "EPS",
}
bs_map_summary = {
    "annualTotalAssets": "総資産",
    "annualStockholdersEquity": "自己資本",
    "annualTotalDebt": "有利子負債",
}
cf_map_summary = {
    "annualOperatingCashFlow": "営業CF",
    "annualInvestingCashFlow": "投資CF",
    "annualFinancingCashFlow": "財務CF",
    "annualCapitalExpenditure": "設備投資",
    "annualFreeCashFlow": "FCF",
}

inc_summary = annual_table(fund, income_map_summary)
bs_summary = annual_table(fund, bs_map_summary)
cf_summary = annual_table(fund, cf_map_summary)

if (
    not cf_summary.empty
    and "FCF" not in cf_summary.columns
    and "営業CF" in cf_summary.columns
    and "設備投資" in cf_summary.columns
):
    cap = cf_summary["設備投資"]
    cf_summary["FCF"] = np.where(
        cap < 0,
        cf_summary["営業CF"] + cap,
        cf_summary["営業CF"] - cap
    )

summary_metrics, summary_scores, summary_overall = build_analysis(
    inc_summary, bs_summary, dividends, price, pe, pb
)
price_stats = calc_price_stats(hist)
risk_flags = build_risk_flags(
    summary_metrics, summary_scores, price_stats, cf_summary
)
summary_text = investment_summary_text(
    summary_overall, summary_scores, summary_metrics, price_stats
)

# サマリー
with tabs[0]:
    st.markdown("#### 分析サマリー")

    c1, c2 = st.columns(2)
    c1.metric("総合スコア", f"{summary_overall} / 100")
    c2.metric("総合評価", score_label(summary_overall))
    st.progress(summary_overall / 100)
    st.markdown(f"**{summary_text['headline']}**")

    if summary_text["strengths"]:
        st.success("強み：" + "、".join(summary_text["strengths"]))
    if summary_text["weaknesses"]:
        st.warning("弱み：" + "、".join(summary_text["weaknesses"]))
    if summary_text["momentum"]:
        st.info(summary_text["momentum"])

    st.markdown("##### 株価ポジション")
    p1, p2, p3 = st.columns(3)
    p1.metric(
        "52週高値から",
        f"{price_stats['52週高値乖離']*100:.1f}%"
        if price_stats["52週高値乖離"] is not None else "取得不可"
    )
    p2.metric(
        "75日線乖離",
        f"{price_stats['75日線乖離']*100:.1f}%"
        if price_stats["75日線乖離"] is not None else "取得不可"
    )
    p3.metric(
        "1年騰落率",
        f"{price_stats['1年騰落率']*100:.1f}%"
        if price_stats["1年騰落率"] is not None else "取得不可"
    )

    st.markdown("##### 中長期成長率")
    rev_cagr = cagr_from_df(inc_summary, "売上高")
    op_cagr = cagr_from_df(inc_summary, "営業利益")
    eps_cagr = cagr_from_df(inc_summary, "EPS")

    g1, g2, g3 = st.columns(3)
    g1.metric("売上CAGR", f"{rev_cagr*100:.1f}%" if rev_cagr is not None else "取得不可")
    g2.metric("営業利益CAGR", f"{op_cagr*100:.1f}%" if op_cagr is not None else "取得不可")
    g3.metric("EPS CAGR", f"{eps_cagr*100:.1f}%" if eps_cagr is not None else "取得不可")

    st.markdown("##### リスクチェック")
    risk_df = pd.DataFrame(
        [{"重要度": a, "項目": b, "内容": c} for a, b, c in risk_flags]
    )
    st.dataframe(risk_df, hide_index=True, width="stretch")

    st.caption(
        "サマリーは取得済みの市場データ・財務データを機械的に整理した参考評価です。"
        "最終判断では決算短信・有価証券報告書・会社IRを確認してください。"
    )


# 株価
with tabs[1]:
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

        ma25 = hist["終値"].rolling(25).mean()
        ma75 = hist["終値"].rolling(75).mean()

        fig.add_trace(go.Scatter(
            x=hist.index,
            y=ma25,
            mode="lines",
            name="25日移動平均",
            hovertemplate="%{x|%Y/%m/%d}<br>25日線: %{y:,.1f}円<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=hist.index,
            y=ma75,
            mode="lines",
            name="75日移動平均",
            hovertemplate="%{x|%Y/%m/%d}<br>75日線: %{y:,.1f}円<extra></extra>",
        ))
        fig.update_layout(
            title="株価推移（最大10年）",
            yaxis_title="株価（円）",
            xaxis_title="",
            height=400,
            margin=dict(l=10, r=10, t=45, b=10),
        )
        st.plotly_chart(fig, width="stretch")

        st.markdown("##### 株価指標")
        s1, s2, s3 = st.columns(3)
        s1.metric(
            "52週高値",
            f"{price_stats['52週高値']:,.1f}円" if price_stats["52週高値"] is not None else "取得不可"
        )
        s2.metric(
            "52週安値",
            f"{price_stats['52週安値']:,.1f}円" if price_stats["52週安値"] is not None else "取得不可"
        )
        s3.metric(
            "年率ボラ",
            f"{price_stats['年率ボラ']*100:.1f}%" if price_stats["年率ボラ"] is not None else "取得不可"
        )

        s1, s2, s3 = st.columns(3)
        s1.metric(
            "1か月",
            f"{price_stats['1か月騰落率']*100:.1f}%" if price_stats["1か月騰落率"] is not None else "取得不可"
        )
        s2.metric(
            "3か月",
            f"{price_stats['3か月騰落率']*100:.1f}%" if price_stats["3か月騰落率"] is not None else "取得不可"
        )
        s3.metric(
            "最大下落率",
            f"{price_stats['最大ドローダウン']*100:.1f}%" if price_stats["最大ドローダウン"] is not None else "取得不可"
        )

# 業績
with tabs[2]:
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
with tabs[3]:
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

        # 財務グラフ：金額＋自己資本比率（右軸）
        fig = go.Figure()

        amount_cols = [c for c in ["総資産", "自己資本", "有利子負債"] if c in raw.columns]
        amount_vals = []
        for c in amount_cols:
            amount_vals += [abs(v) for v in raw[c].dropna().tolist()]

        maxv = max(amount_vals) if amount_vals else 0
        if maxv >= 1_000_000_000_000:
            scale, unit = 1_000_000_000_000, "兆円"
        elif maxv >= 100_000_000:
            scale, unit = 100_000_000, "億円"
        else:
            scale, unit = 1, "円"

        for c in amount_cols:
            fig.add_trace(go.Bar(
                x=raw["年度"].astype(str),
                y=raw[c] / scale,
                name=c,
                hovertemplate=f"%{{x}}年<br>{c}: %{{y:,.2f}} {unit}<extra></extra>",
            ))

        if "自己資本比率" in raw.columns:
            fig.add_trace(go.Scatter(
                x=raw["年度"].astype(str),
                y=raw["自己資本比率"] * 100,
                mode="lines+markers",
                name="自己資本比率",
                yaxis="y2",
                hovertemplate="%{x}年<br>自己資本比率: %{y:,.2f}%<extra></extra>",
            ))

        fig.update_layout(
            title="財務推移",
            barmode="group",
            yaxis=dict(title=unit),
            yaxis2=dict(
                title="自己資本比率（%）",
                overlaying="y",
                side="right",
                showgrid=False,
            ),
            xaxis_title="",
            height=430,
            margin=dict(l=10, r=10, t=45, b=10),
            legend_title_text="",
        )
        st.plotly_chart(fig, width="stretch")

# CF
with tabs[4]:
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

        # CFグラフ：営業CF・投資CF・財務CF・FCFを1枚に統合
        fig = go.Figure()

        cf_cols = [c for c in ["営業CF", "投資CF", "財務CF", "FCF"] if c in cf.columns]
        cf_vals = []
        for c in cf_cols:
            cf_vals += [abs(v) for v in cf[c].dropna().tolist()]

        maxv = max(cf_vals) if cf_vals else 0
        if maxv >= 1_000_000_000_000:
            scale, unit = 1_000_000_000_000, "兆円"
        elif maxv >= 100_000_000:
            scale, unit = 100_000_000, "億円"
        else:
            scale, unit = 1, "円"

        for c in ["営業CF", "投資CF", "財務CF"]:
            if c in cf.columns:
                fig.add_trace(go.Bar(
                    x=cf["年度"].astype(str),
                    y=cf[c] / scale,
                    name=c,
                    hovertemplate=f"%{{x}}年<br>{c}: %{{y:,.2f}} {unit}<extra></extra>",
                ))

        if "FCF" in cf.columns:
            fig.add_trace(go.Scatter(
                x=cf["年度"].astype(str),
                y=cf["FCF"] / scale,
                mode="lines+markers",
                name="FCF",
                hovertemplate=f"%{{x}}年<br>FCF: %{{y:,.2f}} {unit}<extra></extra>",
            ))

        fig.update_layout(
            title="キャッシュフロー推移",
            barmode="group",
            yaxis_title=unit,
            xaxis_title="",
            height=430,
            margin=dict(l=10, r=10, t=45, b=10),
            legend_title_text="",
        )
        st.plotly_chart(fig, width="stretch")

        st.caption("FCFは取得元の値を優先し、取得できない場合のみ営業CFと設備投資から計算します。")

# 分析
with tabs[5]:
    income_map2 = {
        "annualTotalRevenue": "売上高",
        "annualOperatingIncome": "営業利益",
        "annualNetIncome": "純利益",
        "annualDilutedEPS": "EPS",
    }
    bs_map2 = {
        "annualTotalAssets": "総資産",
        "annualStockholdersEquity": "自己資本",
        "annualTotalDebt": "有利子負債",
    }
    inc_a = annual_table(fund, income_map2)
    bs_a = annual_table(fund, bs_map2)
    metrics, scores, overall = build_analysis(inc_a, bs_a, dividends, price, pe, pb)

    st.markdown("#### 総合評価")
    st.metric("長期保有適性スコア", f"{overall} / 100")
    st.progress(overall / 100)

    overall_label = score_label(overall)
    if overall >= 80:
        overall_text = "収益性・成長性・財務・割安性などを総合すると、長期保有候補として非常に高い評価です。"
    elif overall >= 65:
        overall_text = "総合的に良好で、長期保有候補として検討しやすい水準です。"
    elif overall >= 50:
        overall_text = "総合的には標準水準です。強みと弱みを個別に確認して判断する必要があります。"
    elif overall >= 35:
        overall_text = "注意が必要な水準です。成長性など一部に強みがあっても、収益性・財務・割安性などに弱点があります。"
    else:
        overall_text = "弱い評価です。長期保有を検討する場合は、業績改善や財務改善の根拠を一次資料で確認する必要があります。"

    st.markdown(f"### 総合スコア：{overall}点 / 100点")
    with st.expander("スコアの考え方・計算ルール"):
        st.markdown("""
        **総合スコアは、5つの評価項目を同じ重みで平均した参考値です。**

        - 収益性：ROE、ROA、営業利益率、純利益率など
        - 成長性：売上高、営業利益、EPSの成長率など
        - 財務安全性：自己資本比率、有利子負債など
        - 株主還元：配当利回り、配当性向など
        - 割安性：PER、PBRなど

        各指標をあらかじめ設定した閾値に当てはめて点数化しています。
        企業の将来価値や株価上昇を保証・予測するものではありません。
        業種によって適切な指標水準が異なるため、同業他社比較と一次情報の確認を併用してください。
        """)

    st.markdown(f"**総合評価：{overall_label}**")
    st.write(overall_text)
    st.caption("目安：80点以上=非常に良い / 65〜79点=良い / 50〜64点=標準 / 35〜49点=注意 / 34点以下=弱い")

    score_df = pd.DataFrame({
        "項目": list(scores.keys()),
        "スコア": list(scores.values()),
        "判定": [score_label(v) for v in scores.values()],
    })
    st.dataframe(score_df, hide_index=True, width="stretch")

    st.markdown("#### スコアの見方")
    reason_rows = []
    for item, sc in scores.items():
        if sc >= 80:
            reason = "非常に強い"
        elif sc >= 65:
            reason = "強い"
        elif sc >= 50:
            reason = "標準"
        elif sc >= 35:
            reason = "注意"
        else:
            reason = "弱い"

        if item == "収益性":
            detail = f"ROE {metrics['ROE']*100:.1f}% / ROA {metrics['ROA']*100:.1f}%" if metrics["ROE"] is not None and metrics["ROA"] is not None else "ROE・ROA・営業利益率を評価"
        elif item == "成長性":
            detail = f"売上 {metrics['売上成長率']*100:.1f}% / EPS {metrics['EPS成長率']*100:.1f}%" if metrics["売上成長率"] is not None and metrics["EPS成長率"] is not None else "売上・営業利益・EPSの成長率を評価"
        elif item == "財務安全性":
            detail = f"自己資本比率 {metrics['自己資本比率']*100:.1f}%" if metrics["自己資本比率"] is not None else "自己資本比率・負債水準を評価"
        elif item == "株主還元":
            detail = f"配当利回り {metrics['配当利回り']*100:.1f}%" if metrics["配当利回り"] is not None else "配当利回り・配当性向を評価"
        else:
            detail = f"PER {pe:.1f}倍 / PBR {pb:.2f}倍" if pe is not None and pb is not None else "PER・PBRを評価"

        reason_rows.append({"項目": item, "評価": reason, "根拠": detail})

    st.dataframe(pd.DataFrame(reason_rows), hide_index=True, width="stretch")

    st.markdown("#### 主要分析指標")
    metric_rows = [
        ("ROE", metrics["ROE"], "pct"),
        ("ROA", metrics["ROA"], "pct"),
        ("営業利益率", metrics["営業利益率"], "pct"),
        ("純利益率", metrics["純利益率"], "pct"),
        ("売上成長率", metrics["売上成長率"], "pct"),
        ("営業利益成長率", metrics["営業利益成長率"], "pct"),
        ("EPS成長率", metrics["EPS成長率"], "pct"),
        ("自己資本比率", metrics["自己資本比率"], "pct"),
        ("負債自己資本倍率", metrics["負債自己資本倍率"], "ratio"),
        ("配当性向", metrics["配当性向"], "pct"),
    ]
    out = []
    for label, val, kind in metric_rows:
        if val is None:
            disp = "取得不可"
        elif kind == "pct":
            disp = f"{val*100:,.2f}%"
        else:
            disp = f"{val:,.2f}倍"
        out.append({"指標": label, "値": disp})
    st.dataframe(pd.DataFrame(out), hide_index=True, width="stretch")

    st.markdown("#### 自動コメント")
    comments = []
    if scores["収益性"] >= 65:
        comments.append("収益性は比較的良好です。")
    elif scores["収益性"] < 50:
        comments.append("収益性は弱めで、利益率やROEの改善確認が必要です。")

    if scores["成長性"] >= 65:
        comments.append("直近の売上・利益・EPS成長は比較的良好です。")
    elif scores["成長性"] < 50:
        comments.append("直近の成長率は弱めです。減収・減益が一時的か確認が必要です。")

    if scores["財務安全性"] >= 65:
        comments.append("財務安全性は比較的高い水準です。")
    elif scores["財務安全性"] < 50:
        comments.append("財務面は注意が必要です。自己資本比率や有利子負債を確認してください。")

    if scores["割安性"] >= 65:
        comments.append("PER・PBRだけで見ると割高感は比較的小さい水準です。")
    elif scores["割安性"] < 50:
        comments.append("PER・PBRだけで見ると割高感があります。成長期待が織り込まれている可能性があります。")

    for c in comments:
        st.write("・" + c)

    st.warning(
        "このスコアは機械的な参考評価です。業種によって適正なPER・PBR・自己資本比率は大きく異なります。"
        "特に銀行・保険・証券など金融株は、一般企業向けの財務安全性スコアをそのまま使わないでください。"
    )


# 比較
with tabs[6]:
    st.markdown("#### 同業他社比較")
    st.caption("現在の銘柄と、比較したい銘柄を最大3社まで並べて確認できます。")

    default_code = code.replace(".T", "")
    peer_text = st.text_input(
        "比較銘柄コード（カンマ区切り）",
        value="",
        placeholder="例：8267,3382,9983",
        key=f"peer_input_{default_code}",
    )

    if st.button("比較する", width="stretch", key=f"peer_btn_{default_code}"):
        peer_codes = [default_code]
        peer_codes += [
            x.strip() for x in peer_text.replace("、", ",").split(",")
            if x.strip()
        ]

        # 重複除去・最大4社
        unique = []
        for c in peer_codes:
            if c not in unique:
                unique.append(c)
        unique = unique[:4]

        with st.spinner("比較データを取得しています…"):
            rows = [load_peer_snapshot(c) for c in unique]

        st.session_state[f"peer_rows_{default_code}"] = rows

    state_key = f"peer_rows_{default_code}"
    if state_key in st.session_state:
        rows = st.session_state[state_key]

        st.dataframe(
            format_peer_df(rows),
            hide_index=True,
            width="stretch"
        )

        # 総合スコア比較
        score_df = pd.DataFrame({
            "銘柄": [f"{r['コード']} {r['銘柄名']}" for r in rows],
            "総合スコア": [r["総合スコア"] for r in rows],
        })

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=score_df["銘柄"],
            y=score_df["総合スコア"],
            name="総合スコア",
            hovertemplate="%{x}<br>総合スコア: %{y}点<extra></extra>",
        ))
        fig.update_layout(
            title="総合スコア比較",
            yaxis=dict(title="点", range=[0, 100]),
            xaxis_title="",
            height=380,
            margin=dict(l=10, r=10, t=50, b=10),
            showlegend=False,
        )
        st.plotly_chart(fig, width="stretch")

        # 5項目スコア比較
        score_rows = []
        for r in rows:
            score_rows.append({
                "銘柄": f"{r['コード']} {r['銘柄名']}",
                "収益性": r["収益性"],
                "成長性": r["成長性"],
                "財務安全性": r["財務安全性"],
                "株主還元": r["株主還元"],
                "割安性": r["割安性"],
            })
        st.markdown("##### 評価項目別")
        st.dataframe(pd.DataFrame(score_rows), hide_index=True, width="stretch")

        # 自動順位コメント
        ranked = sorted(rows, key=lambda r: r["総合スコア"], reverse=True)
        if ranked:
            top = ranked[0]
            st.success(
                f"この比較では、総合スコア1位は "
                f"{top['コード']} {top['銘柄名']}（{top['総合スコア']}点）です。"
            )

        st.caption(
            "比較スコアは同じ機械的ルールで算出しています。"
            "業種が違う銘柄同士ではPER・PBRや財務構造の単純比較に注意してください。"
        )


# EDINET
with tabs[7]:
    st.markdown("#### 公式開示インテリジェンス")
    st.caption("EDINETの公式提出書類を、確認上の重要度と内容まで確認します。")

    key = get_edinet_key()
    if not key:
        st.error("Streamlit SecretsにEDINET_API_KEYが見つかりません。")
    else:
        edinet_state_key = f"edinet_{code4}"

        if st.button("直近45日の公式開示を取得", width="stretch"):
            with st.spinner("EDINETを確認しています…"):
                filings, ed_err = latest_edinet_filings(code4, key, days=45)

            if filings:
                for f in filings:
                    f["重要度"] = disclosure_priority(f.get("書類名"))
                    f["カテゴリ"] = disclosure_category(f.get("書類名"))
                    f["分析上の意味"] = disclosure_meaning(f.get("書類名"))

                rank = {"高": 0, "中": 1, "低": 2}
                filings = sorted(
                    filings,
                    key=lambda x: (rank.get(x.get("重要度"), 9), str(x.get("提出日時", ""))),
                )

            st.session_state[edinet_state_key] = {
                "filings": filings,
                "error": ed_err
            }

        if edinet_state_key in st.session_state:
            saved = st.session_state[edinet_state_key]
            filings = saved.get("filings", [])
            ed_err = saved.get("error")

            if ed_err:
                st.error(ed_err)
            elif filings:
                st.success("金融庁EDINET APIから公式提出書類を取得しました。")

                # 一覧は分析向けの列だけ表示
                view = pd.DataFrame([
                    {
                        "重要度": f.get("重要度", disclosure_priority(f.get("書類名"))),
                        "提出日時": f.get("提出日時", ""),
                        "カテゴリ": f.get("カテゴリ", disclosure_category(f.get("書類名"))),
                        "書類名": f.get("書類名", ""),
                        "分析上の意味": f.get("分析上の意味", disclosure_meaning(f.get("書類名"))),
                    }
                    for f in filings
                ])
                st.dataframe(view, hide_index=True, width="stretch")

                st.markdown("##### 書類の中身を分析")
                options = {
                    f'{f.get("提出日時","")}｜{f.get("書類名","")}': f
                    for f in filings
                }
                selected_label = st.selectbox(
                    "分析する書類",
                    list(options.keys()),
                    key=f"edinet_select_{code4}",
                )
                selected = options[selected_label]

                if st.button("この書類を分析", width="stretch"):
                    with st.spinner("EDINETから本文を取得して分析しています…"):
                        text, text_err = fetch_edinet_document_text(
                            selected.get("書類ID"), key
                        )

                    analysis_key = f"edinet_analysis_{selected.get('書類ID')}"
                    if text_err:
                        st.session_state[analysis_key] = {"error": text_err}
                    else:
                        result = analyze_edinet_text(selected.get("書類名"), text)
                        st.session_state[analysis_key] = {
                            "error": None,
                            "result": result
                        }

                analysis_key = f"edinet_analysis_{selected.get('書類ID')}"
                if analysis_key in st.session_state:
                    a = st.session_state[analysis_key]
                    if a.get("error"):
                        st.error(a["error"])
                    else:
                        result = a["result"]

                        c1, c2, c3 = st.columns(3)
                        c1.metric("重要度", result["重要度"])
                        c2.metric("カテゴリ", result["カテゴリ"])
                        c3.metric("株価影響", result["株価影響"])

                        st.markdown("###### 分析上の要点")
                        for label, keyword, snippet in result["要点"]:
                            st.markdown(f"**{label}**")
                            st.write(snippet)

                        if result["ポジティブ語"]:
                            st.success(
                                "ポジティブ要素候補：" +
                                "、".join(result["ポジティブ語"])
                            )
                        if result["ネガティブ語"]:
                            st.warning(
                                "ネガティブ要素候補：" +
                                "、".join(result["ネガティブ語"])
                            )

                        st.caption(
                            "この判定はEDINET本文中の重要語と書類種別を使った自動整理です。"
                            "最終判断では原文・会社IRも確認してください。"
                        )
            else:
                st.info("直近45日では該当するEDINET提出書類が見つかりませんでした。")

st.divider()
st.caption(f"取得日時: {now_jst} / 株価基準日: {last_market_date}")

# 利用・法務
with tabs[8]:
    st.markdown("#### ご利用にあたって")
    st.info(
        "この画面は販売準備用のひな形です。販売開始前に、販売者情報・価格・返品/解約条件・"
        "問い合わせ先などを実際の内容に合わせて確定してください。"
    )

    legal_section = st.radio(
        "表示する項目",
        ["免責事項", "利用規約", "プライバシーポリシー", "特定商取引法に基づく表記"],
        horizontal=False,
        key="legal_section",
    )

    if legal_section == "免責事項":
        st.markdown("""
### 免責事項

本サービスは、公開情報・財務情報等を整理、可視化し、一定の計算ルールに基づく参考指標を提供する情報提供サービスです。

本サービスに表示されるスコア、評価、ランキング、グラフ、コメントその他の情報は、特定の金融商品の取得、売却、保有その他の取引を推奨するものではありません。

利用者は、本サービス上の情報のみを根拠として取引を行うのではなく、企業の公式開示、金融商品取引業者が提供する情報その他必要な情報を自ら確認し、自身の判断と責任で意思決定を行うものとします。

本サービスでは情報の正確性、完全性、最新性の確保に努めますが、これらを保証するものではありません。データ取得元の障害、仕様変更、通信障害、計算処理上の不具合等により、表示の遅延、欠損または誤りが生じる場合があります。

本サービスの利用または利用不能により利用者に生じた損害について、法令上免責が認められない場合を除き、運営者は責任を負わないものとします。
        """)

    elif legal_section == "利用規約":
        st.markdown("""
### 利用規約

**第1条（適用）**  
本規約は、本サービスの利用に関する利用者と運営者との間の条件を定めるものです。

**第2条（サービス内容）**  
本サービスは、株式等に関する公開情報を整理・可視化し、あらかじめ設定された計算ルールによる参考指標を表示するサービスです。

**第3条（禁止事項）**  
利用者は、法令または公序良俗に反する行為、本サービスの運営を妨害する行為、不正アクセス、システムへの過度な負荷、データや画面の不正な複製・再配布、第三者の権利を侵害する行為をしてはなりません。

**第4条（知的財産権）**  
本サービス独自のプログラム、画面構成、分析ロジック、文章その他のコンテンツに関する権利は、運営者または正当な権利者に帰属します。外部データについては各権利者の条件が適用されます。

**第5条（サービスの変更・停止）**  
運営者は、保守、障害、外部サービスの停止、仕様変更その他必要がある場合、本サービスの全部または一部を変更または停止することがあります。

**第6条（免責）**  
本サービスは情報提供を目的とし、金融商品の売買その他の取引を推奨するものではありません。表示情報の正確性、完全性、最新性を保証するものではありません。

**第7条（規約の変更）**  
運営者は、必要に応じて本規約を変更することがあります。重要な変更がある場合は、本サービス上で分かりやすく告知します。

**第8条（準拠法・管轄）**  
本規約は日本法を準拠法とします。紛争が生じた場合の合意管轄は、販売開始前に運営者の所在地等を踏まえて確定してください。
        """)

    elif legal_section == "プライバシーポリシー":
        st.markdown("""
### プライバシーポリシー

運営者は、本サービスにおける利用者情報を適切に取り扱います。

**1. 取得する情報**  
現時点の販売準備版では、銘柄コード等の利用者が入力した情報、アクセス時に技術的に生成されるログ・端末情報等が取得される可能性があります。将来、会員登録、問い合わせ、決済等を導入する場合は、取得項目を追加して明示します。

**2. 利用目的**  
取得した情報は、本サービスの提供、障害対応、品質改善、不正利用防止、問い合わせ対応、法令上必要な対応のために利用します。

**3. 第三者提供**  
法令に基づく場合を除き、本人の同意なく個人情報を第三者に提供しません。ただし、サービス提供に必要な範囲で外部事業者に処理を委託する場合があります。

**4. 外部サービス**  
ホスティング、アクセス解析、問い合わせ、決済等の外部サービスを利用する場合、各サービス提供者に情報が送信されることがあります。実際に導入したサービス名・送信情報・利用目的を販売開始前に追記してください。

**5. 安全管理**  
取得情報への不正アクセス、漏えい、滅失または毀損の防止に必要な安全管理措置を講じます。

**6. 問い合わせ窓口**  
販売開始前に、問い合わせ用メールアドレス等を記載してください。

**7. 改定**  
本ポリシーを変更した場合は、本サービス上で告知します。
        """)

    else:
        st.markdown("""
### 特定商取引法に基づく表記

以下は**入力用テンプレート**です。実際に有料販売を開始する前に、確定した情報へ置き換えてください。

| 項目 | 表示内容 |
|---|---|
| 販売事業者 | 【販売事業者名を入力】 |
| 運営責任者 | 【氏名を入力】 |
| 所在地 | 【法令上必要となる表示方法を確認して入力】 |
| 電話番号 | 【法令上必要となる表示方法を確認して入力】 |
| メールアドレス | 【問い合わせ先を入力】 |
| 販売価格 | 【税込価格を入力】 |
| 販売価格以外に必要な費用 | インターネット接続料金、通信料金等は利用者負担 |
| 支払方法 | 【販売開始時に確定】 |
| 支払時期 | 【販売開始時に確定】 |
| サービス提供時期 | 【購入後直ちに利用可能、など実態に合わせて入力】 |
| 返品・キャンセル | デジタルサービスの性質に合わせ、法令を確認のうえ条件を入力 |
| 解約条件 | 【継続課金を採用する場合のみ、手続・期限等を明記】 |
| 動作環境 | 最新版の主要Webブラウザを推奨。詳細は販売開始前に確定 |
        """)

        st.warning(
            "このテンプレートを未入力のまま有料販売しないでください。"
            "また、実際の販売方法に応じて必要表示が変わるため、販売開始前に専門家または所管窓口への確認を推奨します。"
        )

