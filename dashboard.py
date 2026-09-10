"""
金融数据可视化面板
运行: streamlit run dashboard.py
依赖: pip install streamlit plotly pandas requests
"""

import streamlit as st
import pandas as pd
import json
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import re
import akshare as ak
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
import quant_data
from urllib3.util.retry import Retry

st.set_page_config(page_title="金融数据可视化面板", layout="wide")

session = requests.Session()
_retries = Retry(total=5, backoff_factor=1, connect=5, read=5,
                 status_forcelist=[502, 503, 504], allowed_methods=["GET"])
session.mount('https://', HTTPAdapter(max_retries=_retries))
session.mount('http://', HTTPAdapter(max_retries=_retries))
session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})


# ============================================================
# 美股数据函数
# ============================================================

def fetch_us_realtime(symbol: str) -> dict:
    """通过新浪财经获取美股实时报价"""
    url = f"http://hq.sinajs.cn/list=gb_{symbol.lower()}"
    headers = {"Referer": "https://finance.sina.com.cn"}
    try:
        resp = session.get(url, headers=headers, timeout=8)
        match = re.search(r'"(.+?)"', resp.text)
        if not match:
            return {}
        f = match.group(1).split(',')
        if len(f) < 11 or not f[1]:
            return {}
        price = float(f[1])
        prev_close = float(f[5]) if f[5] else 0
        # 盘前/盘后: 当前价为0时用昨收价显示
        if price == 0 and prev_close > 0:
            price = prev_close
            change = 0.0
            change_pct = 0.0
            market_status = "未开盘"
        else:
            change = float(f[4]) if f[4] else 0
            change_pct = float(f[2]) if f[2] else 0
            market_status = "交易中"
        return {
            'name': f[0],
            'price': price,
            'change_pct': change_pct,
            'trade_time': f[3],
            'change': change,
            'prev_close': prev_close,
            'high': float(f[6]) if f[6] else 0,
            'low': float(f[7]) if f[7] else 0,
            'week_high_52': float(f[8]) if f[8] else 0,
            'week_low_52': float(f[9]) if f[9] else 0,
            'volume': int(float(f[10])) if f[10] else 0,
            'market_status': market_status,
        }
    except Exception:
        return {}


@st.cache_data(ttl=300)
def fetch_us_history(symbol: str, trading_days: int) -> pd.DataFrame:
    """获取美股历史日K线,使用 AkShare stock_us_daily"""
    try:
        df = ak.stock_us_daily(symbol=symbol.upper(), adjust="qfq")
        if df is not None and len(df) > 0:
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').tail(trading_days).reset_index(drop=True)
            return df
    except Exception:
        pass

    # 备选: 腾讯财经 API
    try:
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        params = {"param": f"us{symbol.upper()},day,,,{trading_days + 50},qfq"}
        resp = session.get(url, params=params, timeout=10)
        data = resp.json()
        stock_key = f"us{symbol.upper()}"
        klines = (data.get("data", {}).get(stock_key, {}) or {}).get("qfqday", [])
        if not klines:
            klines = (data.get("data", {}).get(stock_key, {}) or {}).get("day", [])
        if klines and len(klines) > 0:
            rows = []
            for line in klines:
                if isinstance(line, list) and len(line) >= 6:
                    rows.append({
                        'date': line[0], 'open': float(line[1]),
                        'close': float(line[2]), 'high': float(line[3]),
                        'low': float(line[4]), 'volume': int(float(line[5])),
                    })
            if rows:
                df = pd.DataFrame(rows).tail(trading_days).reset_index(drop=True)
                df['date'] = pd.to_datetime(df['date'])
                return df
    except Exception:
        pass

    return pd.DataFrame()


# ============================================================
# A股数据函数
# ============================================================

def get_a_share_prefix(code: str) -> str:
    """根据股票代码判断交易所前缀: sh=上海, sz=深圳, bj=北京"""
    code = code.strip()
    if code.startswith('6'):
        return 'sh'
    elif code.startswith(('0', '3')):
        return 'sz'
    elif code.startswith(('8', '4')):
        return 'bj'
    return 'sh'


def fetch_a_share_realtime(code: str) -> dict:
    """通过新浪财经获取A股实时报价"""
    prefix = get_a_share_prefix(code)
    url = f"http://hq.sinajs.cn/list={prefix}{code}"
    headers = {"Referer": "https://finance.sina.com.cn"}
    try:
        resp = session.get(url, headers=headers, timeout=8)
        match = re.search(r'"(.+?)"', resp.text)
        if not match:
            return {}
        f = match.group(1).split(',')
        if len(f) < 10 or not f[3]:
            return {}
        price = float(f[3])
        prev_close = float(f[2])
        # 盘前/盘后: 当前价为0时用昨收价显示
        market_closed = (price == 0)
        if market_closed:
            price = prev_close
            change = 0.0
            change_pct = 0.0
            market_status = "未开盘"
        else:
            change = round(price - prev_close, 3)
            change_pct = round((change / prev_close * 100), 2) if prev_close else 0
            market_status = "交易中"
        return {
            'name': f[0],
            'open': float(f[1]),
            'prev_close': prev_close,
            'price': price,
            'high': float(f[4]),
            'low': float(f[5]),
            'volume': int(float(f[8])),
            'amount': float(f[9]),
            'change': change,
            'change_pct': change_pct,
            'date': f[30] if len(f) > 30 else '',
            'time': f[31] if len(f) > 31 else '',
            'market_status': market_status,
        }
    except Exception:
        return {}


@st.cache_data(ttl=300)
def fetch_a_share_history(code: str, trading_days: int) -> pd.DataFrame:
    """通过新浪财经获取A股历史日K线"""
    prefix = get_a_share_prefix(code)
    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    params = {
        "symbol": f"{prefix}{code}",
        "scale": "240",
        "ma": "no",
        "datalen": str(trading_days + 20),
    }
    try:
        resp = session.get(url, params=params, timeout=10,
                           headers={"Referer": "https://finance.sina.com.cn"})
        import json
        data = json.loads(resp.text)
        if data and len(data) > 0:
            df = pd.DataFrame(data)
            df['day'] = pd.to_datetime(df['day'])
            for col in ['open', 'high', 'low', 'close']:
                df[col] = pd.to_numeric(df[col])
            df['volume'] = pd.to_numeric(df['volume'])
            df = df.rename(columns={'day': 'date'})
            df = df.sort_values('date').tail(trading_days).reset_index(drop=True)
            return df[['date', 'open', 'high', 'low', 'close', 'volume']]
    except Exception:
        pass

    return pd.DataFrame()


# ============================================================
# 港股数据函数
# ============================================================

def fetch_hk_realtime(code: str) -> dict:
    """通过新浪财经获取港股实时报价"""
    code = code.strip().zfill(5)
    url = f"http://hq.sinajs.cn/list=rt_hk{code}"
    headers = {"Referer": "https://finance.sina.com.cn"}
    try:
        resp = session.get(url, headers=headers, timeout=8)
        match = re.search(r'"(.+?)"', resp.text)
        if not match:
            return {}
        f = match.group(1).split(',')
        if len(f) < 19 or not f[6]:
            return {}
        price = float(f[6])
        prev_close = float(f[3])

        # 港股市场状态: 周一至周五 09:30-12:00, 13:00-16:00 (北京时间=港股时间)
        now = datetime.now()
        is_weekday = now.weekday() < 5
        t = now.hour * 100 + now.minute
        hk_open = is_weekday and ((930 <= t < 1200) or (1300 <= t < 1600))

        if price == 0 and prev_close > 0:
            price = prev_close
            change = 0.0
            change_pct = 0.0
            market_status = "未开盘"
        else:
            change = float(f[7]) if f[7] else 0
            change_pct = float(f[8]) if f[8] else 0
            market_status = "交易中" if hk_open else "未开盘"

        open_price = float(f[4]) if f[4] else price
        return {
            'name': f[1],
            'price': price,
            'change_pct': change_pct,
            'change': change,
            'prev_close': prev_close,
            'high': max(open_price, price) if open_price > 0 else price,
            'low': float(f[5]) if f[5] and float(f[5]) > 0 else price,
            'volume': int(float(f[12])) if f[12] else 0,
            'amount': float(f[11]) if f[11] else 0,
            'week_high_52': float(f[15]) if f[15] else 0,
            'week_low_52': float(f[16]) if f[16] else 0,
            'trade_time': f"{f[17]} {f[18]}" if len(f) > 18 else '',
            'market_status': market_status,
        }
    except Exception:
        return {}


@st.cache_data(ttl=300)
def fetch_hk_history(code: str, trading_days: int) -> pd.DataFrame:
    """获取港股历史日K线,使用 AkShare stock_hk_daily"""
    code = code.strip().zfill(5)
    try:
        df = ak.stock_hk_daily(symbol=code, adjust="qfq")
        if df is not None and len(df) > 0:
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').tail(trading_days).reset_index(drop=True)
            return df
    except Exception:
        pass

    # 备选: 腾讯财经 API
    try:
        url = "https://web.ifzq.gtimg.cn/appstock/app/kline/get"
        params = {"param": f"hk{code},day,,{trading_days + 50},qfq"}
        resp = session.get(url, params=params, timeout=10)
        data = resp.json()
        stock_key = f"hk{code}"
        klines = (data.get("data", {}).get(stock_key, {}) or {}).get("qfqday", [])
        if not klines:
            klines = (data.get("data", {}).get(stock_key, {}) or {}).get("day", [])
        if klines and len(klines) > 0:
            rows = []
            for line in klines:
                if isinstance(line, list) and len(line) >= 6:
                    rows.append({
                        'date': line[0], 'open': float(line[1]),
                        'close': float(line[2]), 'high': float(line[3]),
                        'low': float(line[4]), 'volume': int(float(line[5])),
                    })
            if rows:
                df = pd.DataFrame(rows).tail(trading_days).reset_index(drop=True)
                df['date'] = pd.to_datetime(df['date'])
                return df
    except Exception:
        pass

    return pd.DataFrame()

_INDEX_LIST = [
    ('上证指数', 's_sh000001', 'a'),
    ('深证成指', 's_sz399001', 'a'),
    ('创业板指', 's_sz399006', 'a'),
    ('沪深300', 's_sh000300', 'a'),
    ('科创50', 's_sh000688', 'a'),
    ('纳斯达克', 'gb_ixic', 'us'),
    ('道琼斯', 'gb_dji', 'us'),
]


def fetch_indices() -> list:
    """批量获取大盘指数实时行情"""
    codes = ','.join([code for _, code, _ in _INDEX_LIST])
    url = f"http://hq.sinajs.cn/list={codes}"
    headers = {"Referer": "https://finance.sina.com.cn"}
    try:
        resp = session.get(url, headers=headers, timeout=8)
        lines = resp.text.strip().split('\n')
        results = []
        for i, line in enumerate(lines):
            match = re.search(r'"(.+?)"', line)
            if not match or not match.group(1):
                results.append({'name': _INDEX_LIST[i][0], 'price': 0,
                                'change': 0, 'change_pct': 0, 'time': ''})
                continue
            f = match.group(1).split(',')
            name, _, mtype = _INDEX_LIST[i]
            if mtype == 'a':
                results.append({
                    'name': f[0] if f[0] else name,
                    'price': float(f[1]) if f[1] else 0,
                    'change': float(f[2]) if f[2] else 0,
                    'change_pct': float(f[3]) if f[3] else 0,
                    'time': '',
                })
            else:
                results.append({
                    'name': f[0] if f[0] else name,
                    'price': float(f[1]) if f[1] else 0,
                    'change': float(f[4]) if len(f) > 4 and f[4] else 0,
                    'change_pct': float(f[2]) if len(f) > 2 and f[2] else 0,
                    'time': f[3] if len(f) > 3 else '',
                })
        return results
    except Exception:
        return []


# ============================================================
# UI
# ============================================================

page = st.sidebar.radio("导航", ["实时查询", "长期追踪", "量化分析", "数据库浏览"])

if page == "实时查询":
    st.title("金融数据可视化面板")
elif page == "长期追踪":
    st.title("股票长期追踪")
elif page == "量化分析":
    st.title("📊 量化分析")
elif page == "数据库浏览":
    st.title("数据库浏览")

# --- 大盘指数 ---
index_placeholder = st.empty()

@st.fragment(run_every=timedelta(seconds=10))
def _refresh_indices():
    indices = fetch_indices()
    if not indices:
        index_placeholder.warning("获取大盘指数失败,等待重试...")
        return
    with index_placeholder.container():
        cols = st.columns(min(len(indices), 7))
        for i, idx in enumerate(indices):
            with cols[i]:
                delta_str = f"{idx['change']:+.2f} ({idx['change_pct']:+.2f}%)"
                st.metric(idx['name'], f"{idx['price']:,.2f}", delta_str)
        st.caption(f"数据来源: 新浪财经 | 更新时间: {datetime.now().strftime('%H:%M:%S')}")

_refresh_indices()
st.divider()


def render_realtime_section(market: str, input_key: str, default_symbol: str,
                            placeholder_text: str, fetch_fn, rt_prefix: str):
    """渲染实时报价+K线区域的通用函数"""
    st.header(f"{market}实时查询")

    col_input, col_refresh = st.columns([5, 2])
    with col_input:
        stock_symbol = st.text_input(
            "股票代码", value=default_symbol,
            placeholder=placeholder_text,
            key=input_key,
        )
    with col_refresh:
        st.write("")
        st.write("")
        auto_refresh = st.toggle("自动刷新", value=True, help="开启后每5秒自动刷新报价",
                                key=f"auto_refresh_{input_key}")

    symbol = stock_symbol.strip().upper()

    # --- 实时报价 ---
    if auto_refresh:
        refresh_placeholder = st.empty()
        chart_placeholder = st.empty()

        @st.fragment(run_every=timedelta(seconds=5))
        def _refresh_realtime():
            sym = st.session_state.get(input_key, default_symbol).strip().upper()
            quote = fetch_fn(sym)
            if not quote:
                refresh_placeholder.warning(f"无法获取 {sym} 的实时报价,等待重试...")
                chart_placeholder.info("等待数据...")
                return

            delta = quote.get('change', 0)
            delta_pct = quote.get('change_pct', 0)
            currency = "$" if market == "美股" else ("HK$" if market == "港股" else "")
            unit = "股" if market in ("A股", "港股") else ""

            with refresh_placeholder.container():
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("最新价", f"{currency}{quote['price']:.2f}", f"{delta:+.2f}")
                c2.metric("涨跌幅", f"{delta_pct:+.2f}%")
                c3.metric("成交量", f"{quote['volume']:,}{unit}")
                market_closed = quote.get('market_status') == "未开盘"
                if market_closed:
                    c4.metric("昨收价", f"{currency}{quote['prev_close']:.2f}")
                else:
                    c4.metric("今日区间",
                             f"{currency}{quote['low']:.2f} - {currency}{quote['high']:.2f}")
                time_str = quote.get('trade_time', '') or f"{quote.get('date', '')} {quote.get('time', '')}"
                status = quote.get('market_status', '')
                if market in ("美股", "港股"):
                    st.caption(f"{quote['name']} ({sym}) | [{status}] | 52周: {currency}{quote['week_low_52']:.2f} - {currency}{quote['week_high_52']:.2f} | 最后成交: {time_str or 'N/A'}")
                else:
                    st.caption(f"{quote['name']} ({sym}) | [{status}] | 最后成交: {time_str or 'N/A'}")

            rt_key = f'{rt_prefix}_{sym}'
            st.session_state.setdefault(rt_key, [])
            st.session_state[rt_key].append({
                'time': datetime.now().strftime('%H:%M:%S'),
                'price': quote['price'],
            })
            if len(st.session_state[rt_key]) > 120:
                st.session_state[rt_key] = st.session_state[rt_key][-120:]

            if len(st.session_state[rt_key]) > 1:
                df_rt = pd.DataFrame(st.session_state[rt_key])
                with chart_placeholder:
                    fig = go.Figure(go.Scatter(
                        x=df_rt['time'], y=df_rt['price'],
                        mode='lines+markers', line=dict(color='#4B3FE3', width=2),
                        marker=dict(size=4),
                    ))
                    fig.update_layout(
                        title=f"{sym} 实时价格走势", xaxis_title="时间",
                        yaxis_title=f"价格({currency})",
                        template="plotly_white", height=350,
                    )
                    st.plotly_chart(fig, use_container_width=True)
            else:
                chart_placeholder.info("正在收集数据,至少需要2个数据点...")

        _refresh_realtime()
    else:
        col_q1, col_q2, col_q3, col_q4 = st.columns(4)
        with st.spinner("获取实时报价..."):
            quote = fetch_fn(symbol)
        if quote:
            currency = "$" if market == "美股" else ("HK$" if market == "港股" else "")
            unit = "股" if market in ("A股", "港股") else ""
            col_q1.metric("最新价", f"{currency}{quote['price']:.2f}", f"{quote['change']:+.2f}")
            col_q2.metric("涨跌幅", f"{quote['change_pct']:+.2f}%")
            col_q3.metric("成交量", f"{quote['volume']:,}{unit}")
            market_closed = quote.get('market_status') == "未开盘"
            if market_closed:
                col_q4.metric("昨收价", f"{currency}{quote['prev_close']:.2f}")
            else:
                col_q4.metric("今日区间",
                             f"{currency}{quote['low']:.2f} - {currency}{quote['high']:.2f}")
            time_str = quote.get('trade_time', '') or f"{quote.get('date', '')} {quote.get('time', '')}"
            status = quote.get('market_status', '')
            if market in ("美股", "港股"):
                st.caption(f"{quote['name']} ({symbol}) | [{status}] | 52周: {currency}{quote['week_low_52']:.2f} - {currency}{quote['week_high_52']:.2f} | 最后成交: {time_str or 'N/A'}")
            else:
                st.caption(f"{quote['name']} ({symbol}) | [{status}] | 最后成交: {time_str or 'N/A'}")
        else:
            st.warning(f"无法获取 {symbol} 的实时报价,请检查代码是否正确")

    st.divider()

    # --- K线图 ---
    st.subheader("历史K线")

    col_period, col_ma = st.columns([2, 3])
    with col_period:
        period = st.radio("时间范围", ["1日", "1周", "1月", "1年"], horizontal=True,
                          key=f"period_{input_key}")
    with col_ma:
        ma_select = st.multiselect("均线", ["MA5", "MA10", "MA20", "MA60"],
                                   default=["MA5", "MA20"], key=f"ma_{input_key}")
    col_vol, col_macd, col_kdj, col_boll, _ = st.columns([1, 1, 1, 1, 2])
    with col_vol:
        show_vol = st.checkbox("成交量", value=True, key=f"vol_{input_key}")
    with col_macd:
        show_macd = st.checkbox("MACD", value=True, key=f"macd_{input_key}")
    with col_kdj:
        show_kdj = st.checkbox("KDJ", value=True, key=f"kdj_{input_key}")
    with col_boll:
        show_boll = st.checkbox("BOLL", value=False, key=f"boll_{input_key}")

    if symbol:
        period_map = {"1日": 5, "1周": 5, "1月": 22, "1年": 252}
        trading_days = period_map[period]
        fetch_days = trading_days + 80  # 多取数据用于MA60计算

        with st.spinner(f"获取 {symbol} 历史K线数据..."):
            if market == "美股":
                df_query = fetch_us_history(symbol, fetch_days)
            elif market == "港股":
                df_query = fetch_hk_history(symbol, fetch_days)
            else:
                df_query = fetch_a_share_history(symbol, fetch_days)

        if not df_query.empty:
            # 计算技术指标(在完整数据上计算,避免数据不足)
            for p in [5, 10, 20, 60]:
                df_query[f'MA{p}'] = df_query['close'].rolling(window=p).mean()
            ema12 = df_query['close'].ewm(span=12, adjust=False).mean()
            ema26 = df_query['close'].ewm(span=26, adjust=False).mean()
            df_query['DIF'] = ema12 - ema26
            df_query['DEA'] = df_query['DIF'].ewm(span=9, adjust=False).mean()
            df_query['MACD'] = (df_query['DIF'] - df_query['DEA']) * 2

            # BOLL 布林带
            boll_mid = df_query['close'].rolling(window=20).mean()
            boll_std = df_query['close'].rolling(window=20).std()
            df_query['BOLL_UP'] = boll_mid + 2 * boll_std
            df_query['BOLL_MID'] = boll_mid
            df_query['BOLL_LOW'] = boll_mid - 2 * boll_std

            # KDJ 随机指标
            low_9 = df_query['low'].rolling(window=9).min()
            high_9 = df_query['high'].rolling(window=9).max()
            rsv = (df_query['close'] - low_9) / (high_9 - low_9).replace(0, float('nan')) * 100
            rsv = rsv.fillna(50)
            df_query['K'] = rsv.ewm(com=2, adjust=False).mean()
            df_query['D'] = df_query['K'].ewm(com=2, adjust=False).mean()
            df_query['J'] = 3 * df_query['K'] - 2 * df_query['D']

            # 只显示选中的时间段
            df_disp = df_query.tail(trading_days).reset_index(drop=True)

            title_range = {"1日": "近5个交易日", "1周": "近1周", "1月": "近1个月", "1年": "近1年"}
            currency = "$" if market == "美股" else ("HK$" if market == "港股" else "")

            # 构建子图布局
            n_rows = 1
            row_heights = [0.48]
            if show_vol:
                n_rows += 1
                row_heights.append(0.11)
            if show_macd:
                n_rows += 1
                row_heights.append(0.18)
            if show_kdj:
                n_rows += 1
                row_heights.append(0.23)

            fig = make_subplots(rows=n_rows, cols=1, shared_xaxes=True,
                                vertical_spacing=0.05, row_heights=row_heights)

            # K线
            fig.add_trace(go.Candlestick(
                x=df_disp['date'], open=df_disp['open'], high=df_disp['high'],
                low=df_disp['low'], close=df_disp['close'],
                increasing_line_color='#1DC981', decreasing_line_color='#E8463A',
                name='K线',
            ), row=1, col=1)

            # 均线
            ma_colors = {'MA5': '#FFB400', 'MA10': '#4B3FE3',
                         'MA20': '#E8463A', 'MA60': '#00B5D6'}
            for ma_name in ma_select:
                p = int(ma_name[2:])
                if f'MA{p}' in df_disp.columns:
                    fig.add_trace(go.Scatter(
                        x=df_disp['date'], y=df_disp[f'MA{p}'],
                        mode='lines', name=ma_name,
                        line=dict(color=ma_colors.get(ma_name, '#888'), width=1.2),
                    ), row=1, col=1)

            # BOLL 布林带
            if show_boll:
                fig.add_trace(go.Scatter(
                    x=df_disp['date'], y=df_disp['BOLL_UP'],
                    mode='lines', name='BOLL上轨',
                    line=dict(color='rgba(75,63,227,0.4)', width=1, dash='dash'),
                ), row=1, col=1)
                fig.add_trace(go.Scatter(
                    x=df_disp['date'], y=df_disp['BOLL_LOW'],
                    mode='lines', name='BOLL下轨',
                    line=dict(color='rgba(75,63,227,0.4)', width=1, dash='dash'),
                    fill='tonexty', fillcolor='rgba(75,63,227,0.06)',
                ), row=1, col=1)
                fig.add_trace(go.Scatter(
                    x=df_disp['date'], y=df_disp['BOLL_MID'],
                    mode='lines', name='BOLL中轨',
                    line=dict(color='rgba(75,63,227,0.6)', width=1, dash='dot'),
                ), row=1, col=1)

            current_row = 2

            # 成交量
            if show_vol:
                vol_colors = ['#1DC981' if c >= o else '#E8463A'
                              for c, o in zip(df_disp['close'], df_disp['open'])]
                fig.add_trace(go.Bar(
                    x=df_disp['date'], y=df_disp['volume'], name='成交量',
                    marker_color=vol_colors, showlegend=False,
                ), row=current_row, col=1)
                fig.update_yaxes(title_text="成交量", row=current_row, col=1)
                current_row += 1

            # MACD
            if show_macd:
                macd_colors = ['#1DC981' if v >= 0 else '#E8463A'
                               for v in df_disp['MACD']]
                fig.add_trace(go.Bar(
                    x=df_disp['date'], y=df_disp['MACD'], name='MACD柱',
                    marker_color=macd_colors, showlegend=False,
                ), row=current_row, col=1)
                fig.add_trace(go.Scatter(
                    x=df_disp['date'], y=df_disp['DIF'], name='DIF',
                    line=dict(color='#FFB400', width=1.2), showlegend=False,
                ), row=current_row, col=1)
                fig.add_trace(go.Scatter(
                    x=df_disp['date'], y=df_disp['DEA'], name='DEA',
                    line=dict(color='#4B3FE3', width=1.2), showlegend=False,
                ), row=current_row, col=1)
                fig.update_yaxes(title_text="MACD", row=current_row, col=1)
                current_row += 1

            # KDJ
            if show_kdj:
                fig.add_trace(go.Scatter(
                    x=df_disp['date'], y=df_disp['K'],
                    mode='lines', name='K',
                    line=dict(color='#FFB400', width=1.2), showlegend=False,
                ), row=current_row, col=1)
                fig.add_trace(go.Scatter(
                    x=df_disp['date'], y=df_disp['D'],
                    mode='lines', name='D',
                    line=dict(color='#4B3FE3', width=1.2), showlegend=False,
                ), row=current_row, col=1)
                fig.add_trace(go.Scatter(
                    x=df_disp['date'], y=df_disp['J'],
                    mode='lines', name='J',
                    line=dict(color='#E8463A', width=1.2), showlegend=False,
                ), row=current_row, col=1)
                fig.add_hline(y=80, line_dash="dash",
                              line_color="rgba(29,201,129,0.3)",
                              row=current_row, col=1)
                fig.add_hline(y=20, line_dash="dash",
                              line_color="rgba(232,70,58,0.3)",
                              row=current_row, col=1)
                fig.update_yaxes(title_text="KDJ", row=current_row, col=1)
                current_row += 1

            fig.update_layout(
                title=f"{symbol} K线图 ({title_range[period]})",
                template="plotly_white",
                height=700 + (100 if show_kdj else 0),
            )
            fig.update_yaxes(title_text=f"价格({currency})", row=1, col=1)
            for i in range(1, n_rows + 1):
                fig.update_xaxes(rangeslider_visible=False, row=i, col=1)
            st.plotly_chart(fig, use_container_width=True)

            with st.expander("查看原始数据"):
                st.dataframe(df_disp.tail(20), use_container_width=True)
        else:
            st.error(f"无法获取 {symbol} 的历史数据,请确认股票代码正确")

    st.divider()


