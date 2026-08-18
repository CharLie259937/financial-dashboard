"""
金融数据可视化面板
运行: streamlit run dashboard.py
依赖: pip install streamlit plotly pandas requests
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import re
import akshare as ak
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
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

page = st.sidebar.radio("导航", ["实时查询", "长期追踪", "数据库浏览"])

if page == "实时查询":
    st.title("金融数据可视化面板")
elif page == "长期追踪":
    st.title("股票长期追踪")
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

elif page == "数据库浏览":
    import tracker
    tracker.render_database_page()