# ============================================================
# 页面渲染
# ============================================================
if page == "实时查询":
    tab_us, tab_hk, tab_a = st.tabs(["🇺🇸 美股", "🇭🇰 港股", "🇨🇳 A股"])

    with tab_us:
        render_realtime_section(
            market="美股",
            input_key="us_stock_input",
            default_symbol="AAPL",
            placeholder_text="输入美股代码,如 AAPL, TSLA, GOOGL",
            fetch_fn=fetch_us_realtime,
            rt_prefix="us_rt",
        )

    with tab_hk:
        render_realtime_section(
            market="港股",
            input_key="hk_stock_input",
            default_symbol="00700",
            placeholder_text="输入港股代码,如 00700, 09988, 03690",
            fetch_fn=fetch_hk_realtime,
            rt_prefix="hk_rt",
        )

    with tab_a:
        render_realtime_section(
            market="A股",
            input_key="a_stock_input",
            default_symbol="600000",
            placeholder_text="输入A股代码,如 600000, 000001, 300750",
            fetch_fn=fetch_a_share_realtime,
            rt_prefix="a_rt",
        )

elif page == "长期追踪":
    import tracker
    tracker.render_tracking_page()



# ============================================================
# 量化分析页面
# ============================================================
elif page == "量化分析":
    # --- 数据新鲜度横幅 (模块7: 落后>2交易日红色告警) ---
    try:
        _fr = quant_data.get_data_freshness()
        if _fr['alerts']:
            _lag_txt = ", ".join(
                f"{s['code']}({s['name']}) 落后{s['lag_trading_days']}个交易日(行情至{s['quote_latest']})"
                for s in _fr['alerts'])
            st.error(f"⚠️ 数据新鲜度告警: {_lag_txt} — 请运行「📡 前向跟踪」页的每日管道补采")
    except Exception as e:
        print(f"[freshness_banner] {e}")

    quant_tab1, quant_tab2, quant_tab3, quant_tab4, quant_tab5, quant_tab6, quant_tab7, quant_tab8, quant_tab9 = st.tabs([
        "📈 数据采集", "🔔 信号检测", "📝 事件管理", "📊 数据概览", "📉 技术因子",
        "🎯 信号效果", "🧪 策略回测", "🌍 宏观日历", "📡 前向跟踪"
    ])

    # --- Tab 1: 数据采集 ---
    with quant_tab1:
        st.subheader("股票池管理")

        # 添加股票到池(模块10: 入池即引导 — 行情回填→指标→信号回放→宽表)
        qp1, qp2, qp3, qp4 = st.columns([2, 1, 2, 1])
        with qp1:
            new_code = st.text_input("股票代码", placeholder="如 600000 / AAPL / 00700", key="qp_code")
        with qp2:
            new_market = st.selectbox("市场", ["A股", "美股", "港股"], key="qp_market")
        with qp3:
            new_sector = st.text_input("板块/行业", placeholder="如 银行/科技/新能源", key="qp_sector")
        with qp4:
            st.write("")
            st.write("")
            if st.button("加入并引导", type="primary", key="qp_add"):
                if new_code.strip():
                    added = quant_data.add_to_pool(new_code, new_market, "", new_sector)
                    with st.status(f"{added} 入池引导中...", expanded=True) as ob_status:
                        def _ob_log(msg):
                            st.write(str(msg))
                        rep = quant_data.onboard_pool_stock(added, log=_ob_log)
                        if rep['ok']:
                            st.session_state['pool_added_msg'] = (
                                f"✅ {added} ({new_market}) 入池并完成数据引导 — "
                                "回填的历史信号不追溯计入前向，前向样本自入池日起积累")
                            ob_status.update(label=f"✅ {added} 引导完成", state="complete")
                            st.rerun()
                        else:
                            _errs = [f"{k}: {v['error']}" for k, v in rep['steps'].items()
                                     if isinstance(v, dict) and 'error' in v]
                            ob_status.update(
                                label=(f"⚠️ {added} 引导未完成（{'; '.join(_errs) or rep.get('error', '')}"
                                       "）— 可修正后用下方「引导/补数据」重试"),
                                state="error", expanded=True)

        st.divider()

        # 股票池列表(模块10: 含数据覆盖与前向资格)
        pool = quant_data.get_stock_pool(active_only=False)
        if not pool:
            st.info("股票池为空，请先添加股票")
        else:
            if 'pool_added_msg' in st.session_state:
                st.success(st.session_state.pop('pool_added_msg'))
            st.write(f"**股票池 ({len(pool)} 只)** — 数据覆盖 · 前向资格")
            _ob_map = {s['code']: s for s in quant_data.get_pool_onboard_status()}
            pool_data = []
            for s in pool:
                ob = _ob_map.get(s["code"])
                pool_data.append({
                    "代码": s["code"],
                    "市场": s["market"],
                    "名称": s.get("name", "") or "—",
                    "板块": s.get("sector", "") or "—",
                    "状态": "活跃" if s.get("is_active") else "暂停",
                    "行情最新": (ob or {}).get("quote_latest") or "—",
                    "覆盖(行/指标/信号/宽表)": (f"{ob['quotes']}/{ob['indicators']}/"
                                               f"{ob['signals']}/{ob['wide']}" if ob else "0/0/0/0"),
                    "前向资格日": (ob or {}).get("forward_join", "—"),
                    "数据深度": (ob or {}).get("depth_note") or "—",
                    "数据状态": ("✅ 就绪" if ob and ob["ready"] else "⚠️ 需引导"),
                    "涨幅阈值": f"±{s.get('price_threshold', 5.0)}%",
                    "量能倍数": f"{s.get('volume_ratio', 2.0)}x",
                })
            st.dataframe(pd.DataFrame(pool_data), use_container_width=True, hide_index=True)

            # 模块10: 单股引导 / 池结构变化收尾
            ga1, ga2 = st.columns(2)
            with ga1:
                ob_code = st.selectbox("引导/补数据", [s["code"] for s in pool], key="qp_ob_code")
                if st.button("🚀 引导/补数据（行情→深度校验→指标→信号→宽表→封锁日）",
                             key="qp_ob_btn"):
                    with st.status(f"{ob_code} 引导链运行中...", expanded=True) as ob2:
                        def _ob2_log(msg):
                            st.write(str(msg))
                        rep = quant_data.onboard_pool_stock(ob_code, log=_ob2_log)
                        if rep['ok']:
                            st.session_state['pool_added_msg'] = f"✅ {ob_code} 数据引导完成"
                            ob2.update(label=f"✅ {ob_code} 引导完成", state="complete")
                            st.rerun()
                        else:
                            _errs = [f"{k}: {v['error']}" for k, v in rep['steps'].items()
                                     if isinstance(v, dict) and 'error' in v]
                            ob2.update(
                                label=f"⚠️ {ob_code} 引导未完成（{'; '.join(_errs) or rep.get('error', '')}）",
                                state="error", expanded=True)
            with ga2:
                st.caption("池结构变化（增/删/停用股票）后收尾：模块3 三口径重算 + 回测新批次"
                           "（冻结参数，分钟级）")
                if st.button("📊 池结构变化收尾", key="qp_recalc"):
                    with st.status("池结构变化收尾...", expanded=True) as rc_status:
                        st.write("① 模块3 三口径统计重算...")
                        quant_data.run_module3_analysis()
                        st.write("② 回测新批次（冻结参数全量重跑）...")
                        quant_data.run_d2_backtests()
                        rc_status.update(label="✅ 收尾完成（模块3 重算 + 回测新批次已入库）",
                                         state="complete")

            # 删除/切换活跃
            dc1, dc2 = st.columns([1, 1])
            with dc1:
                if 'pool_removed_msg' in st.session_state:
                    st.success(st.session_state.pop('pool_removed_msg'))
                del_code = st.selectbox("选择移除的股票", [s["code"] for s in pool], key="qp_del")
                st.caption("移除会级联删除该股全部数据；前向窗口数据已快照冻结（已入库净值不失配）；"
                           "若该股有前向在持仓位，建议改用右侧「切换」停用")
                if st.button("移除(含数据)", key="qp_remove"):
                    removed = quant_data.remove_from_pool(del_code)
                    _snap = removed.pop('forward_snapshot', {}) or {}
                    st.session_state['pool_removed_msg'] = (
                        f"已移除 {del_code} 并清理数据: " +
                        ", ".join(f"{t} {n}行" for t, n in removed.items()) +
                        f"；前向快照 px {_snap.get('px_rows', 0)} 行 / 信号 "
                        f"{_snap.get('sig_rows', 0)} 条（已入库前向净值仍可复现）")
                    st.rerun()
            with dc2:
                toggle_code = st.selectbox("切换活跃状态", [s["code"] for s in pool], key="qp_toggle")
                st.caption("停用=前向快照+数据保留（不进分析）；再激活=资格从当天重新起算")
                if st.button("切换", key="qp_toggle_btn"):
                    s = next(x for x in pool if x["code"] == toggle_code)
                    quant_data.set_pool_active(toggle_code, not s.get("is_active"))
                    st.rerun()

        st.divider()

        # --- 数据采集类型总览 ---
        st.subheader("数据采集总览")
        dt_status = quant_data.get_data_type_status()

        dt_cols = st.columns(4)
        dt_items = list(dt_status.items())
        for idx, (key, info) in enumerate(dt_items[:4]):
            with dt_cols[idx]:
                pct = (info['count'] / info['total'] * 100) if info['total'] > 0 else 0
                st.metric(info['label'], f"{info['count']}/{info['total']}", f"{pct:.0f}%")
                st.caption(info['desc'])

        evt_cols = st.columns(3)
        for idx, (key, info) in enumerate(dt_items[4:]):
            with evt_cols[idx]:
                pct = (info['count'] / info['total'] * 100) if info['total'] > 0 else 0
                st.metric(info['label'], f"{info['count']}/{info['total']}", f"{pct:.0f}%")
                st.caption(info['desc'])

        st.divider()

        # --- 行情数据采集 (daily_quotes) ---
        st.subheader("📈 行情数据采集")
        st.caption("目标表: daily_quotes | 字段: OHLCV / 涨跌幅 / 换手率 / PE / PB / 市值 / 主力资金")

        collect_days = st.number_input("采集天数", min_value=30, max_value=1095, value=365, step=30, key="qp_days")

        rq1, rq2, rq3 = st.columns(3)
        with rq1:
            st.write("**1. 日线OHLCV**")
            st.caption("开盘/最高/最低/收盘/成交量/成交额")
            if st.button("采集股票池行情", type="primary", key="qp_collect_ohlc"):
                with st.spinner("正在批量采集日线数据..."):
                    results = quant_data.batch_collect_daily(days=int(collect_days))
                total = sum(r["rows"] for r in results)
                st.success(f"完成! {len(results)} 只股票, {total} 条")
                for r in results:
                    st.write(f"  {r['code']} ({r['market']}): {r['rows']} 条")
            manual_code = st.text_input("单只采集代码", placeholder="如 600000", key="qp_manual_code")
            if st.button("采集单只", key="qp_collect_one"):
                if manual_code.strip():
                    with st.spinner(f"采集 {manual_code} ..."):
                        n = quant_data.collect_a_share_daily(manual_code, int(collect_days))
                    if n > 0:
                        st.success(f"成功: {manual_code} {n} 条")
                    else:
                        st.error(f"失败: {manual_code}")

        with rq2:
            st.write("**2. 估值数据**")
            st.caption("PE / PB / 总市值 / 流通市值 · A股/港股: 百度估值 · 美股: Yahoo Finance(历史值按价格比例估算)")
            if st.button("采集股票池估值", type="primary", key="qp_collect_val"):
                with st.spinner("正在采集估值数据..."):
                    results = quant_data.batch_collect_valuation()
                total = sum(r["rows"] for r in results)
                st.success(f"完成! {len(results)} 只, {total} 条估值更新")
                for r in results:
                    st.write(f"  {r['code']} ({r['market']}): {r['rows']} 条")
            val_code = st.text_input("单只采集代码", placeholder="如 600000 / 00700 / AAPL", key="qp_val_code")
            val_market = st.selectbox("市场", ["A股", "港股", "美股"], key="qp_val_market")
            if st.button("采集单只估值", key="qp_val_one"):
                if val_code.strip():
                    with st.spinner(f"采集 {val_code} 估值..."):
                        n = quant_data.collect_stock_valuation(val_code, val_market)
                    if n > 0:
                        st.success(f"成功: {val_code} {n} 条估值数据")
                    else:
                        st.error(f"失败: {val_code}，请检查代码或网络")

        with rq3:
            st.write("**3. 资金流向 / 换手率**")
            st.caption("A股主力净流入(新浪源) + 港美股换手率回填(市值/股本推算)")
            if st.button("采集股票池资金流", type="primary", key="qp_collect_flow"):
                with st.spinner("正在采集资金流向与换手率..."):
                    results = quant_data.batch_collect_capital_flow()
                total = sum(r["rows"] for r in results)
                backfilled = sum(r.get("turnover_backfilled", 0) for r in results)
                msg = f"完成! {len(results)} 只, {total} 条资金流更新"
                if backfilled:
                    msg += f", {backfilled} 条换手率回填"
                st.success(msg)

        st.divider()

        # --- 基准指数采集 (benchmark_index: 宏观事件研究与策略回测 B2 基准共用) ---
        st.subheader("📊 基准指数采集")
        st.caption("目标表: benchmark_index | 沪深300 / 恒生指数 / 标普500 日线 · "
                   "供宏观事件研究(指数AR)与策略回测(大盘对照B2)使用")

        bench = quant_data.get_benchmark_status()
        bench_df = pd.DataFrame([{
            "市场": m,
            "基准指数": f"{s['name']} ({s['code']})",
            "数据条数": s['rows'],
            "覆盖区间": f"{s['start']} ~ {s['end']}" if s['rows'] else "未采集",
        } for m, s in bench.items()])
        st.dataframe(bench_df, use_container_width=True, hide_index=True)

        if st.button("采集三大指数", type="primary", key="qp_collect_idx"):
            with st.spinner("正在采集沪深300 / 恒生指数 / 标普500 ..."):
                idx_res = quant_data.batch_collect_benchmark()
            msg = " · ".join(f"{r['market']}{r['rows']}条" for r in idx_res)
            if all(r['rows'] > 0 for r in idx_res):
                st.success(f"采集完成: {msg}")
            else:
                st.error(f"部分市场采集失败: {msg}")
            st.rerun()

        st.divider()

        # --- 被动信号采集 (passive_signals) ---
        st.subheader("🔔 被动信号采集")
        st.caption("目标表: passive_signals | 信号类型: MACD金叉/死叉 / RSI超买/超卖 / KDJ金叉/死叉 / BOLL突破/跌破 / 放量突破 / 大涨/大跌")

        sig_status = dt_status.get('signals', {})
        if sig_status.get('by_type'):
            st.write("**已采集信号分布:**")
            sig_type_data = [{"信号类型": s["signal_type"], "数量": s["c"]} for s in sig_status['by_type']]
            st.dataframe(pd.DataFrame(sig_type_data), use_container_width=True, hide_index=True)

        ps1, ps2 = st.columns(2)
        with ps1:
            if st.button("运行信号检测", type="primary", key="qp_detect_signals"):
                with st.spinner("正在检测技术指标信号..."):
                    results = quant_data.run_signal_detection()
                total_sigs = sum(r["signals"] for r in results)
                st.success(f"完成! 共 {total_sigs} 个信号")
                for r in results:
                    if r["signals"] > 0:
                        st.write(f"  {r['code']} ({r['name']}): {r['signals']} 个")
        with ps2:
            st.info("信号检测基于已采集的日线数据自动计算技术指标(MACD/RSI/KDJ/BOLL)，无需额外数据源。请先完成行情数据采集。")

        st.divider()

        # --- 主动事件采集 (active_events) ---
        st.subheader("📝 主动事件采集")
        st.caption("目标表: active_events | 类型: 分红 / 政策 / 公告 / 行业 / 宏观 "
                   "(财报采集 2026-09-03 停用: 港股源日期为会计年度期末而非披露日)")

        ae1, ae2 = st.columns(2)
        with ae1:
            st.write("**自动采集 (akshare / yahoo)**")
            st.caption("分红送转记录")
            if st.button("采集股票池事件", type="primary", key="qp_collect_events"):
                with st.spinner("正在采集分红事件..."):
                    results = quant_data.batch_collect_events()
                total_d = sum(r["dividends"] for r in results)
                st.success(f"完成! 分红事件 {total_d} 条")
                for r in results:
                    st.write(f"  {r['code']}: 分红{r['dividends']}")

        with ae2:
            st.write("**手动录入**")
            st.caption("政策/公告/行业新闻/宏观经济等")
            st.info("请到「事件管理」标签页手动录入事件，支持自定义类型、方向和影响等级。")

        st.divider()

        # --- 数据清洗与对齐 (daily_feature_base) ---
        st.subheader("🧹 数据清洗与对齐 → daily_feature_base")
        st.caption("目标表: daily_feature_base (后续所有分析的数据源) | 规则: 日线行情为基准 + 被动信号按当日映射 + 盘后事件后移到下一交易日 + 多信号0/1哑变量")

        with st.expander("清洗规则说明", expanded=False):
            st.write("""
**1. 基准时间锚定**
- 以日线行情的交易日期为基准
- 盘中触发的被动信号 → 归属到触发当日的 K 线
- 盘后发布的主动事件（>=15:00）→ 归属到下一个交易日

**2. 生成宽表骨架**
- 以 daily_quotes 为主表，左连接 passive_signals 和 active_events
- 每一行 = 某只股票某一天的完整信息：行情 + 当日信号标记 + 当日事件标记

**3. 多信号处理**
- 同一日触发多类信号时，用 0/1 哑变量分别标记每类信号
- 不合并为单一字段，保留各类型独立标记
""")

        wt1, wt2 = st.columns([1, 2])
        with wt1:
            if st.button("生成宽表（全股票池）", type="primary", key="qp_gen_wide"):
                with st.spinner("正在生成宽表..."):
                    results = quant_data.batch_generate_wide_tables()
                total_rows = sum(r["rows"] for r in results)
                st.success(f"完成! {len(results)} 只股票, {total_rows} 行宽表数据")
                for r in results:
                    st.write(f"  {r['code']} ({r['market']}): {r['rows']} 行")

        with wt2:
            wt_stats = quant_data.get_feature_base_stats()
            if wt_stats['total_rows'] > 0:
                sc1, sc2, sc3, sc4 = st.columns(4)
                with sc1:
                    st.metric("宽表行数", wt_stats['total_rows'])
                with sc2:
                    st.metric("覆盖股票", wt_stats['total_codes'])
                with sc3:
                    st.metric("信号触发日", wt_stats['signal_rows'])
                with sc4:
                    st.metric("事件发生日", wt_stats['event_rows'])

                st.write("**被动信号分布:**")
                sig_df = pd.DataFrame(wt_stats['signal_breakdown'])
                sig_df.columns = ['信号类型', '触发次数']
                st.dataframe(sig_df, use_container_width=True, hide_index=True)

                st.write("**主动事件分布:**")
                evt_df = pd.DataFrame(wt_stats['event_breakdown'])
                evt_df.columns = ['事件类型', '发生次数']
                st.dataframe(evt_df, use_container_width=True, hide_index=True)
            else:
                st.info("宽表尚未生成，请先完成数据采集后点击「生成宽表」按钮。")

        st.divider()

        # 宽表预览
        st.write("**宽表预览**")
        wt_code = st.text_input("输入股票代码查看宽表", placeholder="如 600000", key="qp_wt_code")
        if wt_code.strip():
            wt_df = quant_data.get_feature_base(wt_code.strip())
            if not wt_df.empty:
                st.dataframe(wt_df, use_container_width=True, hide_index=True)
                st.write(f"共 {len(wt_df)} 行, {len(wt_df.columns)} 列")
                sig_cols = [c for c in wt_df.columns if c.startswith('sig_')]
                evt_cols = [c for c in wt_df.columns if c.startswith('evt_')]
                sig_sum = wt_df[sig_cols].sum()
                st.write("**各信号触发次数:**")
                st.dataframe(sig_sum[sig_sum > 0].reset_index().rename(columns={'index': '信号类型', 0: '次数'}),
                             use_container_width=True, hide_index=True)
            else:
                st.warning(f"未找到 {wt_code.strip()} 的宽表数据，请先生成宽表。")

        st.divider()

        # --- 模块8: 历史回填 ---
        st.subheader("🧱 历史回填与信号回放（模块 8）")
        st.caption(
            f"行情回填至 {quant_data.QUOTE_BACKFILL_START}（多回 1 年做指标预热）· "
            f"信号回放自 {quant_data.REPLAY_START}（source=replay，与实采 live 双口径分离）· "
            "回测冻结协议：数据扩展允许，参数/池/策略变更禁止")

        bf_status = quant_data.get_backfill_status()
        _dep_notes = quant_data.get_source_depth_notes()
        bf_c1, bf_c2 = st.columns(2)
        with bf_c1:
            st.write("**行情覆盖（当前池）**")
            st.dataframe(pd.DataFrame([{
                "代码": s['code'], "市场": s['market'], "行数": s['n'],
                "起点": s['s'], "终点": s['e'],
                "深度": (_dep_notes.get(s['code']) or {}).get('note')
                or ("回填对齐" if s['s'] and s['s'] <= quant_data.QUOTE_BACKFILL_START
                    else "未校验"),
            } for s in bf_status['stocks']]), use_container_width=True, hide_index=True)
            st.caption(
                f"指标 {bf_status['indicators']['rows']} 行 · "
                f"宽表 {bf_status['wide']['rows']} 行（{bf_status['wide']['start']} ~ {bf_status['wide']['end']}）")
        with bf_c2:
            st.write("**信号来源分布**")
            st.dataframe(pd.DataFrame([{
                "来源": s['source'], "条数": s['n'],
                "起点": s['s'], "终点": s['e'],
            } for s in bf_status['signal_sources']] or [{"来源": "—", "条数": 0, "起点": "—", "终点": "—"}]),
                use_container_width=True, hide_index=True)
            m = bf_status['macro']
            st.caption(
                f"宏观日历 {m['rows']} 行（{m['start']} ~ {m['end']}，{m['days']} 天）· "
                f"回填目标 {quant_data.MACRO_BACKFILL_TARGET}（源深度限制）")

        bf_btn1, bf_btn2 = st.columns(2)
        with bf_btn1:
            if st.button("▶️ 回填行情+回放信号+重建宽表", type="primary", key="m8_backfill"):
                with st.status("模块8 回填链运行中...", expanded=True) as m8_status:
                    def _m8_log(msg):
                        st.write(str(msg))
                    try:
                        rep = quant_data.run_m8_backfill_chain(log=_m8_log)
                        m8_status.update(
                            label=(f"✅ 回填链完成：行情 {len(quant_data.get_stock_pool())} 股 · 回放新增 "
                                   f"{rep['replay']['new_inserted']} 条信号"),
                            state="complete", expanded=False)
                        st.rerun()
                    except Exception as e:
                        m8_status.update(label="❌ 回填链异常终止", state="error", expanded=True)
                        st.exception(e)
        with bf_btn2:
            if st.button("🔁 模块3 三口径重算（含 RSI24 时间分割）", key="m8_m3"):
                with st.spinner("三口径统计 + RSI24 时间分割验证中..."):
                    try:
                        m3r = quant_data.run_module3_analysis()
                        v = m3r.get('rsi24_verdict', {}).get('signals', {})
                        parts = [f"{k}: {x.get('verdict')}" for k, x in v.items()]
                        st.success("模块3 三口径重算完成 · RSI24 判定: " + "；".join(parts))
                    except Exception as e:
                        st.error(f"重算失败: {e}")

        st.caption("宏观日历回填（2023-01 起，约 940 天 × 3.5s ≈ 55 分钟）以独立后台进程执行，"
                   "幂等可断点续采，不阻塞页面")
        if st.button("🌍 启动宏观日历回填（后台）", key="m8_macro"):
            import subprocess
            import sys
            import os
            try:
                qd_dir = os.path.dirname(os.path.abspath(quant_data.__file__))
                logf = open(os.path.join(qd_dir, "macro_backfill.log"), "a", encoding="utf-8")
                subprocess.Popen(
                    [sys.executable, "-c",
                     "import quant_data;quant_data.backfill_macro_events("
                     "start=quant_data.MACRO_BACKFILL_TARGET)"],
                    cwd=qd_dir, stdout=logf, stderr=subprocess.STDOUT)
                st.success("宏观回填已在后台启动 · 进度见 macro_backfill.log · "
                           "刷新本页可在上方「宏观日历」覆盖区间看到扩展")
            except Exception as e:
                st.error(f"后台启动失败: {e}")

        # --- 模块11: 分钟级数据(观察口径) ---
        st.subheader("🕐 分钟级数据（模块 11 · 观察口径）")
        st.caption(
            "Yahoo 分钟K线 · 5m 前向采集（60 天容错窗）+ 60m 回填（近 2 年）· "
            "未复权（源无 adjclose，与日线 qfq 对齐留到分析层）· "
            "仅观察用途不触碰冻结回测 · 每日管道自动增量（近 7 天）· "
            "源窗口硬限：1m 仅 7 天 / 5m·15m·30m 仅 60 天 / 60m 730 天")

        _iv_status = quant_data.get_intraday_status()
        st.dataframe(pd.DataFrame([{
            "代码": r['code'], "名称": r['name'], "市场": r['market'],
            "粒度": r['interval'], "行数": r['rows'],
            "起点": r['start'], "终点": r['end'],
        } for r in _iv_status['rows']]), use_container_width=True, hide_index=True)

        if st.button("▶️ 回填分钟数据（5m 近60天 + 60m 近2年，幂等）", key="m11_backfill"):
            with st.status("分钟数据回填中...", expanded=True) as m11_status:
                def _m11_log(msg):
                    st.write(str(msg))
                try:
                    rep = quant_data.collect_intraday_quotes(log=_m11_log)
                    tot_new = sum(v['new'] for v in rep['rows'].values())
                    m11_status.update(
                        label=(f"✅ 分钟数据回填完成：{rep['stocks']} 股 × "
                               f"{len(rep['intervals'])} 粒度，新增 {tot_new} 根"),
                        state="complete", expanded=False)
                    st.rerun()
                except Exception as e:
                    m11_status.update(label="❌ 分钟数据回填异常", state="error",
                                      expanded=True)
                    st.exception(e)

    # --- Tab 2: 信号检测 ---
    with quant_tab2:
        st.subheader("被动信号检测")

        # 运行检测
        if st.button("运行信号检测", type="primary", key="qd_detect"):
            with st.spinner("正在检测技术指标信号..."):
                results = quant_data.run_signal_detection()
            total_sigs = sum(r["signals"] for r in results)
            st.success(f"检测完成! 共 {total_sigs} 个信号")
            for r in results:
                if r["signals"] > 0:
                    st.write(f"  {r['code']} ({r['name']}): {r['signals']} 个信号")

        st.divider()

        # 信号查询
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            sig_code = st.text_input("股票代码筛选", placeholder="留空查看全部", key="qd_sig_code")
        with fc2:
            sig_type = st.selectbox("信号类型", ["全部", "macd_cross", "rsi_oversold", "rsi_overbought",
                                                  "kdj_cross", "boll_break", "volume_surge", "price_limit"],
                                    key="qd_sig_type")
        with fc3:
            sig_dir = st.selectbox("方向", ["全部", "bullish", "bearish", "neutral"], key="qd_sig_dir")

        signals = quant_data.get_passive_signals(
            code=sig_code.strip().upper() if sig_code else None,
            signal_type=sig_type if sig_type != "全部" else None,
            direction=sig_dir if sig_dir != "全部" else None,
            limit=200
        )

        if signals:
            st.write(f"**共 {len(signals)} 个信号**")
            sig_df = pd.DataFrame([{
                "触发时间": s["trigger_time"],
                "代码": s["code"],
                "名称": s.get("name", ""),
                "信号类型": s["signal_type"],
                "子类型": s.get("signal_subtype", ""),
                "方向": "看多" if s["direction"]=="bullish" else ("看空" if s["direction"]=="bearish" else "中性"),
                "价格": s.get("price", 0),
                "指标值": round(s["indicator_value"], 2) if s.get("indicator_value") else None,
                "阈值": s.get("threshold", ""),
                "描述": s.get("description", ""),
            } for s in signals])
            st.dataframe(sig_df, use_container_width=True, hide_index=True)

            # 方向分布图
            dir_counts = sig_df["方向"].value_counts()
            fig_dir = go.Figure(go.Bar(
                x=dir_counts.index, y=dir_counts.values,
                marker_color=["#1DC981" if d=="看多" else "#E8463A" if d=="看空" else "#888" for d in dir_counts.index]
            ))
            fig_dir.update_layout(title="信号方向分布", template="plotly_white", height=300,
                                  xaxis_title="方向", yaxis_title="数量")
            st.plotly_chart(fig_dir, use_container_width=True)

            # 信号类型分布
            type_counts = sig_df["信号类型"].value_counts()
            fig_type = go.Figure(go.Bar(
                x=type_counts.index, y=type_counts.values,
                marker_color="#4B3FE3"
            ))
            fig_type.update_layout(title="信号类型分布", template="plotly_white", height=300,
                                   xaxis_title="类型", yaxis_title="数量")
            st.plotly_chart(fig_type, use_container_width=True)
        else:
            st.info("暂无被动信号数据。请先采集日线数据并运行信号检测。")

    # --- Tab 3: 事件管理 ---
    with quant_tab3:
        st.subheader("主动事件管理")

        # 添加事件
        with st.expander("添加主动事件", expanded=True):
            ec1, ec2, ec3 = st.columns(3)
            with ec1:
                ev_code = st.text_input("股票代码", key="ae_code")
            with ec2:
                ev_market = st.selectbox("市场", ["A股", "美股", "港股"], key="ae_market")
            with ec3:
                ev_time = st.text_input("事件时间", value=datetime.now().strftime('%Y-%m-%d %H:%M:00'),
                                        key="ae_time")

            ec4, ec5 = st.columns(2)
            with ec4:
                ev_type = st.selectbox("事件类型", [
                    "earnings", "policy", "industry", "announcement", "dividend", "macro"
                ], format_func=lambda x: {
                    "earnings": "财报", "policy": "政策", "industry": "行业新闻",
                    "announcement": "公司公告", "dividend": "分红", "macro": "宏观经济"
                }[x], key="ae_type")
            with ec5:
                ev_subtype = st.text_input("事件子类型", placeholder="如 季报/降息/并购", key="ae_subtype")

            ec6, ec7 = st.columns(2)
            with ec6:
                ev_dir = st.selectbox("方向", ["positive", "negative", "neutral"],
                                       format_func=lambda x: {"positive":"利好","negative":"利空","neutral":"中性"}[x],
                                       key="ae_dir")
            with ec7:
                ev_level = st.selectbox("影响等级", ["critical", "major", "minor"],
                                        format_func=lambda x: {"critical":"重大","major":"一般","minor":"轻微"}[x],
                                        key="ae_level")

            ev_title = st.text_input("事件标题", key="ae_title")
            ev_content = st.text_area("事件内容", key="ae_content")
            ev_source = st.text_input("来源(URL)", key="ae_source")

            if st.button("添加事件", type="primary", key="ae_add"):
                if ev_code.strip() and ev_title.strip():
                    quant_data.add_active_event(
                        ev_code.strip().upper(), ev_market, "", ev_time,
                        ev_type, ev_subtype, ev_dir, ev_level, ev_title, ev_content, ev_source
                    )
                    st.success(f"事件已添加: {ev_title}")
                    st.rerun()
                else:
                    st.warning("请填写股票代码和事件标题")

        st.divider()

        # 查看事件
        events = quant_data.get_active_events(limit=500)
        if events:
            ev_type_map = {"earnings":"财报","policy":"政策","industry":"行业",
                           "announcement":"公告","dividend":"分红","macro":"宏观"}
            ev_dir_map = {"positive":"利好","negative":"利空","neutral":"中性"}
            ev_level_map = {"critical":"重大","major":"一般","minor":"轻微"}

            ev_df = pd.DataFrame([{
                "ID": e["id"],
                "时间": e["event_time"],
                "日期": e.get("trade_date", str(e["event_time"])[:10]),
                "代码": e["code"],
                "类型": ev_type_map.get(e["event_type"], e["event_type"]),
                "方向": ev_dir_map.get(e["direction"], e["direction"]),
                "等级": ev_level_map.get(e["impact_level"], e["impact_level"]),
                "标题": e.get("title", ""),
                "来源": e.get("source", ""),
            } for e in events])

            # --- 可视化图表 ---
            st.subheader("事件可视化")

            vc1, vc2 = st.columns(2)
            with vc1:
                type_counts = ev_df["类型"].value_counts()
                fig_pie = go.Figure(data=[go.Pie(
                    labels=type_counts.index, values=type_counts.values,
                    hole=0.4, textinfo="label+percent+value"
                )])
                fig_pie.update_layout(title="事件类型分布", height=350,
                                      showlegend=True, margin=dict(t=50, b=20))
                st.plotly_chart(fig_pie, use_container_width=True)

            with vc2:
                dir_counts = ev_df["方向"].value_counts()
                colors = {"利好":"#e74c3c","利空":"#2ecc71","中性":"#95a5a6"}
                fig_bar = go.Figure(data=[go.Bar(
                    x=dir_counts.index, y=dir_counts.values,
                    marker_color=[colors.get(x, "#3498db") for x in dir_counts.index],
                    text=dir_counts.values, textposition="outside"
                )])
                fig_bar.update_layout(title="事件方向分布", height=350,
                                       margin=dict(t=50, b=20),
                                       xaxis_title="方向", yaxis_title="数量")
                st.plotly_chart(fig_bar, use_container_width=True)

            # 事件时间线散点图
            ev_df["日期"] = pd.to_datetime(ev_df["日期"])
            fig_timeline = go.Figure()
            type_colors = {"财报":"#e74c3c","政策":"#9b59b6","行业":"#f39c12",
                           "公告":"#3498db","分红":"#1abc9c","宏观":"#34495e"}
            for ev_type in ev_df["类型"].unique():
                subset = ev_df[ev_df["类型"] == ev_type]
                fig_timeline.add_trace(go.Scatter(
                    x=subset["日期"], y=subset["代码"], mode="markers",
                    name=ev_type, marker=dict(size=10,
                                              color=type_colors.get(ev_type, "#3498db")),
                    text=subset["标题"], hovertemplate="<b>%{text}</b><br>%{x} %{y}<extra></extra>"
                ))
            fig_timeline.update_layout(title="事件时间线（按股票+类型）", height=400,
                                        margin=dict(t=50, b=20),
                                        xaxis_title="日期", yaxis_title="股票代码")
            st.plotly_chart(fig_timeline, use_container_width=True)

            # 按股票分组的堆叠柱状图
            stock_evt = ev_df.groupby(["代码","类型"]).size().unstack(fill_value=0)
            fig_stack = go.Figure()
            for col in stock_evt.columns:
                fig_stack.add_trace(go.Bar(
                    x=stock_evt.index, y=stock_evt[col], name=col
                ))
            fig_stack.update_layout(title="各股票事件数量（按类型堆叠）", height=350,
                                     barmode="stack", margin=dict(t=50, b=20),
                                     xaxis_title="股票代码", yaxis_title="事件数量")
            st.plotly_chart(fig_stack, use_container_width=True)

            # 影响等级热力图
            heat_df = ev_df.groupby(["代码","等级"]).size().unstack(fill_value=0)
            for level in ["重大","一般","轻微"]:
                if level not in heat_df.columns:
                    heat_df[level] = 0
            heat_df = heat_df[["重大","一般","轻微"]]
            fig_heat = go.Figure(data=go.Heatmap(
                z=heat_df.values, x=heat_df.columns.tolist(),
                y=heat_df.index.tolist(), colorscale="YlOrRd",
                text=heat_df.values, texttemplate="%{text}",
                hovertemplate="股票:%{y} 等级:%{x} 数量:%{z}<extra></extra>"
            ))
            fig_heat.update_layout(title="影响等级分布热力图", height=300,
                                    margin=dict(t=50, b=20),
                                    xaxis_title="影响等级", yaxis_title="股票代码")
            st.plotly_chart(fig_heat, use_container_width=True)

            # 事件叠加K线图
            st.divider()
            st.subheader("事件叠加K线")
            evt_stocks = ev_df["代码"].unique()
            if len(evt_stocks) > 0:
                sel_stock = st.selectbox("选择股票", evt_stocks, key="evt_kline_stock")
                stock_events = ev_df[ev_df["代码"] == sel_stock]
                stock_info = quant_data.get_stock_from_pool(sel_stock)
                market = stock_info["market"] if stock_info else "A股"

                kline_df = quant_data.get_daily_quotes(sel_stock, days=180)
                if kline_df is not None and len(kline_df) > 0:
                    fig_k = go.Figure(data=[go.Candlestick(
                        x=kline_df["trade_date"], open=kline_df["open"],
                        high=kline_df["high"], low=kline_df["low"],
                        close=kline_df["close"], name="K线"
                    )])

                    dir_colors = {"利好":"#e74c3c","利空":"#2ecc71","中性":"#f39c12"}
                    for _, row in stock_events.iterrows():
                        evt_date = row["日期"]
                        kline_row = kline_df[kline_df["trade_date"] == evt_date.strftime("%Y-%m-%d")]
                        if kline_row.empty:
                            nearest = kline_df[kline_df["trade_date"] <= evt_date.strftime("%Y-%m-%d")]
                            if nearest.empty:
                                continue
                            evt_y = nearest.iloc[-1]["high"] * 1.03
                        else:
                            evt_y = kline_row.iloc[0]["high"] * 1.03

                        fig_k.add_trace(go.Scatter(
                            x=[evt_date], y=[evt_y], mode="markers",
                            marker=dict(size=14, symbol="triangle-down",
                                         color=dir_colors.get(row["方向"], "#f39c12")),
                            name=row["类型"], text=row["标题"],
                            hovertemplate=f"<b>{row['类型']}</b><br>{row['标题']}<br>%{{x}}<extra></extra>"
                        ))

                    fig_k.update_layout(title=f"{sel_stock} 事件叠加K线", height=450,
                                         xaxis_rangeslider_visible=False,
                                         margin=dict(t=50, b=20))
                    st.plotly_chart(fig_k, use_container_width=True)
                else:
                    st.info(f"无 {sel_stock} 的日线数据，请先在「数据采集」中采集行情")

            st.divider()
            st.write(f"**共 {len(events)} 个事件**")
            st.dataframe(ev_df[["ID","时间","代码","类型","方向","等级","标题","来源"]],
                         use_container_width=True, hide_index=True)

            # 删除事件
            del_id = st.number_input("删除事件ID", min_value=1, step=1, key="ae_del_id")
            if st.button("删除事件", key="ae_del"):
                quant_data.delete_active_event(int(del_id))
                st.success(f"已删除事件 ID: {del_id}")
                st.rerun()
        else:
            st.info("暂无主动事件数据，请先在「数据采集」中采集事件或在上方手动添加")

    # --- Tab 4: 数据概览 ---
    with quant_tab4:
        st.subheader("数据概览")
        coverage = quant_data.get_data_coverage()

        # 总览指标
        mc1, mc2, mc3, mc4 = st.columns(4)
        with mc1:
            st.metric("日线记录数", f"{coverage['total_quotes']:,}")
        with mc2:
            st.metric("覆盖股票数", coverage['total_codes'])
        with mc3:
            st.metric("被动信号数", coverage['total_signals'])
        with mc4:
            st.metric("主动事件数", coverage['total_events'])

        st.write(f"**数据时间范围:** {coverage['date_range']}")
        st.write(f"**活跃股票池:** {coverage['pool_count']} 只")

        # 按市场分布
        if coverage.get('by_market'):
            st.subheader("按市场分布")
            mk_data = [{"市场": m["market"], "股票数": m["codes"], "日线数": m["rows"]}
                       for m in coverage['by_market']]
            st.dataframe(pd.DataFrame(mk_data), use_container_width=True, hide_index=True)

        # 信号统计
        stats = quant_data.get_signal_stats()
        if stats['total'] > 0:
            st.subheader("信号统计")
            sc1, sc2 = st.columns(2)
            with sc1:
                dir_data = [{"方向": "看多" if d["direction"]=="bullish" else "看空" if d["direction"]=="bearish" else "中性",
                             "数量": d["c"]} for d in stats['by_direction']]
                st.dataframe(pd.DataFrame(dir_data), use_container_width=True, hide_index=True)
            with sc2:
                type_data = [{"类型": t["signal_type"], "子类型": t.get("signal_subtype",""),
                             "数量": t["c"]} for t in stats['by_type']]
                st.dataframe(pd.DataFrame(type_data), use_container_width=True, hide_index=True)

        # 有数据的股票列表
        stocks_with_data = quant_data.get_stock_list_with_data()
        if stocks_with_data:
            st.subheader("已有日线数据的股票")
            sd_df = pd.DataFrame([{
                "代码": s["code"],
                "市场": s["market"],
                "天数": s["days"],
                "起始日": s["start"],
                "截止日": s["end"],
            } for s in stocks_with_data])
            st.dataframe(sd_df, use_container_width=True, hide_index=True)

            # 选择股票查看日线图
            st.divider()
            st.subheader("日线行情图")
            sel_stock = st.selectbox("选择股票", [s["code"] for s in stocks_with_data], key="qd_chart_sel")
            if sel_stock:
                df_q = quant_data.get_daily_quotes(sel_stock, days=250)
                if not df_q.empty:
                    df_q = quant_data.calc_all_indicators(df_q)
                    fig_q = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                         row_heights=[0.7, 0.3], vertical_spacing=0.05)
                    fig_q.add_trace(go.Candlestick(
                        x=df_q['date'], open=df_q['open'], high=df_q['high'],
                        low=df_q['low'], close=df_q['close'],
                        increasing_line_color='#1DC981', decreasing_line_color='#E8463A',
                        name='K线'
                    ), row=1, col=1)
                    if 'MA20' in df_q.columns:
                        fig_q.add_trace(go.Scatter(
                            x=df_q['date'], y=df_q['MA20'],
                            mode='lines', name='MA20',
                            line=dict(color='#4B3FE3', width=1)
                        ), row=1, col=1)
                    vol_colors = ['#1DC981' if c >= o else '#E8463A'
                                  for c, o in zip(df_q['close'], df_q['open'])]
                    fig_q.add_trace(go.Bar(
                        x=df_q['date'], y=df_q['volume'], name='成交量',
                        marker_color=vol_colors, showlegend=False
                    ), row=2, col=1)
                    fig_q.update_layout(title=f"{sel_stock} 日线行情", template="plotly_white", height=500)
                    fig_q.update_yaxes(title_text="价格", row=1, col=1)
                    fig_q.update_yaxes(title_text="成交量", row=2, col=1)
                    fig_q.update_xaxes(rangeslider_visible=False)
                    st.plotly_chart(fig_q, use_container_width=True)
        else:
            st.info("暂无日线数据，请先在数据采集标签页中采集")

        # --- 估值数据可视化 ---
        st.divider()
        st.subheader("估值数据概览")

        val_summary = quant_data.get_valuation_summary()
        val_stocks = [s for s in val_summary if s['pe_ratio'] > 0 or s['pb_ratio'] > 0]

        if val_stocks:
            # 估值汇总表
            val_df = pd.DataFrame([{
                "代码": s["code"],
                "市场": s["market"],
                "最新价": s["close"],
                "PE(市盈率)": round(s["pe_ratio"], 2) if s["pe_ratio"] else None,
                "PB(市净率)": round(s["pb_ratio"], 2) if s["pb_ratio"] else None,
                "总市值(亿)": round(s["total_market_cap"] / 1e8, 2) if s["total_market_cap"] else None,
                "流通市值(亿)": round(s["circ_market_cap"] / 1e8, 2) if s["circ_market_cap"] else None,
                "数据日期": s["trade_date"],
            } for s in val_stocks])
            st.dataframe(val_df, use_container_width=True, hide_index=True)

            # PE/PB 趋势图
            st.divider()
            vc1, vc2 = st.columns([3, 1])
            with vc2:
                sel_val_stock = st.selectbox("选择股票查看估值趋势", [s["code"] for s in val_stocks], key="val_trend_sel")
            with vc1:
                st.write("")

            if sel_val_stock:
                val_hist = quant_data.get_valuation_history(sel_val_stock, days=365)
                if val_hist and len(val_hist) > 1:
                    vh_df = pd.DataFrame(val_hist)
                    vh_df['trade_date'] = pd.to_datetime(vh_df['trade_date'])

                    fig_val = make_subplots(rows=3, cols=1, shared_xaxes=True,
                                           row_heights=[0.4, 0.3, 0.3], vertical_spacing=0.08,
                                           subplot_titles=("收盘价", "PE / PB", "总市值"))

                    # 收盘价
                    fig_val.add_trace(go.Scatter(
                        x=vh_df['trade_date'], y=vh_df['close'],
                        mode='lines', name='收盘价',
                        line=dict(color='#2196F3', width=2)
                    ), row=1, col=1)

                    # PE
                    pe_data = vh_df[vh_df['pe_ratio'] > 0]
                    if not pe_data.empty:
                        fig_val.add_trace(go.Scatter(
                            x=pe_data['trade_date'], y=pe_data['pe_ratio'],
                            mode='lines+markers', name='PE',
                            line=dict(color='#E8463A', width=2),
                            yaxis='y2'
                        ), row=2, col=1)

                    # PB
                    pb_data = vh_df[vh_df['pb_ratio'] > 0]
                    if not pb_data.empty:
                        fig_val.add_trace(go.Scatter(
                            x=pb_data['trade_date'], y=pb_data['pb_ratio'],
                            mode='lines+markers', name='PB',
                            line=dict(color='#4B3FE3', width=2),
                            yaxis='y3'
                        ), row=2, col=1)

                    # 总市值
                    mc_data = vh_df[vh_df['total_market_cap'] > 0]
                    if not mc_data.empty:
                        fig_val.add_trace(go.Bar(
                            x=mc_data['trade_date'], y=mc_data['total_market_cap'] / 1e8,
                            name='总市值(亿)', marker_color='#1DC981',
                            showlegend=False
                        ), row=3, col=1)

                    fig_val.update_layout(
                        title=f"{sel_val_stock} 估值趋势",
                        template="plotly_white", height=600,
                        xaxis_rangeslider_visible=False,
                        margin=dict(t=80, b=20)
                    )
                    fig_val.update_yaxes(title_text="价格", row=1, col=1)
                    fig_val.update_yaxes(title_text="PE / PB", row=2, col=1)
                    fig_val.update_yaxes(title_text="市值(亿)", row=3, col=1)
                    st.plotly_chart(fig_val, use_container_width=True)
                else:
                    st.info(f" {sel_val_stock} 的估值历史数据不足，请先采集更多估值数据")

            # PE 横向对比
            st.divider()
            st.subheader("股票估值横向对比")
            comp_stocks = [s for s in val_stocks]
            fig_pe = go.Figure()
            fig_pe.add_trace(go.Bar(
                x=[s["code"] for s in comp_stocks],
                y=[s["pe_ratio"] for s in comp_stocks],
                name="PE", marker_color="#E8463A",
                text=[f"{s['pe_ratio']:.1f}" for s in comp_stocks],
                textposition="outside"
            ))
            fig_pe.update_layout(title="市盈率(PE)对比", template="plotly_white",
                                 height=300, margin=dict(t=50, b=20),
                                 xaxis_title="股票", yaxis_title="PE")
            st.plotly_chart(fig_pe, use_container_width=True)

            fig_pb = go.Figure()
            fig_pb.add_trace(go.Bar(
                x=[s["code"] for s in comp_stocks],
                y=[s["pb_ratio"] for s in comp_stocks],
                name="PB", marker_color="#4B3FE3",
                text=[f"{s['pb_ratio']:.2f}" for s in comp_stocks],
                textposition="outside"
            ))
            fig_pb.update_layout(title="市净率(PB)对比", template="plotly_white",
                                 height=300, margin=dict(t=50, b=20),
                                 xaxis_title="股票", yaxis_title="PB")
            st.plotly_chart(fig_pb, use_container_width=True)

            fig_mc = go.Figure()
            fig_mc.add_trace(go.Bar(
                x=[s["code"] for s in comp_stocks],
                y=[s["total_market_cap"] / 1e8 for s in comp_stocks],
                name="总市值(亿)", marker_color="#1DC981",
                text=[f"{s['total_market_cap']/1e8:.0f}" for s in comp_stocks],
                textposition="outside"
            ))
            fig_mc.update_layout(title="总市值对比(亿元)", template="plotly_white",
                                 height=300, margin=dict(t=50, b=20),
                                 xaxis_title="股票", yaxis_title="总市值(亿)")
            st.plotly_chart(fig_mc, use_container_width=True)
        else:
            st.info("暂无估值数据，请先在「数据采集」标签页点击「采集股票池估值」")

    # --- Tab 5: 技术因子 (模块2: 基础技术因子计算) ---
    with quant_tab5:
        st.subheader("基础技术因子")
        st.caption("基于前复权日线滚动计算，严禁未来函数 · 初始窗口期保留空值 · 入库 daily_indicators 表")

        with st.expander("因子清单 (11类指标)"):
            fc1, fc2 = st.columns(2)
            with fc1:
                st.markdown("""
**趋势类**
- MA5 / MA10 / MA20 / MA60 均线
- MACD: DIF、DEA、柱状值 (12,26,9)

**震荡类**
- RSI(14) Wilder平滑
- KDJ(9,3,3): K、D、J
- 布林带: 上/中/下轨 (20, 2σ)
""")
            with fc2:
                st.markdown("""
**波动类**
- 20日收益率波动率(%)
- ATR(14) 真实波幅均值

**量能类**
- 5/20日均量
- OBV 能量潮

**收益类**
- 当日涨跌幅
- 过去3/5/20日累计涨跌幅
""")

        # 因子计算
        cf1, cf2 = st.columns([3, 1])
        with cf2:
            st.write("")
            if st.button("计算全部股票因子", type="primary", key="qp_calc_factors"):
                with st.spinner("正在计算技术因子..."):
                    results = quant_data.batch_generate_indicators()
                st.success(f"完成! {len(results)} 只股票, 共 {sum(r['rows'] for r in results)} 行因子数据")
                st.session_state["factor_results"] = results
        with cf1:
            st.write("")

        if "factor_results" in st.session_state:
            for r in st.session_state["factor_results"]:
                st.write(f"  {r['code']} ({r['market']}): {r['rows']} 行")

        # 因子覆盖统计
        factor_stats = quant_data.get_indicator_stats()
        if factor_stats:
            st.divider()
            st.write("**因子覆盖统计**")
            fs_df = pd.DataFrame([{
                "代码": s["code"],
                "市场": s["market"],
                "行数": s["total"],
                "日期范围": f"{s['min_date']} ~ {s['max_date']}",
                "MA60有效": s["ma60_ok"],
                "RSI14有效": s["rsi14_ok"],
                "BOLL有效": s["boll_ok"],
                "OBV有效": s["obv_ok"],
            } for s in factor_stats])
            st.dataframe(fs_df, use_container_width=True, hide_index=True)

            # 因子可视化
            st.divider()
            st.write("**单只股票因子可视化**")
            vs1, vs2 = st.columns([2, 1])
            with vs2:
                sel_factor_stock = st.selectbox(
                    "选择股票",
                    [s["code"] for s in factor_stats],
                    key="qp_factor_stock"
                )
                factor_days = st.selectbox(
                    "显示范围", [60, 120, 250, 365], index=1, key="qp_factor_days"
                )
            with vs1:
                st.write("")

            ind_rows = quant_data.get_indicators(sel_factor_stock, days=factor_days)
            if ind_rows:
                ind_df = pd.DataFrame(ind_rows)
                ind_df['trade_date'] = pd.to_datetime(ind_df['trade_date'])

                # 主图: K线 + 均线 + 布林带 / 成交量 / MACD / RSI+KDJ
                fig_f = make_subplots(
                    rows=4, cols=1, shared_xaxes=True,
                    row_heights=[0.42, 0.16, 0.21, 0.21],
                    vertical_spacing=0.03,
                    subplot_titles=("K线 + 均线 + 布林带", "成交量", "MACD", "RSI / KDJ")
                )
                fig_f.add_trace(go.Candlestick(
                    x=ind_df['trade_date'], open=ind_df['open'], high=ind_df['high'],
                    low=ind_df['low'], close=ind_df['close'],
                    name="K线", increasing_line_color="#E8463A",
                    decreasing_line_color="#1DC981"
                ), row=1, col=1)
                ma_colors = {'ma5': '#FF9800', 'ma10': '#2196F3', 'ma20': '#9C27B0', 'ma60': '#795548'}
                for ma, color in ma_colors.items():
                    ma_data = ind_df[~ind_df[ma].isna()]
                    if not ma_data.empty:
                        fig_f.add_trace(go.Scatter(
                            x=ma_data['trade_date'], y=ma_data[ma], mode='lines',
                            name=ma.upper(), line=dict(color=color, width=1.2)
                        ), row=1, col=1)
                for boll, color, dash in [('boll_up', '#90A4AE', 'dot'), ('boll_low', '#90A4AE', 'dot')]:
                    bd = ind_df[~ind_df[boll].isna()]
                    if not bd.empty:
                        fig_f.add_trace(go.Scatter(
                            x=bd['trade_date'], y=bd[boll], mode='lines',
                            name=boll.replace('_', ' ').upper(),
                            line=dict(color=color, width=1, dash=dash)
                        ), row=1, col=1)

                vol_colors = ['#E8463A' if c >= o else '#1DC981'
                              for c, o in zip(ind_df['close'], ind_df['open'])]
                fig_f.add_trace(go.Bar(
                    x=ind_df['trade_date'], y=ind_df['volume'], name="成交量",
                    marker_color=vol_colors, showlegend=False
                ), row=2, col=1)
                vma_data = ind_df[~ind_df['vol_ma5'].isna()]
                if not vma_data.empty:
                    fig_f.add_trace(go.Scatter(
                        x=vma_data['trade_date'], y=vma_data['vol_ma5'], mode='lines',
                        name="VOL_MA5", line=dict(color='#FF9800', width=1.2)
                    ), row=2, col=1)

                fig_f.add_trace(go.Bar(
                    x=ind_df['trade_date'], y=ind_df['macd_bar'], name="MACD柱",
                    marker_color=['#E8463A' if v >= 0 else '#1DC981' for v in ind_df['macd_bar'].fillna(0)],
                    showlegend=False
                ), row=3, col=1)
                fig_f.add_trace(go.Scatter(
                    x=ind_df['trade_date'], y=ind_df['dif'], mode='lines',
                    name="DIF", line=dict(color='#FF9800', width=1.5)
                ), row=3, col=1)
                fig_f.add_trace(go.Scatter(
                    x=ind_df['trade_date'], y=ind_df['dea'], mode='lines',
                    name="DEA", line=dict(color='#2196F3', width=1.5)
                ), row=3, col=1)

                fig_f.add_trace(go.Scatter(
                    x=ind_df['trade_date'], y=ind_df['rsi14'], mode='lines',
                    name="RSI14", line=dict(color='#7B1FA2', width=1.5)
                ), row=4, col=1)
                fig_f.add_trace(go.Scatter(
                    x=ind_df['trade_date'], y=ind_df['kdj_k'], mode='lines',
                    name="K", line=dict(color='#E8463A', width=1)
                ), row=4, col=1)
                fig_f.add_trace(go.Scatter(
                    x=ind_df['trade_date'], y=ind_df['kdj_d'], mode='lines',
                    name="D", line=dict(color='#2196F3', width=1)
                ), row=4, col=1)
                fig_f.add_hline(y=70, line_dash='dot', line_color='#E8463A',
                                line_width=0.8, row=4, col=1)
                fig_f.add_hline(y=30, line_dash='dot', line_color='#1DC981',
                                line_width=0.8, row=4, col=1)

                fig_f.update_layout(
                    height=950, template="plotly_white",
                    xaxis_rangeslider_visible=False,
                    margin=dict(t=40, b=20), legend=dict(orientation='h', y=1.02)
                )
                st.plotly_chart(fig_f, use_container_width=True)

                # 副图: OBV + ATR + 波动率
                st.write("**量能与波动因子**")
                fig_g = make_subplots(
                    rows=2, cols=1, shared_xaxes=True,
                    row_heights=[0.5, 0.5], vertical_spacing=0.1,
                    subplot_titles=("OBV 能量潮", "ATR(14) / 20日波动率(%)")
                )
                fig_g.add_trace(go.Scatter(
                    x=ind_df['trade_date'], y=ind_df['obv'], mode='lines',
                    name="OBV", line=dict(color='#00897B', width=1.5), showlegend=False
                ), row=1, col=1)
                atr_data = ind_df[~ind_df['atr14'].isna()]
                fig_g.add_trace(go.Scatter(
                    x=atr_data['trade_date'], y=atr_data['atr14'], mode='lines',
                    name="ATR14", line=dict(color='#E8463A', width=1.5)
                ), row=2, col=1)
                vol20_data = ind_df[~ind_df['volatility20'].isna()]
                fig_g.add_trace(go.Scatter(
                    x=vol20_data['trade_date'], y=vol20_data['volatility20'], mode='lines',
                    name="波动率20", line=dict(color='#2196F3', width=1.5)
                ), row=2, col=1)
                fig_g.update_layout(height=420, template="plotly_white",
                                    margin=dict(t=40, b=20))
                st.plotly_chart(fig_g, use_container_width=True)

                # 收益类因子 + 因子明细表
                st.write("**收益类因子趋势 (%)**")
                fig_r = go.Figure()
                for col, color in [('ret_3d', '#FF9800'), ('ret_5d', '#2196F3'), ('ret_20d', '#9C27B0')]:
                    rd = ind_df[~ind_df[col].isna()]
                    if not rd.empty:
                        fig_r.add_trace(go.Scatter(
                            x=rd['trade_date'], y=rd[col], mode='lines',
                            name=col.replace('_', '').upper(),
                            line=dict(color=color, width=1.3)
                        ))
                fig_r.add_hline(y=0, line_dash='dot', line_color='#888', line_width=0.8)
                fig_r.update_layout(height=320, template="plotly_white",
                                    margin=dict(t=30, b=20))
                st.plotly_chart(fig_r, use_container_width=True)

                st.write("**因子数据明细 (最新30行)**")
                detail_df = ind_df.tail(30).copy()
                detail_df['trade_date'] = detail_df['trade_date'].dt.strftime('%Y-%m-%d')
                show_cols = ['trade_date', 'code', 'close', 'ma5', 'ma10', 'ma20', 'ma60',
                             'dif', 'dea', 'macd_bar', 'rsi14', 'kdj_k', 'kdj_d', 'kdj_j',
                             'boll_up', 'boll_mid', 'boll_low', 'volatility20', 'atr14',
                             'vol_ma5', 'vol_ma20', 'obv', 'change_pct', 'ret_3d', 'ret_5d', 'ret_20d']
                st.dataframe(detail_df[show_cols], use_container_width=True, hide_index=True)
            else:
                st.info("该股票暂无因子数据，请点击上方「计算全部股票因子」")
        else:
            st.info("暂无因子数据，请点击上方「计算全部股票因子」按钮")


    # --- Tab 8: 宏观日历 (模块6: 先于策略回测 Tab 7 渲染, 规避其条件分支风险) ---
    with quant_tab8:
        st.subheader("宏观经济日历与事件研究 (模块6 + 模块9 FDR/Overlay)")
        st.caption("数据源: 百度股市通 sapi (自写客户端带浏览器指纹, 审计 2026-09-02) · 事件时刻为北京时间 · "
                   "研究口径: 常数均值模型 AR (标的=指数自身估计窗均值), 事件窗[-5,+10], 估计窗[-120,-10] min60, "
                   "t 检验/_agg_group 共享统计核心 · 分组: 家族×地区×指数×意外方向(公布vs预期) · "
                   "模块9: ⑥⑦ 节 FDR 校正裁决(BH q=0.05 按窗口分层) + 宏观 Overlay 封锁配置(已冻结)")

        # --- ① 采集管理 ---
        mst = quant_data.get_macro_status()
        with st.expander("⚙️ 采集管理", expanded=(mst['rows'] == 0)):
            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("事件总行数", f"{mst['rows']:,}")
            mc2.metric("覆盖区间", f"{mst['start'] or '—'} ~ {mst['end'] or '—'}")
            mc3.metric("高重要性(★2)", f"{mst['high']:,}")
            mc4.metric("覆盖地区", len(mst['regions']))
            mcol1, mcol2 = st.columns(2)
            with mcol1:
                if st.button("采集最近3天 (日常刷新)", type="primary", key="qp_macro_recent"):
                    from datetime import datetime as _dt, timedelta as _td
                    dates = [(_dt.now() - _td(days=i)).strftime('%Y-%m-%d')
                             for i in range(2, -1, -1)]
                    with st.spinner("采集中(约10秒)..."):
                        try:
                            r = quant_data.collect_macro_events(dates)
                            st.success(f"完成: 新采 {r['ok']} 天 / {r['rows']} 行 "
                                       f"(跳过 {r['skipped']}, 失败 {r['failed']})")
                            st.rerun()
                        except Exception as e:
                            st.error(f"采集失败: {e}")
            with mcol2:
                if st.button("运行 / 重跑 宏观事件研究", type="primary", key="qp_macro_study"):
                    if mst['rows'] == 0:
                        st.error("macro_events 为空, 先采集数据")
                    else:
                        with st.spinner("计算 AR/CAR 并入库(约10秒)..."):
                            try:
                                meta = quant_data.run_macro_study()
                                st.success(f"完成: {meta['n_events']} 条高重要性事件 → "
                                           f"{meta['n_groups']} 组统计, 正在刷新...")
                                st.rerun()
                            except Exception as e:
                                st.error(f"研究失败: {e}")
            st.caption("全窗口回填(2025-07-29起, 首次约25分钟)用脚本 backfill_macro_events() 执行; "
                       "最近3天强制重采以补填实际值, 其余日期已有即跳过(幂等)")

        if mst['rows'] == 0:
            st.info("macro_events 为空: 全窗口回填尚未执行(后台脚本运行中或未启动), "
                    "完成后点击上方「运行宏观事件研究」")
        else:
            # --- ② 宏观日历浏览器 ---
            st.write("**② 宏观日历浏览器**")
            mcal1, mcal2, mcal3 = st.columns([1, 2, 1])
            with mcal1:
                m_day = st.date_input(
                    "查看日期", value=pd.to_datetime(mst['end']),
                    min_value=pd.to_datetime(mst['start']),
                    max_value=pd.to_datetime(mst['end']),
                    key="qp_macro_day").strftime('%Y-%m-%d')
            with mcal2:
                m_regions = st.multiselect(
                    "地区筛选", mst['regions'],
                    default=[r for r in ['美国', '中国', '中国香港', '欧元区', '日本', '英国']
                             if r in mst['regions']],
                    key="qp_macro_regions")
            with mcal3:
                m_high_only = st.checkbox("仅高重要性(★2)", value=False, key="qp_macro_high")
            mdf = quant_data.query_macro_events(
                m_day, m_day, regions=m_regions or None,
                star_min=2 if m_high_only else None)
            if mdf.empty:
                st.caption(f"{m_day} 无匹配事件(周末/节假日正常)")
            else:
                show_mdf = mdf.rename(columns={
                    'trade_date': '日期', 'time': '时间', 'region': '地区',
                    'title': '事件', 'star': '重要性', 'pub_val': '公布',
                    'forecast_val': '预期', 'former_val': '前值'})
                show_mdf['重要性'] = show_mdf['重要性'].map(
                    {1: '★', 2: '★★'}).fillna('')
                st.caption(f"{m_day} · {len(show_mdf)} 条 · 时间为北京时间")
                st.dataframe(show_mdf[['时间', '地区', '事件', '重要性', '公布', '预期', '前值']],
                             use_container_width=True, hide_index=True)

            # --- ③ 事件密度(月度, 完整性可视自检) ---
            st.write("**③ 事件密度 · 月度 (数据完整性自检: 高重要性事件应每月稳定出现)**")
            conn = quant_data.get_db()
            try:
                dens = pd.read_sql_query("""
                    SELECT substr(trade_date,1,7) AS month, region, COUNT(*) AS n
                    FROM macro_events WHERE star=2 AND region IN ('美国','中国','欧元区','日本')
                    GROUP BY month, region ORDER BY month""", conn)
            finally:
                conn.close()
            if not dens.empty:
                fig_dens = go.Figure()
                dens_colors = {'美国': '#1E88E5', '中国': '#E53935',
                               '欧元区': '#43A047', '日本': '#FB8C00'}
                for rg in ['美国', '中国', '欧元区', '日本']:
                    sub = dens[dens['region'] == rg]
                    if not sub.empty:
                        fig_dens.add_trace(go.Bar(
                            x=sub['month'], y=sub['n'], name=rg,
                            marker_color=dens_colors.get(rg, '#90A4AE')))
                fig_dens.update_layout(
                    barmode='stack', yaxis_title="高重要性事件数(条/月)",
                    template="plotly_white", height=320,
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
                st.plotly_chart(fig_dens, use_container_width=True)

            # --- ④ 宏观事件研究结果 ---
            st.write("**④ 宏观事件研究结果 (高重要性事件 × 指数 AR/CAR)**")
            mstudy = quant_data.load_macro_study()
            mres = mstudy['results']
            if mres.empty:
                st.info("研究结果为空: 点击上方「运行 / 重跑 宏观事件研究」")
            else:
                mmeta = mstudy['meta'] or {}
                st.caption(f"最近计算: {mmeta.get('run_ts', '—')} · "
                           f"高重要性事件 {mmeta.get('n_events', '—')} 条 → "
                           f"{mmeta.get('n_groups', '—')} 组 · "
                           f"{mmeta.get('caveat', '')}")
                mwin = st.selectbox(
                    "统计窗口", ['[0,0]', '[0,+1]', '[0,+3]', '[0,+5]', '[0,+10]', '[-5,+10]'],
                    index=3, key="qp_macro_win")
                base_res = mres[(mres['direction'].isna()) & (mres['window'] == mwin) &
                                (mres['n'] >= 3)].copy()
                idx_names = {'.INX': '标普500', 'HSI': '恒生指数', 'sh000300': '沪深300'}
                if base_res.empty:
                    st.caption("当前窗口无 n≥3 的分组")
                else:
                    base_res['指数'] = base_res['index_code'].map(idx_names)
                    car_col = f'CAR{mwin}%'
                    show_res = base_res[['family', 'region', '指数', 'n', 'car_mean',
                                         't_stat', 'p_value', 'sig']].rename(columns={
                        'family': '事件家族', 'region': '地区', 'n': '样本n',
                        'car_mean': car_col, 't_stat': 't值',
                        'p_value': 'p值', 'sig': '显著性'})
                    show_res[car_col] = pd.to_numeric(show_res[car_col], errors='coerce')
                    show_res['t值'] = pd.to_numeric(show_res['t值'], errors='coerce')
                    show_res['p值'] = pd.to_numeric(show_res['p值'], errors='coerce')
                    show_res = show_res.sort_values(['地区', '事件家族', '指数'])
                    st.dataframe(show_res, use_container_width=True, hide_index=True)
                    fig_car = go.Figure()
                    for code, color in [('.INX', '#1E88E5'), ('HSI', '#E53935'),
                                        ('sh000300', '#43A047')]:
                        sub = base_res[base_res['index_code'] == code].sort_values('family')
                        if not sub.empty:
                            fig_car.add_trace(go.Bar(
                                x=sub['family'] + '·' + sub['region'], y=sub['car_mean'],
                                name=idx_names[code], marker_color=color))
                    fig_car.add_hline(y=0, line_color='#9E9E9E', line_width=0.8)
                    fig_car.update_layout(
                        yaxis_title=f"CAR {mwin} (%)", template="plotly_white", height=420,
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                        xaxis_tickangle=-35)
                    st.plotly_chart(fig_car, use_container_width=True)

                # --- ⑤ 意外方向拆分 + 曲线 ---
                st.write("**⑤ 意外方向拆分 (公布 vs 预期) 与 AAR/CAAR 曲线**")
                dir_res = mres[(mres['direction'].notna()) & (mres['n'] >= 3) &
                               (~mres['family'].isin(['全部高重要性']))].copy()
                if dir_res.empty:
                    st.caption("无可拆分的方向分组(需公布与预期均可解析且 n≥3)")
                else:
                    w10 = dir_res[dir_res['window'] == '[0,+10]']
                    show_dir = w10[['family', 'region', 'direction', 'index_code', 'n',
                                    'car_mean', 'p_value', 'sig']].rename(columns={
                        'family': '事件家族', 'region': '地区', 'direction': '意外方向',
                        'index_code': '指数', 'n': '样本n', 'car_mean': 'CAR[0,+10]%',
                        'p_value': 'p值', 'sig': '显著性'})
                    show_dir['指数'] = show_dir['指数'].map(idx_names)
                    show_dir['CAR[0,+10]%'] = pd.to_numeric(show_dir['CAR[0,+10]%'],
                                                            errors='coerce')
                    show_dir['p值'] = pd.to_numeric(show_dir['p值'], errors='coerce')
                    show_dir = show_dir.sort_values(['事件家族', '意外方向', '指数'])
                    st.caption("同一事件的相反意外方向 CAR 应显著异号, 否则方向语义不成立")
                    st.dataframe(show_dir, use_container_width=True, hide_index=True)
                mcur = mstudy['curves']
                curve_groups = sorted(set(mres[mres['n'] >= 3]['group_id']))
                if curve_groups:
                    sel_g = st.selectbox("曲线分组", curve_groups, index=0,
                                         format_func=lambda g: g.replace('|', ' · '),
                                         key="qp_macro_curve")
                    sub = mcur[mcur['group_id'] == sel_g].sort_values('rel_day')
                    if not sub.empty:
                        fig_cv = go.Figure()
                        fig_cv.add_trace(go.Bar(
                            x=sub['rel_day'], y=sub['aar'], name='AAR(%)',
                            marker_color='#90CAF9'))
                        fig_cv.add_trace(go.Scatter(
                            x=sub['rel_day'], y=sub['caar'], name='CAAR(%)',
                            mode='lines+markers', line=dict(color='#E53935', width=2.2)))
                        fig_cv.add_vline(x=0, line_dash='dot', line_color='#9E9E9E')
                        fig_cv.update_layout(
                            xaxis_title="相对交易日 (0=事件日)", yaxis_title="%",
                            title=sel_g.replace('|', ' · '), template="plotly_white",
                            height=360, legend=dict(orientation="h", y=1.1, x=0))
                        st.plotly_chart(fig_cv, use_container_width=True)

                # --- ⑥ 事件明细样例 ---
                with st.expander("⑥ 事件→交易日映射明细 (最近30条, 含排除原因)"):
                    mev = mstudy['events'].sort_values('trade_date', ascending=False).head(30)
                    if mev.empty:
                        st.caption("无映射明细")
                    else:
                        show_ev = mev[['trade_date', 'time', 'region', 'family', 'title',
                                       'index_code', 't_day', 'included', 'reason']].rename(
                            columns={'trade_date': '事件日(北京)', 'time': '时刻',
                                     'region': '地区', 'family': '家族', 'title': '事件',
                                     'index_code': '指数', 't_day': '映射交易日',
                                     'included': '纳入', 'reason': '排除原因'})
                        show_ev['纳入'] = show_ev['纳入'].map({1: '✓', 0: '✗'})
                        st.dataframe(show_ev, use_container_width=True, hide_index=True)

                # --- ⑦ FDR 多重检验校正与宏观 Overlay (模块9) ---
                st.write("**⑦ FDR 多重检验校正与宏观 Overlay (模块9)**")
                try:
                    fv = quant_data.load_fdr_view()
                except Exception as e:
                    fv = None
                    st.error(f"FDR 面板读取失败: {e}")
                if fv is not None:
                    if fv['results'].empty:
                        st.info("FDR 裁决尚未运行(run_macro_fdr): 1602 组名义显著含大量假阳性"
                                "(预期~80组), 未裁决前不得引用任何\"显著\"组; overlay 家族未选出")
                    else:
                        fm, om = fv['fdr_meta'], fv['overlay_meta']
                        lr = fm.get('last_run', {})
                        fc1, fc2, fc3, fc4 = st.columns(4)
                        fc1.metric("检验组数(6窗口族)", f"{lr.get('n_rows', 0):,}")
                        fc2.metric("BH 存活行", lr.get('n_survive_rows', 0))
                        fc3.metric("Overlay 入选组", lr.get('n_eligible_groups', 0))
                        fc4.metric("裁决时间", str(lr.get('run_ts', '—'))[:16])
                        cfg = om.get('config', {})
                        if cfg.get('groups'):
                            st.markdown(f"**入选家族** (四条件: BH存活 · n≥{cfg.get('min_n', 30)} · "
                                        f"CAR<0 只减不加 · 窗口[0,+N]; 护栏: 覆盖率≤{cfg.get('max_cov', 0.5):.0%})")
                            el_df = pd.DataFrame([{
                                '地区': g['region'], '事件家族': g['family'],
                                '指数': g['index_code'], '意外方向': g['direction'],
                                '封锁窗口N': g['window_days'], '样本n': g['n'],
                                'CAR均值%': round(g['car_mean'], 2), 't值': round(g['t_stat'], 2),
                                'p值': round(g['p_value'], 4), 'q值(BH)': round(g['q_value'], 4),
                                '封锁日覆盖率': f"{max(g['coverage'].values()):.1%}" if g.get('coverage') else '—',
                            } for g in cfg['groups']])
                            st.dataframe(el_df, use_container_width=True, hide_index=True)
                        strata = lr.get('strata', {})
                        if strata:
                            st.caption("分层 BH(q=0.05, 每窗口独立检验族): " + " · ".join(
                                f"{w} m={s['m']}/存活{s['n_survive']}/资格{s['n_eligible']}"
                                for w, s in sorted(strata.items())))
                        dg = fm.get('diagnostic_cutoff', {})
                        if dg:
                            if dg.get('zero_survival_warning'):
                                st.error("⚠️ 选择泄漏脆弱性: 主清单在知识截止前子样本中零存活 — "
                                         "overlay 入选可能完全是截止后数据的产物, 前向裁决前不具任何证据力")
                            else:
                                st.caption(f"诊断对照(知识截止 {dg.get('cutoff')} 前子样本, 非门槛): "
                                           f"主清单 {dg.get('n_main')} 组 / 子样本 {dg.get('n_diag')} 组 / "
                                           f"重合 {len(dg.get('overlap', []))} — "
                                           + (f"仅主清单: {', '.join(dg.get('main_only', []))}"
                                              if dg.get('main_only') else '完全重合'))
                        with st.expander("BH 存活组明细 (survive=1):"):
                            surv = fv['results'][fv['results']['survive'] == 1].copy()
                            if not surv.empty:
                                show_sv = surv.head(60)[[
                                    'window', 'region', 'family', 'index_code', 'direction',
                                    'n', 'car_mean', 't_stat', 'p_value', 'q_value',
                                    'overlay_eligible', 'eligible_reason']].rename(columns={
                                        'window': '窗口', 'region': '地区', 'family': '事件家族',
                                        'index_code': '指数', 'direction': '意外方向', 'n': '样本n',
                                        'car_mean': 'CAR%', 't_stat': 't值', 'p_value': 'p值',
                                        'q_value': 'q值', 'overlay_eligible': '入选',
                                        'eligible_reason': '说明'})
                                st.dataframe(show_sv, use_container_width=True, hide_index=True)
                                st.caption("存活≠入选: [0,+0] 无可封锁日、[-5,+10] 含事件前窗、"
                                           "正CAR(只减不加原则)、n<30、覆盖率>50% 均只记录不行动")
                        with st.expander("封锁日台账 (macro_overlay_days, 追加式永不改写):"):
                            if fv['ledger']:
                                ld_df = pd.DataFrame([{
                                    '股票': l['code'], '封锁日数': l['n_days'],
                                    '区间': f"{l['s']} ~ {l['e']}"} for l in fv['ledger']])
                                st.dataframe(ld_df, use_container_width=True, hide_index=True)
                            else:
                                st.caption("台账为空")
                            st.caption("台账刷新由每日管道执行(守卫=完整性截断日, 已入库净值日期永不回补封锁); "
                                       "配置冻结后宏观研究重跑不自动更新 overlay(需新的预注册决策)")

    # --- Tab 7: 策略回测 (模块5: D1引擎 + D2双段批量回测) ---
    # 渲染次序说明: 本块置于 Tab 6(含st.stop)之前, 与 Tab 8 同理; 且本块自身
    # 全程条件渲染, 任何分支都不使用 st.stop(), 保证8个标签页均能完整渲染
    with quant_tab7:
        st.subheader("信号策略回测 (模块5)")
        st.caption("D1 引擎: T日收盘信号 → T+1开盘成交 → 持有N交易日 → 开盘卖出 · K=3槽位等权 · 不做空"
                   " · 跳空成本 = 回测收益 − 模块3统计收益 · D2 双段: full=工程验证 / oos=知识截止日后"
                   "证据参考(净值从1重起, 同窗口B1/B2) · 结果回读自 backtest_* 四表(重启不丢)")

        # --- 回测运行管理(条件渲染, 无st.stop) ---
        def _bt_runs_count():
            conn = quant_data.get_db()
            try:
                return conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]
            except Exception:
                return 0
            finally:
                conn.close()

        n_bt_runs = _bt_runs_count()
        with st.expander("⚙️ 回测运行管理", expanded=(n_bt_runs == 0)):
            st.caption("D2 批量回测 = 8策略×2段(full/oos)×3费率 + 双基准×2段, 多批次入库:"
                       " 跨日批次保留作对照(如 模块8扩窗批次 vs 2026-09-03冻结批次), 同日重跑只覆盖当日批次;"
                       " 选型参数已按 D2.0 协议冻结(2026-08-31), 重跑只刷新数据不改选型;"
                       " forward 样本(2026-09起)以 run_date 追加式入库, 不受影响")
            if st.button("运行 / 重跑 D2 批量回测", type="primary", key="qp_run_d2"):
                with st.spinner("正在执行批量回测并入库(约1-2分钟)..."):
                    try:
                        d2_summary = quant_data.run_d2_backtests()
                        st.success(f"完成: {d2_summary['runs']}个run · 净值{d2_summary['equity_rows']}行"
                                   f" · 交易{d2_summary['trade_rows']}行, 正在刷新...")
                        st.rerun()
                    except Exception as e:
                        st.error(f"回测失败: {e}")

        if n_bt_runs == 0:
            st.info("回测结果为空: 展开上方「回测运行管理」点击运行按钮(需行情/信号/基准数据齐备)")
        else:
            # ---- 批次选择(模块8 多批次对照模式) ----
            conn = quant_data.get_db()
            try:
                batches = conn.execute(
                    "SELECT run_date, COUNT(*) n, MIN(start_date) s, MAX(end_date) e "
                    "FROM backtest_runs GROUP BY run_date ORDER BY run_date DESC").fetchall()
            finally:
                conn.close()
            if len(batches) > 1:
                bsel = st.selectbox(
                    "结果批次(默认最新)", list(range(len(batches))), index=0,
                    key="qp_bt_batch",
                    format_func=lambda i: f"{batches[i]['run_date']} · {batches[i]['s']} ~ {batches[i]['e']}")
                bt_batch_date = batches[bsel]['run_date']
                st.caption(f"共 {len(batches)} 个批次: 跨日批次保留作对照, 同日重跑只覆盖当日; "
                           "冻结批次(旧窗口)与扩窗批次(模块8)的差异即为样本扩展的影响")
            else:
                bt_batch_date = batches[0]['run_date']

            # ---- 数据装载(按所选批次过滤, 重启后直接从库回读) ----
            conn = quant_data.get_db()
            try:
                bt_rows = conn.execute("""
                    SELECT run_id, strategy_id, strategy_name, kind, segment, fee,
                           start_date, end_date, n_days, total_return, annual_return,
                           annual_vol, sharpe, max_drawdown, mdd_start, mdd_end,
                           daily_win_rate, seg1_annual, seg2_annual, n_trades,
                           avg_trade_ret, avg_stat_ret, avg_gap_cost, avg_fund_util,
                           n_triggers, n_rejected, n_end_dropped, excess_vs_bh,
                           excess_vs_index, run_date, n_macro_blocked
                    FROM backtest_runs WHERE run_date=?
                    ORDER BY strategy_id, segment, fee""", (bt_batch_date,)).fetchall()
                eq_rows = conn.execute("""
                    SELECT e.run_id, e.trade_date, e.strategy_value
                    FROM backtest_equity e
                    JOIN backtest_runs r ON e.run_id = r.run_id
                    WHERE r.fee = 0.0 AND r.run_date=?""", (bt_batch_date,)).fetchall()
                tr_rows = conn.execute("""
                    SELECT t.strategy_id, t.segment, t.fee, t.code, t.market, t.trigger_date,
                           t.entry_date, t.entry_price, t.exit_date, t.exit_price, t.return_pct,
                           t.stat_ret, t.gap_cost, t.holding_days
                    FROM backtest_trades t
                    JOIN backtest_runs r ON t.run_id = r.run_id
                    WHERE r.run_date=? ORDER BY t.trigger_date, t.code""",
                    (bt_batch_date,)).fetchall()
                proto_row = conn.execute(
                    "SELECT result_json FROM backtest_meta WHERE meta_key='d2_protocol' "
                    "AND run_date=? ORDER BY id DESC LIMIT 1",
                    (bt_batch_date,)).fetchone()
            finally:
                conn.close()
            proto = json.loads(proto_row['result_json']) if proto_row else {}

            # ---- 索引与工具 ----
            def _bt_run(sid, seg, fee=0.0):
                return next((r for r in bt_rows if r['strategy_id'] == sid
                             and r['segment'] == seg and r['fee'] == fee), None)

            bt_strats = sorted({r['strategy_id'] for r in bt_rows if r['kind'] == 'strategy'})
            run_meta = {(r['strategy_id'], r['segment']): r for r in bt_rows if r['fee'] == 0.0}
            eq_by_run = {}
            for e in eq_rows:
                eq_by_run.setdefault(e['run_id'], []).append((e['trade_date'], e['strategy_value']))
            for k in eq_by_run:
                eq_by_run[k].sort()

            def _bt_curve(sid, seg):
                r = run_meta.get((sid, seg))
                if not r:
                    return [], []
                pts = eq_by_run.get(r['run_id'], [])
                return [p[0] for p in pts], [p[1] for p in pts]

            def _row_name(*rows):
                # sqlite3.Row 无 .get(), 统一安全取名称
                for r in rows:
                    if r is not None and r['strategy_name']:
                        return r['strategy_name']
                return ''

            def _n(v, scale=100, nd=2):
                return None if v is None else round(v * scale, nd)

            # ---- ① 查看参数区(冻结协议, 只读) ----
            kf = proto.get('knowledge_cutoff', '2026-05-01')
            fz = proto.get('freeze_date', '2026-08-31')
            st.write(f"**① 查看参数 (D2.0 冻结协议: 知识截止 {kf} / 冻结日 {fz} / K=3 / "
                     f"费率档 0·0.1%·0.3%, 选型不可交互调参)**")
            btv1, btv2, btv3 = st.columns([1.2, 1, 2.3])
            with btv1:
                bt_seg = st.selectbox(
                    "回测段", ["oos", "full"], index=0, key="qp_bt_seg",
                    format_func=lambda s: "oos · 样本外证据段" if s == "oos" else "full · 全窗口工程验证段")
            with btv2:
                bt_fee = st.selectbox(
                    "费率(单边)", [0.0, 0.001, 0.003], index=0, key="qp_bt_fee",
                    format_func=lambda f: "0%" if f == 0.0 else f"{f * 100:.1f}%")
            with btv3:
                bt_sids = st.multiselect("图表显示策略", bt_strats, default=bt_strats,
                                         key="qp_bt_strats")
            seg_row_b1 = _bt_run('B1', bt_seg)
            seg_note = (f"{bt_seg} 段: {seg_row_b1['start_date']} ~ {seg_row_b1['end_date']} · "
                        f"{seg_row_b1['n_days']}个并集交易日" if seg_row_b1 else "")
            st.caption(seg_note + (f" · 结果生成于 {seg_row_b1['run_date']}" if seg_row_b1 else ""))

            # ---- ② 净值对比图 ----
            st.write(f"**② 净值曲线 · {bt_seg} 段 (费率0, 策略 vs B1买入持有 vs B2大盘指数)**")
            bt_colors = {'S1a': '#E53935', 'S1b': '#FB8C00', 'S1c': '#43A047',
                         'S1d': '#8E24AA', 'S1e': '#00ACC1', 'S2': '#1E88E5',
                         'S2M': '#5E35B1', 'S1aM': '#00897B'}
            fig_bt = go.Figure()
            for sid in bt_sids:
                xs, ys = _bt_curve(sid, bt_seg)
                if xs:
                    fig_bt.add_trace(go.Scatter(
                        x=xs, y=ys, name=f"{sid} {_row_name(_bt_run(sid, bt_seg))}",
                        line=dict(color=bt_colors.get(sid, '#607D8B'), width=1.6)))
            for bid, dash, color in [('B1', 'dash', '#212121'), ('B2', 'dot', '#757575')]:
                xs, ys = _bt_curve(bid, bt_seg)
                if xs:
                    fig_bt.add_trace(go.Scatter(
                        x=xs, y=ys, name=f"{bid} {_row_name(_bt_run(bid, bt_seg))}",
                        line=dict(color=color, width=2.0, dash=dash)))
            fig_bt.add_hline(y=1.0, line_dash='solid', line_color='#BDBDBD', line_width=0.8)
            fig_bt.update_layout(
                yaxis_title="净值(起点=1)", template="plotly_white", height=440,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
            st.plotly_chart(fig_bt, use_container_width=True)
            if bt_seg == 'oos':
                st.caption("oos 段净值从 1 重起, B1 在截止日后首个交易日等权再建仓, B2 同窗口重新合成"
                           "——三者在同一起跑线上对比")
            else:
                st.caption("full 段为全窗口: 全部选型在该窗口选出, 收益系统性高估, 仅作机制验证")

            # ---- ③ 回撤曲线(水下曲线) ----
            st.write(f"**③ 回撤水下曲线 · {bt_seg} 段 (费率0)**")
            fig_dd = go.Figure()
            for sid in bt_sids:
                xs, ys = _bt_curve(sid, bt_seg)
                if not xs:
                    continue
                peak, dds = -1e9, []
                for v in ys:
                    peak = max(peak, v)
                    dds.append((v / peak - 1) * 100)
                fig_dd.add_trace(go.Scatter(
                    x=xs, y=dds, name=f"{sid}",
                    line=dict(color=bt_colors.get(sid, '#607D8B'), width=1.4)))
            for bid, dash, color in [('B1', 'dash', '#212121'), ('B2', 'dot', '#757575')]:
                xs, ys = _bt_curve(bid, bt_seg)
                if not xs:
                    continue
                peak, dds = -1e9, []
                for v in ys:
                    peak = max(peak, v)
                    dds.append((v / peak - 1) * 100)
                fig_dd.add_trace(go.Scatter(
                    x=xs, y=dds, name=bid, line=dict(color=color, width=1.7, dash=dash)))
            fig_dd.add_hline(y=0, line_dash='solid', line_color='#9E9E9E', line_width=0.8)
            fig_dd.update_layout(
                yaxis_title="自峰值回撤(%)", template="plotly_white", height=340,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
            st.plotly_chart(fig_dd, use_container_width=True)

            # ---- ④ 指标对比表 + 费率敏感性 ----
            st.write("**④ 策略回测对比总表 (双段, 费率0)**")
            cmp_rows = []
            for sid in bt_strats + ['B1', 'B2']:
                f_r, o_r = _bt_run(sid, 'full'), _bt_run(sid, 'oos')
                nm = _row_name(f_r, o_r) or sid
                cmp_rows.append({
                    '对象': f"{sid} {nm}",
                    'FULL收益%': _n(f_r and f_r['total_return']),
                    'OOS收益%': _n(o_r and o_r['total_return']),
                    'OOS年化%': _n(o_r and o_r['annual_return']),
                    'OOS夏普': _n(o_r and o_r['sharpe'], scale=1),
                    'OOS回撤%': _n(o_r and o_r['max_drawdown']),
                    'OOS笔数': (o_r['n_trades'] if o_r else None),
                    'OOS超额vsB1(pp)': _n(o_r and o_r['excess_vs_bh']),
                    'OOS超额vsB2(pp)': _n(o_r and o_r['excess_vs_index']),
                })
            cmp_df = pd.DataFrame(cmp_rows)
            for c in ['FULL收益%', 'OOS收益%', 'OOS年化%', 'OOS夏普', 'OOS回撤%',
                      'OOS笔数', 'OOS超额vsB1(pp)', 'OOS超额vsB2(pp)']:
                cmp_df[c] = pd.to_numeric(cmp_df[c], errors='coerce')
            st.dataframe(cmp_df, use_container_width=True, hide_index=True)
            st.caption("FULL=工程验证栏(收益仅作机制对照) · OOS=证据参考栏 · 超额为 oos 段总收益与"
                       "同窗口 B1/B2 之差(pp) · 夏普空=无交易")

            # ---- ④b 宏观 Overlay 机制对照 (模块9) ----
            _overlay_pairs = [(m, p) for m, p in (('S2M', 'S2'), ('S1aM', 'S1a'))
                              if _bt_run(m, 'full') or _bt_run(m, 'oos')]
            if _overlay_pairs:
                st.write("**④b 宏观 Overlay 机制对照 (模块9: S*M = 原版 + 宏观封锁日不开新仓)**")
                ov_rows = []
                for m_id, p_id in _overlay_pairs:
                    for seg in ('full', 'oos'):
                        mr, pr = _bt_run(m_id, seg), _bt_run(p_id, seg)
                        if not (mr and pr):
                            continue
                        ov_rows.append({
                            '对照': f"{m_id} vs {p_id}", '段': seg,
                            '原版收益%': _n(pr['total_return']),
                            'Overlay收益%': _n(mr['total_return']),
                            '收益差pp': _n((mr['total_return'] or 0)
                                           - (pr['total_return'] or 0)) if (mr['total_return'] is not None and pr['total_return'] is not None) else None,
                            '原版回撤%': _n(pr['max_drawdown']),
                            'Overlay回撤%': _n(mr['max_drawdown']),
                            '原版笔数': pr['n_trades'], 'Overlay笔数': mr['n_trades'],
                            '封锁次数': mr['n_macro_blocked'],
                        })
                if ov_rows:
                    ov_df = pd.DataFrame(ov_rows)
                    for c in ['原版收益%', 'Overlay收益%', '收益差pp', '原版回撤%',
                              'Overlay回撤%', '原版笔数', 'Overlay笔数', '封锁次数']:
                        ov_df[c] = pd.to_numeric(ov_df[c], errors='coerce')
                    st.dataframe(ov_df, use_container_width=True, hide_index=True)
                try:
                    _ov_meta = quant_data.load_fdr_view()['overlay_meta'].get('config', {})
                except Exception:
                    _ov_meta = {}
                _ov_groups = _ov_meta.get('groups', [])
                if _ov_groups:
                    st.caption("封锁家族(FDR 入选, 配置已冻结): " + "; ".join(
                        f"{g['group_id'].replace('|', ' · ')} · 封锁{g['window_days']}日"
                        for g in _ov_groups))
                st.warning("**机制对照, 非证据**: overlay 家族由含知识截止后数据的研究窗口选出(选择泄漏), "
                           "S*M 的 full/oos 结果均只演示封锁机制本身; 交易差为负≠纯封锁数, 因封锁释放槽位后"
                           "后续触发可入场(槽位级联)。唯一干净裁决 = 📡 前向跟踪页的 overlay 并行对照")

            st.write(f"**⑤ 绩效指标明细 · {bt_seg} 段 × 费率 {bt_fee * 100:.1f}%**")
            det_rows = []
            for sid in bt_strats + ['B1', 'B2']:
                r = _bt_run(sid, bt_seg, bt_fee)
                if r is None:
                    r = _bt_run(sid, bt_seg, 0.0)
                if r is None:
                    continue
                det_rows.append({
                    '对象': f"{r['strategy_id']} {r['strategy_name']}",
                    '总收益%': _n(r['total_return']),
                    '年化%': _n(r['annual_return']),
                    '波动%': _n(r['annual_vol']),
                    '夏普': _n(r['sharpe'], scale=1),
                    '回撤%': _n(r['max_drawdown']),
                    '回撤区间': f"{(r['mdd_start'] or '—')}~{(r['mdd_end'] or '—')}",
                    '日胜率%': _n(r['daily_win_rate']),
                    '笔数': r['n_trades'],
                    '均笔收益%': _n(r['avg_trade_ret']),
                    '均跳空成本pp': _n(r['avg_gap_cost']),
                    '超额vsB1(pp)': _n(r['excess_vs_bh']),
                    '超额vsB2(pp)': _n(r['excess_vs_index']),
                })
            det_df = pd.DataFrame(det_rows)
            for c in ['总收益%', '年化%', '波动%', '夏普', '回撤%', '日胜率%', '笔数',
                      '均笔收益%', '均跳空成本pp', '超额vsB1(pp)', '超额vsB2(pp)']:
                det_df[c] = pd.to_numeric(det_df[c], errors='coerce')
            st.dataframe(det_df, use_container_width=True, hide_index=True)
            st.caption("回撤区间为该段内最大回撤起止日 · 均跳空成本=回测收益−模块3统计收益的均值"
                       "(T+1开盘成交相对信号日收盘买入的执行损耗)")

            st.write("**⑥ 费率敏感性 (总收益%, 单边 0 / 0.1% / 0.3%)**")
            fig_fee = make_subplots(rows=1, cols=2, subplot_titles=("full 段", "oos 段"))
            fee_colors = {0.0: '#43A047', 0.001: '#FB8C00', 0.003: '#E53935'}
            for col, seg in enumerate(['full', 'oos'], start=1):
                for fee in [0.0, 0.001, 0.003]:
                    vals, labels = [], []
                    for sid in bt_strats:
                        r = _bt_run(sid, seg, fee)
                        vals.append(_n(r and r['total_return']))
                        labels.append(sid)
                    fig_fee.add_trace(go.Bar(
                        x=labels, y=vals, name=f"费率{fee * 100:.1f}%",
                        marker_color=fee_colors[fee], showlegend=(col == 1)), row=1, col=col)
                fig_fee.add_hline(y=0, line_dash='dot', line_color='#757575',
                                  line_width=0.8, row=1, col=col)
            fig_fee.update_layout(
                barmode='group', template="plotly_white", height=360,
                legend=dict(orientation="h", yanchor="bottom", y=1.05, x=0))
            fig_fee.update_yaxes(title_text="总收益%", row=1, col=1)
            st.plotly_chart(fig_fee, use_container_width=True)
            st.caption("费率敏感性=策略对交易成本的稳健性: 高频策略(组合S2/KDJ金叉S1d)在0.3%费率下"
                       "由正转负, 低频策略(大跌S1a/S1b)受费率影响最小")

            # ---- ⑦ 交易明细表 ----
            st.write(f"**⑦ 交易明细 · {bt_seg} 段 × 费率 {bt_fee * 100:.1f}%**")
            tv1, tv2 = st.columns([1.5, 3])
            with tv1:
                tr_sid = st.selectbox("选择策略", bt_strats, index=0, key="qp_bt_tr_sid")
            trs = [t for t in tr_rows if t['strategy_id'] == tr_sid
                   and t['segment'] == bt_seg and abs(t['fee'] - bt_fee) < 1e-9]
            if trs:
                tr_df = pd.DataFrame([{
                    '代码': t['code'], '市场': t['market'],
                    '触发日': t['trigger_date'], '买入日': t['entry_date'],
                    '买入价': t['entry_price'], '卖出日': t['exit_date'],
                    '卖出价': t['exit_price'], '收益%': _n(t['return_pct']),
                    '统计口径%': _n(t['stat_ret']), '跳空成本pp': _n(t['gap_cost']),
                    '持有天数': t['holding_days'],
                } for t in trs])
                for c in ['买入价', '卖出价', '收益%', '统计口径%', '跳空成本pp', '持有天数']:
                    tr_df[c] = pd.to_numeric(tr_df[c], errors='coerce')
                with tv2:
                    st.write("")
                    st.metric("交易笔数", len(trs), f"均收益 {tr_df['收益%'].mean():.2f}%")
                st.dataframe(tr_df, use_container_width=True, hide_index=True, height=280)
                st.caption("统计口径% = 信号日收盘买入→退出日收盘卖出(模块3口径); 跳空成本 = 收益% − 统计口径%"
                           "（负值=开盘成交劣于收盘成交的隔夜跳空损耗）")
            else:
                with tv2:
                    st.write("")
                st.info(f"{tr_sid} 在 {bt_seg} 段 × 费率{bt_fee * 100:.1f}% 下无成交交易")

            # ---- ⑧ 分段稳健性 ----
            st.write(f"**⑧ 分段稳健性 · {bt_seg} 段 (前半/后半年化收益%)**")
            sr_rows = []
            for sid in bt_strats + ['B1', 'B2']:
                r = _bt_run(sid, bt_seg)
                if r is None:
                    continue
                sr_rows.append({
                    '对象': f"{sid} {r['strategy_name']}",
                    '前半段年化%': _n(r['seg1_annual']),
                    '后半段年化%': _n(r['seg2_annual']),
                    '一致性': ('一致' if (r['seg1_annual'] is not None
                                     and r['seg2_annual'] is not None
                                     and (r['seg1_annual'] > 0) == (r['seg2_annual'] > 0))
                              else '不一致'),
                })
            if sr_rows:
                sr_df = pd.DataFrame(sr_rows)
                for c in ['前半段年化%', '后半段年化%']:
                    sr_df[c] = pd.to_numeric(sr_df[c], errors='coerce')
                st.dataframe(sr_df, use_container_width=True, hide_index=True)
            st.caption("前/后半段 = 日历中点切分的行情regime稳健性口径; 与 full/oos(知识截止日切分,"
                       "管选择偏差控制)是两种并存口径, 用途分开(见下方协议说明)")

            # ---- ⑨ 诊断信息 ----
            st.write(f"**⑨ 触发与容量诊断 · {bt_seg} 段**")
            dg_rows = []
            months = max(1.0, (seg_row_b1['n_days'] or 1) / 21.0) if seg_row_b1 else 1.0
            for sid in bt_strats:
                r = _bt_run(sid, bt_seg)
                if r is None:
                    continue
                dg_rows.append({
                    '策略': f"{sid} {r['strategy_name']}",
                    '触发数': r['n_triggers'],
                    '成交笔数': r['n_trades'],
                    '满仓拒绝': r['n_rejected'],
                    '末端放弃': r['n_end_dropped'],
                    '宏观封锁': r['n_macro_blocked'],
                    '触发密度(次/月)': round(r['n_triggers'] / months, 2),
                    '资金占用率%': _n(r['avg_fund_util']),
                    '均跳空成本pp': _n(r['avg_gap_cost']),
                })
            if dg_rows:
                dg_df = pd.DataFrame(dg_rows)
                for c in ['触发数', '成交笔数', '满仓拒绝', '末端放弃', '宏观封锁',
                          '触发密度(次/月)', '资金占用率%', '均跳空成本pp']:
                    dg_df[c] = pd.to_numeric(dg_df[c], errors='coerce')
                st.dataframe(dg_df, use_container_width=True, hide_index=True)
                st.caption("满仓拒绝 = K=3槽位全占用时被放弃的信号(容量约束); 末端放弃 = 数据末端"
                       "无法完成完整持有期的信号; 宏观封锁 = overlay 变体在封锁日放弃的开仓数"
                       "(仅 S*M 列有值); 资金占用率 = 持仓槽市值占比均值")

            # ---- ⑩ 协议与诚实性声明 + 自动结论 ----
            st.divider()
            if proto:
                st.info(f"**D2.0 协议**: 知识截止日 {proto.get('knowledge_cutoff')} · "
                        f"冻结日 {proto.get('freeze_date')}\n\n"
                        f"{proto.get('honesty_note', '')}\n\n"
                        f"{proto.get('freeze_rule', '')}")
            else:
                st.info("backtest_meta 缺少 d2_protocol 键, 建议重跑 D2 批量回测补齐协议参数")

            st.write("**⑩ 自动结论**")
            conclusions = []
            oos_strats = [(_bt_run(sid, 'oos'), sid) for sid in bt_strats]
            # overlay 变体(S*M)不参与"样本外最强"评选: 其 oos 优势可能来自选择泄漏(模块9)
            _is_overlay = lambda sid: bool(
                quant_data.BT_STRATEGIES.get(sid, {}).get('overlay'))
            oos_valid = [(r, sid) for r, sid in oos_strats
                         if r and r['excess_vs_bh'] is not None and not _is_overlay(sid)]
            if oos_valid:
                best_r, best_sid = max(oos_valid, key=lambda x: x[0]['excess_vs_bh'])
                conclusions.append(
                    f"**样本外最强**: {best_sid} {best_r['strategy_name']} — oos 总收益 "
                    f"{best_r['total_return'] * 100:+.2f}%, 超额 vs B1 "
                    f"{best_r['excess_vs_bh'] * 100:+.2f}pp, 夏普 "
                    f"{(best_r['sharpe'] if best_r['sharpe'] is not None else 0):.2f}, "
                    f"成交 {best_r['n_trades']} 笔")
                oos_fee3 = [(_bt_run(sid, 'oos', 0.003), sid) for sid in bt_strats]
                fee_robust = [f"{sid}" for r, sid in oos_fee3
                              if r and r['total_return'] is not None and r['total_return'] > 0
                              and not _is_overlay(sid)]
                fee_fragile = [f"{sid}" for r, sid in oos_fee3
                               if r and r['total_return'] is not None and r['total_return'] <= 0
                               and not _is_overlay(sid)]
                if fee_robust:
                    conclusions.append(f"**费率稳健**(0.3%费率下 oos 仍为正): {', '.join(fee_robust)}")
                if fee_fragile:
                    conclusions.append(f"**费率脆弱**(0.3%费率下 oos 转负, 实盘按0.3%双边成本将亏损): "
                                       f"{', '.join(fee_fragile)}")
            for m_id, p_id in (('S2M', 'S2'), ('S1aM', 'S1a')):
                mr, pr = _bt_run(m_id, 'oos'), _bt_run(p_id, 'oos')
                if mr and pr and mr['n_macro_blocked']:
                    d_pp = ((mr['total_return'] or 0) - (pr['total_return'] or 0)) * 100 \
                        if (mr['total_return'] is not None and pr['total_return'] is not None) else None
                    conclusions.append(
                        f"**Overlay 机制对照 {m_id}**: oos 段封锁 {mr['n_macro_blocked']} 次开仓"
                        + (f", 收益差 {d_pp:+.1f}pp" if d_pp is not None else "")
                        + " — 家族选择用了研究窗口全量数据(含 oos 期), 该差异不构成证据, "
                          "转正/淘汰由前向样本裁决(规则预注册)")
            for r, sid in oos_strats:
                if not r or not r['n_triggers']:
                    conclusions.append(f"**无法样本外验证**: {sid} oos 段 0 触发(测试窗内无该类事件/信号), "
                                       f"仅 full 段机制演示, 不构成业绩证据")
                if sid == 'S1e':
                    conclusions.append(
                        f"**观察策略 S1e**(RSI24超卖): oos 超额 "
                        f"{(r['excess_vs_bh'] or 0) * 100:+.2f}pp — RSI24 时间分割验证未达转正"
                        f"(两段超额方向分歧, 见模块3 RSI24 验证面板), 维持「观察(验证中)」身份, "
                        f"转正由 forward 样本裁决")
                if sid == 'S2' and r and r['n_triggers'] and r['n_rejected'] / r['n_triggers'] > 0.3:
                    conclusions.append(
                        f"**容量约束**: S2 组合 {r['n_triggers']} 触发中 {r['n_rejected']} 笔被 K=3 满仓拒绝"
                        f"({r['n_rejected'] / r['n_triggers'] * 100:.0f}%), 资金占用率 "
                        f"{(r['avg_fund_util'] or 0) * 100:.0f}% — 容量是组合策略收益第一决定因素")
            conclusions.append(
                "**看空警示族单独说明**: 现行唯一「有效」族(MACD/KDJ死叉, 反向口径)引擎不做空, "
                "其价值在第二阶段作为规避/减仓特征, 不在本页多头回测范围内")
            conclusions.append(
                "**诚实性**: full 段收益为样本内复述(系统性高估), oos 段为回溯性伪样本外"
                "(仅检验regime稳健性); 严格样本外 = 2026-09 起 forward test, 由看板每日数据自动累积")
            for cc in conclusions:
                st.markdown(f"- {cc}")
            st.caption("结论层如实标注样本量: 大跌真实样本 n=10、跌破下轨 n=25(去重后), "
                       "S1a~S1d 统计后盾薄弱, 双栏结论以机制验证为主, 不作特征池准入依据")

    # --- Tab 6: 信号效果 (模块3: 埋点信号历史胜率与收益统计) ---
    with quant_tab6:
        st.subheader("埋点信号效果统计")
        st.caption("观察周期: 未来1/3/5/10日 · 随机基准对比 · 入选标准: 胜率>55% 且 盈亏比>1.2 且 样本≥30"
                   " · 看空信号采用反向口径: 胜率=看跌正确率, 对照随机下跌率基准")

        # --- 投资组合标注 ---
        try:
            profiles, pf_summary = quant_data.compute_portfolio_profile()
        except Exception as e:
            profiles, pf_summary = [], {'n_stocks': 0, 'markets': {}, 'env': '—'}
        if profiles:
            mkt_str = ' / '.join(f"{k}{v}只" for k, v in pf_summary['markets'].items())
            st.info(
                f"**当前统计口径的投资组合**: 共 {pf_summary['n_stocks']} 只股票"
                f"（{mkt_str}）· 市场环境: {pf_summary['env']}"
                f"（上涨{pf_summary['n_up']} / 下跌{pf_summary['n_down']} / 震荡{pf_summary['n_flat']}）"
                f"\n\n下方全部合并口径统计 = 该组合所有股票信号汇总，非单只股票结果"
            )
            pf_df = pd.DataFrame([{
                "代码": p['code'], "市场": p['market'], "板块": p['sector'] or "—",
                "数据天数": p['days'], "区间": f"{p['start']} ~ {p['end']}",
                "累计收益%": p['total_return'], "年化%": p['annualized'],
                "年化波动%": p['volatility'], "趋势画像": p['trend'],
                "信号样本": p['signals'], "样本占比%": p['signal_share'],
            } for p in profiles])
            st.dataframe(pf_df, use_container_width=True, hide_index=True)
            trend_color_map = {
                '强势上涨': '🔴', '温和上涨': '🟠', '震荡': '🟡',
                '温和下跌': '🟢', '强势下跌': '🟢',
            }
            trend_parts = [f"{trend_color_map.get(p['trend'], '⚪')}{p['code']}({p['trend']})"
                           for p in profiles]
            st.caption("趋势画像: " + " · ".join(trend_parts))

        m3c1, m3c2 = st.columns([3, 1])
        with m3c2:
            st.write("")
            if st.button("运行信号效果分析", type="primary", key="qp_run_m3"):
                with st.spinner("正在统计全部埋点信号效果..."):
                    try:
                        st.session_state["m3_result"] = quant_data.run_module3_analysis()
                        st.session_state["m3_done"] = True
                    except Exception as e:
                        st.error(f"分析失败: {e}")
        with m3c1:
            st.write("")

        if "m3_result" not in st.session_state:
            # 尝试读取上次入库的报告
            conn = quant_data.get_db()
            try:
                has_report = conn.execute(
                    "SELECT COUNT(*) FROM signal_effect_report").fetchone()[0]
            except Exception:
                has_report = 0
            finally:
                conn.close()
            if has_report:
                st.info("检测到历史报告，点击上方按钮重新计算最新结果")
            else:
                st.info("请点击上方「运行信号效果分析」按钮，统计全部被动信号与主动事件的历史胜率")
        else:

            m3 = st.session_state["m3_result"]
            baselines = m3["baselines"]
            rev_baselines = m3.get("rev_baselines") or quant_data.compute_random_baselines(reverse=True)

            # 随机基准
            st.write("**随机基准（全量交易日买入持有N日）**")
            bl_df = pd.DataFrame([{
                "持有周期": f"{n}日",
                "触发数": baselines[n]["triggers"],
                "随机上涨胜率%": baselines[n]["win_rate"],
                "随机下跌率%": rev_baselines[n]["win_rate"],
                "平均收益%": baselines[n]["avg_return"],
                "盈亏比": baselines[n]["profit_loss_ratio"] or "—",
                "最大盈利%": baselines[n]["max_win"],
                "最大亏损%": baselines[n]["max_loss"],
            } for n in quant_data.HOLD_PERIODS])
            st.dataframe(bl_df, use_container_width=True, hide_index=True)
            st.caption("看多信号对照「随机上涨胜率」，看空信号（反向口径）对照「随机下跌率」"
                       "—— 同一行情下两者之和≈100%，分别衡量两类方向的随机水平")

            # 有效信号池
            st.divider()
            st.write("**核心有效信号池**")
            pool = m3["pool"]
            pool_df = pd.DataFrame([{
                "信号": f"{p['signal_type']}|{p['signal_subtype']}",
                "方向": p["direction"],
                "口径": "反向" if p.get("eval_mode") == "reverse" else "正向",
                "状态": p["status"],
                "优势周期": p.get("best_period", "—"),
                "胜率%": p.get("best_win_rate", "—"),
                "盈亏比": p.get("best_pl_ratio", "—"),
                "平均收益%": p.get("best_avg_return", "—"),
                "超额胜率pp": p.get("excess_win_rate", "—"),
                "超额收益%": p.get("excess_return", "—"),
                "样本量": p["total_signals"],
            } for p in pool])
            st.dataframe(pool_df, use_container_width=True, hide_index=True)
            st.caption("反向口径行: 胜率=看跌正确率, 平均收益=平均跌幅, 盈亏比=平均跌幅/平均反弹幅度 · "
                       "「观察(验证中)」= RSI24 并行观察信号: 未通过时间分割预注册规则前不转正 · "
                       "「有效(时间分割通过)」/「淘汰(时间分割未过)」由模块8预注册规则裁决(见下方)")

            # RSI24 时间分割验证（模块8 预注册规则）
            v = m3.get("rsi24_verdict") or {}
            if v.get("signals"):
                st.write("**RSI24 时间分割验证（模块8 预注册规则，看结果前冻结）**")
                st.caption(v.get("rule", ""))
                v_rows = []
                for sig, e in v["signals"].items():
                    for seg_label, seg in (("段A 开发70%", e.get("seg_a")),
                                           ("段B 验证30%", e.get("seg_b"))):
                        if seg:
                            v_rows.append({
                                "信号": sig, "分段": seg_label,
                                "触发数": seg.get("triggers"),
                                "胜率%": seg.get("win_rate"),
                                "基准%": seg.get("baseline_win_rate"),
                                "超额胜率pp": seg.get("excess_win_rate"),
                                "平均收益%": seg.get("avg_return"),
                                "超额收益%": seg.get("excess_return"),
                            })
                if v_rows:
                    st.dataframe(pd.DataFrame(v_rows), use_container_width=True, hide_index=True)
                verdicts = []
                for sig, e in v["signals"].items():
                    icon = {"转正": "🟢", "淘汰": "🔴"}.get(e.get("verdict"), "🟡")
                    split_d = e.get("split_date") or "—"
                    verdicts.append(f"{icon} **{sig}**: {e.get('verdict')}（分割点 {split_d}）— "
                                    f"{e.get('verdict_reason', '')}")
                st.markdown("  \n".join(verdicts))
                st.caption("预注册规则一经写入不得依结果修改（与回测冻结协议同源）· "
                           "转正条件: 两段各≥20触发且超额胜率/超额收益均为正 · "
                           "淘汰条件: 任一段超额胜率≤-5pp或超额收益≤-3pp")

            # 被动信号明细
            st.divider()
            st.write("**被动信号统计明细（按信号类型×方向×持有周期）**")
            by_cal = m3.get("passive_by_caliber") or {"合并": m3["passive"]}
            cal_options = [c for c in ("合并", "replay", "live") if c in by_cal and by_cal[c]]
            cal_names = {"合并": "合并（replay+live，资格判定口径）",
                         "replay": "仅回放 replay（2021-01 起）",
                         "live": "仅实采 live（2025-07-29 起）"}
            passive_sel = m3["passive"]
            if len(cal_options) > 1:
                cal_sel = st.radio("统计口径（模块8 双来源分离）", cal_options,
                                   horizontal=True, format_func=lambda c: cal_names.get(c, c),
                                   key="m3_caliber")
                passive_sel = by_cal.get(cal_sel, m3["passive"])
                st.caption("有效池资格判定以**合并**口径为准（样本最大）；replay/live 口径仅供来源对照，"
                           "若两口径结论背离，以 live 定观察方向、合并定统计资格")
            sig_rows = []
            for r in passive_sel:
                for n, st_ in r["stats"].items():
                    sig_rows.append({
                        "信号": f"{r['signal_type']}|{r['signal_subtype']}",
                        "方向": r["direction"],
                        "口径": "反向" if st_.get("eval_mode") == "reverse" else "正向",
                        "周期": f"{n}日",
                        "触发数": st_["triggers"],
                        "达标数": st_["win_count"],
                        "胜率%": st_["win_rate"],
                        "基准胜率%": st_.get("baseline_win_rate", "—"),
                        "超额胜率pp": st_.get("excess_win_rate", "—"),
                        "平均收益%": st_["avg_return"],
                        "超额收益%": st_.get("excess_return", "—"),
                        "盈利均值%": st_["avg_win"],
                        "亏损均值%": st_["avg_loss"],
                        "盈亏比": st_["profit_loss_ratio"] or "—",
                        "最大盈亏%": f"{st_['max_win']:.2f} / {st_['max_loss']:.2f}",
                    })
            if sig_rows:
                st.dataframe(pd.DataFrame(sig_rows), use_container_width=True, hide_index=True,
                             height=400)
                st.caption("正向口径: 达标数=上涨次数, 平均收益=平均涨幅 · "
                           "反向口径(看空): 达标数=下跌次数, 胜率=看跌正确率, "
                           "平均收益=平均跌幅, 基准=随机下跌率, 盈利均值=平均跌幅, 亏损均值=平均反弹")

                # 胜率对比图
                st.write("**各信号胜率 vs 随机基准**")
                fig_w = go.Figure()
                for r in passive_sel:
                    label = f"{r['signal_type']}|{r['signal_subtype']}|{r['direction']}"
                    periods = sorted(r["stats"].keys())
                    fig_w.add_trace(go.Scatter(
                        x=[f"{n}日" for n in periods],
                        y=[r["stats"][n]["win_rate"] for n in periods],
                        mode="lines+markers", name=label[:28]
                    ))
                fig_w.add_trace(go.Scatter(
                    x=[f"{n}日" for n in quant_data.HOLD_PERIODS],
                    y=[baselines[n]["win_rate"] for n in quant_data.HOLD_PERIODS],
                    mode="lines+markers", name="随机上涨胜率基准",
                    line=dict(color="#555555", width=3, dash="dash")
                ))
                fig_w.add_trace(go.Scatter(
                    x=[f"{n}日" for n in quant_data.HOLD_PERIODS],
                    y=[rev_baselines[n]["win_rate"] for n in quant_data.HOLD_PERIODS],
                    mode="lines+markers", name="随机下跌率基准(看空对照)",
                    line=dict(color="#B8860B", width=3, dash="dash")
                ))
                fig_w.add_hline(y=55, line_dash="dot", line_color="#E8463A", line_width=1,
                                annotation_text="55%入选线")
                fig_w.update_layout(height=430, template="plotly_white",
                                    yaxis_title="胜率%", margin=dict(t=30, b=20))
                st.plotly_chart(fig_w, use_container_width=True)
                st.caption("看多(bullish)曲线对照灰色「随机上涨胜率基准」; "
                           "看空(bearish)曲线为反向口径(看跌正确率)，对照金色「随机下跌率基准」; "
                           "红色虚线为55%入选线")

                # 超额收益图
                st.write("**各信号超额收益（信号平均收益 − 随机基准）**")
                fig_e = go.Figure()
                for r in passive_sel:
                    label = f"{r['signal_type']}|{r['signal_subtype']}|{r['direction']}"
                    periods = sorted(r["stats"].keys())
                    fig_e.add_trace(go.Bar(
                        x=[f"{n}日" for n in periods],
                        y=[r["stats"][n].get("excess_return", 0) for n in periods],
                        name=label[:28]
                    ))
                fig_e.add_hline(y=0, line_color="#888", line_width=1)
                fig_e.update_layout(height=430, template="plotly_white", barmode="group",
                                    yaxis_title="超额收益%", margin=dict(t=30, b=20))
                st.plotly_chart(fig_e, use_container_width=True)
                st.caption("看空(bearish)信号为反向口径: 超额收益 = 平均跌幅 − 随机平均跌幅, "
                           "正值表示看空信号触发后跌幅大于随机水平(预警有效)")

            # 按股票分组统计
            st.divider()
            st.write("**按股票分组统计（同类信号在不同股票上的表现差异）**")
            try:
                per_stock, stock_baselines, stock_rev_baselines = quant_data.compute_per_stock_signal_stats()
            except Exception as e:
                per_stock, stock_baselines, stock_rev_baselines = {}, {}, {}
                st.warning(f"按股票分组统计失败: {e}")

            if per_stock:
                pg1, pg2 = st.columns([2, 1])
                with pg2:
                    sel_stock = st.selectbox(
                        "选择股票查看明细", list(per_stock.keys()), key="qp_m3_stock"
                    )
                with pg1:
                    st.write("")

                # 该股随机基准
                if sel_stock in stock_baselines:
                    sb = stock_baselines[sel_stock]
                    srb = stock_rev_baselines.get(sel_stock, {})
                    sb_df = pd.DataFrame([{
                        "周期": f"{n}日",
                        "该股随机胜率%": sb[n]["win_rate"] if sb.get(n) else "—",
                        "该股随机下跌率%": srb[n]["win_rate"] if srb.get(n) else "—",
                        "该股随机均收%": sb[n]["avg_return"] if sb.get(n) else "—",
                    } for n in quant_data.HOLD_PERIODS])
                    st.caption(f"{sel_stock} 自身随机基准（该股全部交易日买入持有）:")
                    st.dataframe(sb_df, use_container_width=True, hide_index=True)

                ps_rows = []
                for (sig_type, subtype, direction), stats in per_stock.get(sel_stock, {}).items():
                    for n, st_ in sorted(stats.items()):
                        ps_rows.append({
                            "信号": f"{sig_type}|{subtype}",
                            "方向": direction,
                            "口径": "反向" if st_.get("eval_mode") == "reverse" else "正向",
                            "周期": f"{n}日",
                            "触发数": st_["triggers"],
                            "胜率%": st_["win_rate"],
                            "该股基准%": st_.get("baseline_win_rate", "—"),
                            "超额胜率pp": st_.get("excess_win_rate", "—"),
                            "平均收益%": st_["avg_return"],
                            "超额收益%": st_.get("excess_return", "—"),
                            "盈亏比": st_["profit_loss_ratio"] or "—",
                        })
                if ps_rows:
                    st.dataframe(pd.DataFrame(ps_rows), use_container_width=True,
                                 hide_index=True, height=350)
                else:
                    st.info("该股票暂无信号统计")

                # 横向对比图: 各股票同类信号胜率 (选信号类型)
                all_sig_keys = set()
                for code, groups in per_stock.items():
                    all_sig_keys.update(groups.keys())
                sig_labels = {k: f"{k[0]}|{k[1]}|{k[2]}" for k in all_sig_keys}

                hc1, hc2 = st.columns([2, 1])
                with hc2:
                    sel_sig = st.selectbox(
                        "选择信号横向对比", sorted(sig_labels.values()), key="qp_m3_sig"
                    )
                    cmp_period = st.selectbox(
                        "对比周期", [1, 3, 5, 10], index=1, key="qp_m3_cmp_period"
                    )
                with hc1:
                    st.write("")

                sel_key = next((k for k, v in sig_labels.items() if v == sel_sig), None)
                if sel_key:
                    # 看空信号为反向口径, 对照该股随机下跌率基准
                    sel_reverse = (sel_key[2] == 'bearish')
                    fig_cmp = go.Figure()
                    codes_x, wr_y, bl_y, n_labels = [], [], [], []
                    for code in sorted(per_stock.keys()):
                        stt = per_stock[code].get(sel_key, {}).get(cmp_period)
                        bl_src = (stock_rev_baselines if sel_reverse else stock_baselines)
                        bl = bl_src.get(code, {}).get(cmp_period)
                        if stt:
                            codes_x.append(code)
                            wr_y.append(stt["win_rate"])
                            bl_y.append(bl["win_rate"] if bl else None)
                            n_labels.append(f"n={stt['triggers']}")
                    if codes_x:
                        fig_cmp.add_trace(go.Bar(
                            x=codes_x, y=wr_y,
                            name="看跌正确率" if sel_reverse else "信号胜率",
                            marker_color="#4f46e5",
                            text=n_labels, textposition="outside",
                        ))
                        bl_valid = [b for b in bl_y if b is not None]
                        if bl_valid:
                            fig_cmp.add_trace(go.Scatter(
                                x=codes_x, y=bl_y,
                                name="该股随机下跌率基准" if sel_reverse else "该股随机基准",
                                mode="lines+markers",
                                line=dict(color="#555555", width=2, dash="dash")
                            ))
                        fig_cmp.add_hline(y=55, line_dash="dot", line_color="#E8463A",
                                          line_width=1, annotation_text="55%线")
                        fig_cmp.update_layout(
                            height=360, template="plotly_white", barmode="group",
                            yaxis_title="胜率%", title=f"{sel_sig} · {cmp_period}日周期"
                            + ("（反向口径: 看跌正确率）" if sel_reverse else ""),
                            margin=dict(t=50, b=20)
                        )
                        st.plotly_chart(fig_cmp, use_container_width=True)

            # --- 股票表现差异分析 ---
            st.divider()
            st.write("**股票表现差异分析**")
            st.caption("差异分析基于: ① 各股趋势画像 ② 信号适应性(每股加权平均超额胜率) ③ 信号一致性(同类信号在各股是否同向有效)"
                       " · 看空信号的超额按反向口径计算(看跌超额正确率), 与看多信号同向可比")
            try:
                diff = quant_data.compute_stock_diff_analysis(per_stock, stock_baselines)
            except Exception as e:
                diff = {'stock_adapt': {}, 'consistency': []}
                st.warning(f"差异分析失败: {e}")

            if diff['stock_adapt']:
                # 信号适应性: 各股加权平均超额胜率
                adapt_rows = []
                for code in sorted(diff['stock_adapt'].keys()):
                    row = {"代码": code}
                    for n in quant_data.HOLD_PERIODS:
                        a = diff['stock_adapt'][code].get(n)
                        row[f"{n}日超额pp"] = a['avg_excess'] if a else None
                        row[f"{n}日样本"] = a['triggers'] if a else 0
                    adapt_rows.append(row)
                adapt_df = pd.DataFrame(adapt_rows)
                st.write("① 信号适应性 — 每股全部信号的加权平均超额胜率（正值=信号整体跑赢该股随机基准）")
                st.dataframe(adapt_df, use_container_width=True, hide_index=True)

                fig_ad = go.Figure()
                for n in quant_data.HOLD_PERIODS:
                    codes_x, y_vals = [], []
                    for code in sorted(diff['stock_adapt'].keys()):
                        a = diff['stock_adapt'][code].get(n)
                        if a:
                            codes_x.append(code)
                            y_vals.append(a['avg_excess'])
                    if codes_x:
                        fig_ad.add_trace(go.Bar(x=codes_x, y=y_vals, name=f"{n}日"))
                fig_ad.add_hline(y=0, line_color="#888", line_width=1)
                fig_ad.update_layout(height=340, template="plotly_white", barmode="group",
                                     yaxis_title="加权平均超额胜率(pp)",
                                     title="各股信号适应性对比", margin=dict(t=50, b=20))
                st.plotly_chart(fig_ad, use_container_width=True)

                # 趋势对照
                if profiles:
                    st.caption("趋势对照: " + " · ".join(
                        f"{p['code']} 年化{p['annualized']}%({p['trend']})" for p in profiles))

            if diff['consistency']:
                st.write("② 信号一致性 — 同类信号在各股的3日超额胜率方向（≥2只股票才参与判断）")
                cons_rows = []
                for c in diff['consistency']:
                    cons_rows.append({
                        "信号": c['signal'],
                        "类型": c['kind'],
                        "有效股票数": f"{c['positive']}/{c['total']}",
                        "最佳": f"{c['best_stock']['code']}(+{c['best_stock']['excess']}pp)",
                        "最差": f"{c['worst_stock']['code']}({c['worst_stock']['excess']}pp)",
                    })
                cons_df = pd.DataFrame(cons_rows)
                st.dataframe(cons_df, use_container_width=True, hide_index=True, height=300)

                # 热力图: 信号×股票 超额胜率
                sig_names = [c['signal'] for c in diff['consistency']]
                all_codes = sorted(per_stock.keys())
                z_matrix, z_text = [], []
                for c in diff['consistency']:
                    dmap = {r['code']: r['excess'] for r in c['detail']}
                    z_matrix.append([dmap.get(code, None) for code in all_codes])
                    z_text.append([f"{dmap.get(code, '—')}" if dmap.get(code) is not None else '—'
                                   for code in all_codes])
                fig_hm = go.Figure(go.Heatmap(
                    z=z_matrix, x=all_codes, y=sig_names, text=z_text,
                    texttemplate="%{text}", colorscale="RdYlGn", zmid=0,
                    colorbar_title="超额胜率pp",
                ))
                fig_hm.update_layout(height=420, template="plotly_white",
                                     title="信号 × 股票 超额胜率热力图（3日周期，绿=正 红=负）",
                                     margin=dict(t=50, b=20))
                st.plotly_chart(fig_hm, use_container_width=True)

                # 自动结论
                universal_ok = [c for c in diff['consistency'] if c['kind'] == '普适有效']
                universal_bad = [c for c in diff['consistency'] if c['kind'] == '普适失效']
                dependent = [c for c in diff['consistency'] if c['kind'] == '个股依赖']
                adapt_best, adapt_worst = None, None
                if diff['stock_adapt']:
                    adapt_3d = {code: v.get(3, {}).get('avg_excess')
                                for code, v in diff['stock_adapt'].items() if v.get(3)}
                    if adapt_3d:
                        adapt_best = max(adapt_3d, key=adapt_3d.get)
                        adapt_worst = min(adapt_3d, key=adapt_3d.get)

                conclusions = []
                if adapt_best is not None:
                    conclusions.append(
                        f"信号适应性最高: **{adapt_best}**（3日加权超额 "
                        f"+{diff['stock_adapt'][adapt_best][3]['avg_excess']}pp）— 信号在该股整体有效"
                    )
                if adapt_worst is not None and adapt_worst != adapt_best:
                    conclusions.append(
                        f"信号适应性最低: **{adapt_worst}**（3日加权超额 "
                        f"{diff['stock_adapt'][adapt_worst][3]['avg_excess']}pp）— 该股不适合照搬信号"
                    )
                if universal_ok:
                    names = '、'.join(c['signal'] for c in universal_ok[:3])
                    conclusions.append(f"普适有效信号（全部股票超额为正）: {names}")
                if universal_bad:
                    names = '、'.join(c['signal'] for c in universal_bad[:3])
                    conclusions.append(f"普适失效信号（全部股票超额为负，建议弃用）: {names}")
                if dependent:
                    spread = max(
                        (c['best_stock']['excess'] - c['worst_stock']['excess'])
                        for c in dependent)
                    conclusions.append(
                        f"个股依赖信号 {len(dependent)} 类，最大跨度达 {spread:.1f}pp — "
                        f"同一信号在不同股票效果相反，须结合个股趋势选用"
                    )
                if profiles:
                    up_stocks = [p['code'] for p in profiles if '上涨' in p['trend']]
                    down_stocks = [p['code'] for p in profiles if '下跌' in p['trend']]
                    if up_stocks and down_stocks:
                        conclusions.append(
                            f"趋势归因: {('/'.join(up_stocks))} 处上涨趋势（顺势信号占优），"
                            f"{('/'.join(down_stocks))} 处下跌趋势（抄底类信号普遍失效）"
                        )
                if conclusions:
                    st.write("③ 自动结论")
                    for cc in conclusions:
                        st.markdown(f"- {cc}")

            # 主动事件统计
            st.divider()
            st.write("**主动事件统计（类型×方向×影响等级）**")
            evt_rows = []
            for r in m3["events"]["by_dimension"]:
                for n, st_ in r["stats"].items():
                    evt_rows.append({
                        "事件类型": r["key"][0], "方向": r["key"][1], "影响等级": r["key"][2],
                        "口径": "反向" if st_.get("eval_mode") == "reverse" else "正向",
                        "周期": f"{n}日",
                        "触发数": st_["triggers"],
                        "胜率%": st_["win_rate"],
                        "超额胜率pp": st_.get("excess_win_rate", "—"),
                        "平均收益%": st_["avg_return"],
                        "超额收益%": st_.get("excess_return", "—"),
                        "盈亏比": st_["profit_loss_ratio"] or "—",
                    })
            if evt_rows:
                st.dataframe(pd.DataFrame(evt_rows), use_container_width=True, hide_index=True)
                st.caption("利空(bearish)事件为反向口径: 胜率=事件后下跌占比, 平均收益=平均跌幅, "
                           "超额为正表示利空事件确实伴随超常下跌")
            else:
                st.info("暂无主动事件数据")

            st.write("**事件发布时点效果（盘前/盘中/盘后）**")
            tm_rows = []
            for r in m3["events"]["by_time"]:
                for n, st_ in r["stats"].items():
                    tm_rows.append({
                        "事件类型": r["key"][0], "发布时点": r["key"][1],
                        "周期": f"{n}日",
                        "触发数": st_["triggers"],
                        "胜率%": st_["win_rate"],
                        "平均收益%": st_["avg_return"],
                        "超额收益%": st_.get("excess_return", "—"),
                    })
            if tm_rows:
                st.dataframe(pd.DataFrame(tm_rows), use_container_width=True, hide_index=True)
                st.caption("时点分组混合利好/利空事件, 统一为正向口径(上涨胜率), "
                           "反映不同发布时点的市场整体消化速度")
            else:
                st.info("暂无事件时点数据")

            st.caption("结果已同步入库: signal_effect_report 表（可用 DB Browser 查看《埋点信号效果总表》）")

    # --- Tab 9: 前向跟踪 (模块7) ---
    with quant_tab9:
        st.subheader("📡 Forward Test 前向跟踪")
        st.caption(
            f"前向窗口起点 {quant_data.FORWARD_START}（冻结回测批 2026-08-27 之后首个交易日）· "
            "追加式入库 · 裁决规则预注册 · 净值从 1 重起，与回测 oos 段严格分离")

        # ---------- ① 每日管道 ----------
        st.markdown("**① 每日管道**（收盘后运行一次 · 9 步：孤儿股补课 → 行情 → 指数 → 指标 → "
                    "信号扫描 → 宽表 → 宏观日历重采(近3天) → 封锁日台账追加(守卫=完整性截断日) → "
                    "前向记录 → 新鲜度）")
        pc1, pc2 = st.columns([1, 2])
        with pc1:
            if st.button("▶️ 运行每日管道", type="primary", key="m7_pipe_run"):
                with st.status("每日管道运行中...", expanded=True) as m7_status:
                    def _m7_log(msg):
                        st.write(str(msg))
                    try:
                        rep = quant_data.run_daily_pipeline(log=_m7_log)
                        n_err = sum(1 for v in rep['steps'].values()
                                    if isinstance(v, dict) and 'error' in v)
                        if n_err:
                            m7_status.update(label=f"⚠️ 管道完成（{n_err} 个步骤失败，详见日志）",
                                             state="error", expanded=True)
                            for k, v in rep['steps'].items():
                                if isinstance(v, dict) and 'error' in v:
                                    st.error(f"步骤 {k}: {v['error']}")
                        else:
                            fr_al = rep.get('freshness', {}).get('alerts', [])
                            lag_txt = ("，无新鲜度告警" if not fr_al else
                                       "，告警: " + ", ".join(
                                           f"{s['code']} 落后{s['lag_trading_days']}日" for s in fr_al))
                            m7_status.update(label=f"✅ 每日管道完成{lag_txt}",
                                             state="complete", expanded=False)
                            st.rerun()
                    except Exception as e:
                        m7_status.update(label="❌ 管道异常终止", state="error", expanded=True)
                        st.exception(e)
        with pc2:
            _last = quant_data.get_pipeline_last_run()
            if _last:
                _steps_ok = [k for k, v in _last.get('steps', {}).items() if v == 'OK']
                _steps_err = [k for k, v in _last.get('steps', {}).items() if v != 'OK']
                st.markdown(
                    f"最近运行：**{_last.get('generated_at', '—')}** · "
                    f"成功 {len(_steps_ok)}/{len(_last.get('steps', {}))} 步")
                if _steps_err:
                    st.markdown("失败步骤：" + "、".join(
                        f"`{k}`({v})" for k, v in _last.get('steps', {}).items() if v != 'OK'))
            else:
                st.info("尚未运行过每日管道（点击左侧按钮或运行 daily_pipeline.py）")

        # 新鲜度表
        st.markdown("**② 数据新鲜度**（落后交易日数 > 阈值即红色告警；指数/宏观滞后仅展示）")
        try:
            _fr = quant_data.get_data_freshness()
            _fr_rows = [{
                "代码": s['code'], "名称": s['name'], "市场": s['market'],
                "行情最新": s['quote_latest'] or "—",
                "落后交易日": s['lag_trading_days'],
                "信号最新": s['signal_latest'] or "—",
                "状态": "🔴 告警" if s['alert'] else "🟢 正常",
            } for s in _fr['stocks']]
            if _fr_rows:
                _fr_df = pd.DataFrame(_fr_rows)
                st.dataframe(_fr_df.style.map(
                    lambda v: "color:red;font-weight:bold" if isinstance(v, int) and v > _fr['threshold'] else "",
                    subset=["落后交易日"]), use_container_width=True, hide_index=True)
            st.caption(" · ".join(
                f"{m}指数至 {d}" for m, d in _fr['benchmark'].items())
                + f" · 宏观日历至 {_fr['macro_latest'] or '—'}"
                + f" · 告警阈值: 落后 > {_fr['threshold']} 个交易日")
        except Exception as e:
            st.error(f"新鲜度读取失败: {e}")

        # ---------- ③ 裁决面板 ----------
        st.markdown("**③ 观察名单裁决**（规则已预注册于 forward_meta.protocol，不随结果调整）")
        try:
            _wl = quant_data.evaluate_watchlist()
            st.dataframe(pd.DataFrame([{
                "策略": w['strategy_id'], "名称": w['name'], "说明": w['note'],
                "样本进度": w['progress'],
                "前向胜率": f"{w['win_rate']:.1%}" if w['win_rate'] is not None else "—",
                "超额(vs B1)": f"{w['excess_vs_b1']:+.2%}" if w['excess_vs_b1'] is not None else "—",
                "当前裁决": w['verdict'],
            } for w in _wl]), use_container_width=True, hide_index=True)
        except Exception as e:
            st.error(f"裁决面板读取失败: {e}")

        # ---------- ③b Overlay 前向裁决 (模块9) ----------
        st.markdown("**③b 宏观 Overlay 裁决**（S*M = 原版 + 宏观封锁日不开新仓；"
                    "规则预注册于 forward_meta.overlay_protocol，回测段仅为机制对照）")
        try:
            _ov = quant_data.evaluate_overlay()
            st.dataframe(pd.DataFrame([{
                "策略": o['strategy_id'], "名称": o['name'],
                "对照原版": o['parent_id'],
                "样本进度": o['progress'],
                "宏观封锁": o['macro_blocked'],
                "超额vs B1 (S*M)": f"{o['excess_vs_b1']:+.2%}" if o['excess_vs_b1'] is not None else "—",
                "超额vs B1 (原版)": f"{o['parent_excess_vs_b1']:+.2%}" if o['parent_excess_vs_b1'] is not None else "—",
                "回撤(S*M)": f"{o['max_drawdown']:.1%}" if o['max_drawdown'] is not None else "—",
                "回撤(原版)": f"{o['parent_max_drawdown']:.1%}" if o['parent_max_drawdown'] is not None else "—",
                "当前裁决": o['verdict'],
            } for o in _ov]), use_container_width=True, hide_index=True)
            st.caption("淘汰 = overlay 拖累收益(超额低于原版)；保留(转正) = 收益不降且回撤收窄；"
                       "其余继续观察 · 各需完成 ≥20 笔交易后裁决 · 台账追加式保证重放确定性")
        except Exception as e:
            st.error(f"Overlay 裁决面板读取失败: {e}")

        # ---------- ③c 池构成与前向资格 (模块10) ----------
        st.markdown("**③c 池构成与前向资格**（模块10：入池即引导 · 资格区间过滤 · 离池快照冻结；"
                    "规则预注册于 forward_meta.pool_protocol）")
        try:
            _mv = quant_data.get_pool_membership_view()
            _fb = set(_mv['first_batch'])
            _mem_rows = [{
                "代码": m['code'],
                "首次资格日": m['join_eff'],
                "B1 首批成员": "✅" if m['code'] in _fb else "—",
                "状态": "在池（开放区间）" if m['in_pool'] else "已离池（区间关闭，快照冻结）",
            } for m in _mv['members']]
            if _mem_rows:
                st.dataframe(pd.DataFrame(_mem_rows), use_container_width=True, hide_index=True)
            else:
                st.info("暂无开放资格区间的股票")
            _ev_rows = [{
                "代码": e['code'], "市场": e['market'] or "—",
                "事件": "入池/再激活" if e['event'] == 'join' else "离池/停用",
                "生效日": e['eff_date'], "来源": e['source'] or "—",
                "记录时间": e['run_ts'] or "—",
            } for e in _mv['events']]
            with st.expander(f"池构成事件时间线（{len(_ev_rows)} 条，追加式永不改写）", expanded=False):
                if _ev_rows:
                    st.dataframe(pd.DataFrame(_ev_rows), use_container_width=True, hide_index=True)
                else:
                    st.info("暂无池构成事件")
            if _mv.get('pool_protocol'):
                with st.expander("池构成协议（预注册，冻结）", expanded=False):
                    st.json(_mv['pool_protocol'])
            st.caption(
                f"B1 基准仅由首批成员（首次资格日 ≤ 前向起点 {_mv['forward_start']}，"
                f"当前 {_mv['first_batch'] or '—'}）构成，后加入股票永不进入 B1；"
                "新股回填的历史信号不追溯计入前向，前向样本自入池日起积累")
        except Exception as e:
            st.error(f"池构成面板读取失败: {e}")

        # ---------- 前向视图 ----------
        try:
            _view = quant_data.get_forward_view()
        except Exception as e:
            st.error(f"前向视图读取失败: {e}")
            _view = {'equity': {}, 'trades': [], 'status': {}, 'protocol': None}

        # ④ 净值曲线
        st.markdown("**④ 前向净值曲线**（策略 vs B1 等权买入持有 / B2 指数基准）")
        if _view['equity']:
            fig_fwd = go.Figure()
            _palette = ["#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A",
                        "#19D3F3", "#FF6692", "#B6E880"]
            for _i, (_sid, _eq) in enumerate(_view['equity'].items()):
                if not _eq['dates']:
                    continue
                _is_base = _sid in ('B1', 'B2')
                fig_fwd.add_trace(go.Scatter(
                    x=_eq['dates'], y=_eq['nav'], mode='lines',
                    name=_sid, line=dict(
                        width=3 if _is_base else 2,
                        dash='dash' if _is_base else 'solid',
                        color=('#7F7F7F' if _sid == 'B2' else '#FF6692') if _is_base
                        else _palette[_i % len(_palette)])))
            fig_fwd.update_layout(
                height=380, margin=dict(l=10, r=10, t=30, b=10),
                yaxis_title="净值", legend=dict(orientation="h", y=1.12),
                hovermode="x unified")
            st.plotly_chart(fig_fwd, use_container_width=True)
        else:
            st.info("前向净值暂无数据（运行每日管道后生成）")

        # ⑤ 策略状态快照
        st.markdown("**⑤ 策略前向状态快照**（来自最近一次前向步进 forward_meta.status）")
        _sts = _view.get('status') or {}
        if _sts.get('strategies'):
            _st_rows = []
            for _sid, s in _sts['strategies'].items():
                _st_rows.append({
                    "策略": _sid, "名称": s['name'],
                    "身份": ("🛡 宏观封锁" if s.get('overlay')
                             else ("👀 观察" if s.get('observation') else "正式")),
                    "触发": s['n_triggers'], "完成交易": s['n_trades'],
                    "拒单": s['rejected'], "尾仓未平": s['end_dropped'],
                    "宏观封锁": s.get('macro_blocked', 0),
                    "总收益": f"{s['total_return']:+.2%}" if s.get('total_return') is not None else "—",
                    "胜率": f"{s['win_rate']:.1%}" if s.get('win_rate') is not None else "—",
                    "Sharpe": f"{s['sharpe']:.2f}" if s.get('sharpe') is not None else "—",
                    "最大回撤": f"{s['max_drawdown']:.1%}" if s.get('max_drawdown') is not None else "—",
                    "超额(vs B1)": f"{s['excess_vs_b1']:+.2%}" if s.get('excess_vs_b1') is not None else "—",
                    "守卫不一致": s.get('n_mismatch', 0),
                })
            st.dataframe(pd.DataFrame(_st_rows), use_container_width=True, hide_index=True)
            st.caption(
                f"窗口 {_sts.get('window', ['—', '—'])[0]} ~ {_sts.get('window', ['—', '—'])[-1]} · "
                f"{_sts.get('n_days', 0)} 个交易日 · "
                f"B1 {_sts.get('b1_total', 0):+.2%} / B2 {_sts.get('b2_total', 0):+.2%} · "
                f"快照生成于 {_sts.get('generated_at', '—')}"
                if _sts.get('b1_total') is not None else
                f"窗口 {_sts.get('window', ['—', '—'])[0]} ~ {_sts.get('window', ['—', '—'])[-1]} · 快照 {_sts.get('generated_at', '—')}")
            if _sts.get('mismatches'):
                st.warning(f"守卫告警: {len(_sts['mismatches'])} 条已入库 NAV 与重算不一致（未改写，"
                           "多为行情源修订所致，需人工核查）")
        else:
            st.info("暂无前向状态快照")

        # ⑥ 交易明细
        st.markdown("**⑥ 前向交易明细**（最近 300 笔，追加式；尚未到持有期末的交易不入库）")
        if _view['trades']:
            _tdf = pd.DataFrame(_view['trades'])
            _tdf = _tdf.rename(columns={
                'strategy_id': '策略', 'code': '代码', 'market': '市场',
                'trigger_date': '触发日', 'entry_date': '买入日', 'entry_price': '买入价',
                'exit_date': '卖出日', 'exit_price': '卖出价', 'return_pct': '收益%',
                'holding_days': '持有天数'})
            st.dataframe(_tdf, use_container_width=True, hide_index=True)
        else:
            st.info("前向窗口暂无已完成交易")

        # ⑦ 协议
        with st.expander("**⑦ 前向协议（预注册，冻结）**", expanded=False):
            if _view.get('protocol'):
                st.json(_view['protocol'])
            else:
                st.info("协议尚未生成（首次运行前向步进时写入并冻结）")

elif page == "数据库浏览":
    import tracker
    tracker.render_database_page()
