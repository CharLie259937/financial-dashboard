"""
股票长期追踪模块
功能: 主动埋点(行情快照 5min) + 被动埋点(涨跌幅/量能异动)
运行: 通过 dashboard.py 侧边栏导航进入
"""

import sqlite3
import json
import threading
import time
import requests
import re
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DB_PATH = "stock_data.db"

session = requests.Session()
_retries = Retry(total=5, backoff_factor=1, connect=5, read=5,
                 status_forcelist=[502, 503, 504], allowed_methods=["GET"])
session.mount('https://', HTTPAdapter(max_retries=_retries))
session.mount('http://', HTTPAdapter(max_retries=_retries))
session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})


# ============================================================
# 数据获取函数 (与 dashboard.py 一致)
# ============================================================

def get_a_share_prefix(code: str) -> str:
    code = code.strip()
    if code.startswith('6'):
        return 'sh'
    elif code.startswith(('0', '3')):
        return 'sz'
    elif code.startswith(('8', '4')):
        return 'bj'
    return 'sh'


def _us_market_open() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    month = now.month
    if 3 <= month <= 11:
        return t >= 2130 or t < 400
    else:
        return t >= 2230 or t < 500


def _a_share_market_open() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    return (930 <= t < 1130) or (1300 <= t < 1500)


def fetch_us_realtime(symbol: str) -> dict:
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
        us_open = _us_market_open()
        if price == 0 and prev_close > 0:
            price = prev_close
            change = 0.0
            change_pct = 0.0
            market_status = "未开盘"
        elif not us_open:
            change = 0.0
            change_pct = 0.0
            market_status = "未开盘"
        else:
            change = float(f[4]) if f[4] else 0
            change_pct = float(f[2]) if f[2] else 0
            market_status = "交易中"
        return {
            'name': f[0], 'price': price, 'change_pct': change_pct,
            'trade_time': f[3], 'change': change, 'prev_close': prev_close,
            'high': float(f[6]) if f[6] else 0, 'low': float(f[7]) if f[7] else 0,
            'week_high_52': float(f[8]) if f[8] else 0, 'week_low_52': float(f[9]) if f[9] else 0,
            'volume': int(float(f[10])) if f[10] else 0, 'market_status': market_status,
        }
    except Exception:
        return {}


def fetch_a_share_realtime(code: str) -> dict:
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
        a_open = _a_share_market_open()
        if price == 0:
            price = prev_close
            change = 0.0
            change_pct = 0.0
            market_status = "未开盘"
        elif not a_open:
            change = 0.0
            change_pct = 0.0
            market_status = "未开盘"
        else:
            change = round(price - prev_close, 3)
            change_pct = round((change / prev_close * 100), 2) if prev_close else 0
            market_status = "交易中"
        return {
            'name': f[0], 'open': float(f[1]), 'prev_close': prev_close,
            'price': price, 'high': float(f[4]), 'low': float(f[5]),
            'volume': int(float(f[8])), 'amount': float(f[9]),
            'change': change, 'change_pct': change_pct,
            'date': f[30] if len(f) > 30 else '', 'time': f[31] if len(f) > 31 else '',
            'market_status': market_status,
        }
    except Exception:
        return {}


def fetch_hk_realtime(code: str) -> dict:
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
            'name': f[1], 'price': price, 'change_pct': change_pct,
            'change': change, 'prev_close': prev_close,
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


FETCH_RT = {
    '美股': fetch_us_realtime,
    'A股': fetch_a_share_realtime,
    '港股': fetch_hk_realtime,
}


# ============================================================
# 数据库
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS tracked_stocks (
        code TEXT PRIMARY KEY, market TEXT NOT NULL, name TEXT, added_at TEXT,
        price_threshold REAL DEFAULT 5.0, volume_ratio REAL DEFAULT 2.0
    )""")
    # 兼容旧表: 如果列不存在则添加
    try:
        c.execute("ALTER TABLE tracked_stocks ADD COLUMN price_threshold REAL DEFAULT 5.0")
    except:
        pass
    try:
        c.execute("ALTER TABLE tracked_stocks ADD COLUMN volume_ratio REAL DEFAULT 2.0")
    except:
        pass
    c.execute("""CREATE TABLE IF NOT EXISTS price_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL, market TEXT NOT NULL,
        price REAL, change REAL, change_pct REAL,
        volume INTEGER, high REAL, low REAL, prev_close REAL,
        market_status TEXT, timestamp TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS signal_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL, market TEXT NOT NULL, name TEXT,
        signal_type TEXT NOT NULL, description TEXT,
        price REAL, volume INTEGER, details TEXT, timestamp TEXT
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_snap_code ON price_snapshots(code)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_snap_ts ON price_snapshots(timestamp)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sig_ts ON signal_events(timestamp)")
    c.execute("""CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY, value TEXT
    )""")
    conn.commit()
    conn.close()


def get_setting(key, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?,?)", (key, str(value)))
    conn.commit()
    conn.close()


# ============================================================
# 追踪股票管理
# ============================================================

def add_tracked_stock(code, market, name="", price_threshold=5.0, volume_ratio=2.0):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO tracked_stocks(code, market, name, added_at, price_threshold, volume_ratio) VALUES(?,?,?,?,?,?)",
        (code.strip().upper(), market, name, datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
         price_threshold, volume_ratio)
    )
    conn.commit()
    conn.close()


def update_stock_thresholds(code, price_threshold, volume_ratio):
    conn = get_db()
    conn.execute(
        "UPDATE tracked_stocks SET price_threshold=?, volume_ratio=? WHERE code=?",
        (price_threshold, volume_ratio, code)
    )
    conn.commit()
    conn.close()


def remove_tracked_stock(code):
    conn = get_db()
    conn.execute("DELETE FROM tracked_stocks WHERE code=?", (code,))
    conn.commit()
    conn.close()


def get_tracked_stocks():
    conn = get_db()
    rows = conn.execute("SELECT * FROM tracked_stocks ORDER BY market, code").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================
# 数据采集
# ============================================================

def save_price_snapshot(code, market, quote):
    conn = get_db()
    conn.execute("""INSERT INTO price_snapshots
        (code, market, price, change, change_pct, volume, high, low, prev_close, market_status, timestamp)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (code, market, quote.get('price', 0), quote.get('change', 0),
         quote.get('change_pct', 0), quote.get('volume', 0),
         quote.get('high', 0), quote.get('low', 0), quote.get('prev_close', 0),
         quote.get('market_status', ''),
         datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()


def save_signal_event(code, market, name, signal_type, description, price, volume, details=""):
    conn = get_db()
    conn.execute("""INSERT INTO signal_events
        (code, market, name, signal_type, description, price, volume, details, timestamp)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        (code, market, name, signal_type, description, price, volume, details,
         datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()


def get_latest_snapshot(code):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM price_snapshots WHERE code=? ORDER BY id DESC LIMIT 1", (code,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ============================================================
# 被动埋点: 触发检测
# ============================================================

def check_triggers(code, market, name, quote, prev,
                    price_threshold=None, volume_ratio=None):
    signals = []
    change_pct = quote.get('change_pct', 0)
    volume = quote.get('volume', 0)
    price = quote.get('price', 0)

    if price_threshold is None:
        price_threshold = 5.0
    if volume_ratio is None:
        volume_ratio = 2.0

    if abs(change_pct) >= price_threshold:
        direction = "大涨" if change_pct > 0 else "大跌"
        signals.append({
            'signal_type': 'price_limit',
            'description': f"{direction} {change_pct:+.2f}% (阈值:±{price_threshold}%)",
            'price': price, 'volume': volume,
        })

    if prev and prev.get('volume', 0) > 0 and volume > 0:
        vol_ratio_val = volume / prev['volume']
        if vol_ratio_val >= volume_ratio:
            signals.append({
                'signal_type': 'volume_surge',
                'description': f"成交量放大 {vol_ratio_val:.1f} 倍 (前:{prev['volume']} 现:{volume}, 阈值:{volume_ratio}倍)",
                'price': price, 'volume': volume,
            })

    return signals


# ============================================================
# 追踪周期
# ============================================================

def run_tracking_cycle():
    stocks = get_tracked_stocks()
    total_signals = 0
    results = []

    for stock in stocks:
        fetch_fn = FETCH_RT.get(stock['market'])
        if not fetch_fn:
            continue
        quote = fetch_fn(stock['code'])
        if not quote or quote.get('price', 0) == 0:
            continue

        name = quote.get('name', stock.get('name', ''))
        if not stock.get('name'):
            conn = get_db()
            conn.execute("UPDATE tracked_stocks SET name=? WHERE code=?", (name, stock['code']))
            conn.commit()
            conn.close()

        prev = get_latest_snapshot(stock['code'])
        save_price_snapshot(stock['code'], stock['market'], quote)

        stock_pt = stock.get('price_threshold', 5.0)
        stock_vr = stock.get('volume_ratio', 2.0)
        signals = check_triggers(stock['code'], stock['market'], name, quote, prev,
                                  price_threshold=stock_pt, volume_ratio=stock_vr)
        for sig in signals:
            save_signal_event(stock['code'], stock['market'], name,
                              sig['signal_type'], sig['description'],
                              sig['price'], sig['volume'])
            total_signals += 1

        results.append({
            'code': stock['code'], 'name': name, 'market': stock['market'],
            'price': quote.get('price', 0), 'change_pct': quote.get('change_pct', 0),
            'volume': quote.get('volume', 0), 'signals': len(signals),
            'status': quote.get('market_status', ''),
        })

    return {'stocks': len(stocks), 'signals': total_signals, 'details': results}


# ============================================================
# 查询函数
# ============================================================

def get_recent_signals(limit=50):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM signal_events ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_price_history(code, hours=24):
    conn = get_db()
    since = (datetime.now() - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')
    rows = conn.execute(
        "SELECT * FROM price_snapshots WHERE code=? AND timestamp>=? ORDER BY timestamp",
        (code, since)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_snapshot_count(code=None):
    conn = get_db()
    if code:
        row = conn.execute("SELECT COUNT(*) as cnt FROM price_snapshots WHERE code=?", (code,)).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) as cnt FROM price_snapshots").fetchone()
    conn.close()
    return row['cnt'] if row else 0


# ============================================================
# 后台线程追踪 (独立于浏览器 WebSocket)
# ============================================================

_tracking_thread = None
_tracking_stop_event = threading.Event()
_tracking_status = {'last_run': '', 'last_stocks': 0, 'last_signals': 0, 'errors': 0}


def _background_worker(interval=300):
    init_db()
    while not _tracking_stop_event.is_set():
        try:
            result = run_tracking_cycle()
            _tracking_status['last_run'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            _tracking_status['last_stocks'] = result.get('stocks', 0)
            _tracking_status['last_signals'] = result.get('signals', 0)
        except Exception as e:
            _tracking_status['errors'] += 1
        _tracking_stop_event.wait(interval)


def start_background_tracking(interval=300):
    global _tracking_thread
    if _tracking_thread and _tracking_thread.is_alive():
        return
    _tracking_stop_event.clear()
    _tracking_thread = threading.Thread(target=_background_worker, args=(interval,), daemon=True)
    _tracking_thread.start()


def stop_background_tracking():
    _tracking_stop_event.set()


def is_tracking_running():
    global _tracking_thread
    return _tracking_thread is not None and _tracking_thread.is_alive()


# ============================================================
# 追踪页面 UI
# ============================================================

def render_tracking_page():
    init_db()
    st.header("长期追踪")

    col1, col2 = st.columns([3, 1])
    with col2:
        total_snaps = get_snapshot_count()
        total_signals = len(get_recent_signals(limit=1000))
        st.metric("已采集快照", f"{total_snaps:,}")
        st.metric("被动信号事件", f"{total_signals}")

    tab1, tab2, tab3 = st.tabs(["追踪管理", "被动信号事件", "历史走势"])

    # ---- Tab 1: 追踪管理 ----
    with tab1:
        st.subheader("添加追踪股票")
        add_col1, add_col2, add_col3, add_col4, add_col5 = st.columns([2, 1, 1, 1, 1])
        with add_col1:
            new_code = st.text_input("股票代码", placeholder="如 600000 / AAPL / 00700", key="track_add_code")
        with add_col2:
            new_market = st.selectbox("市场", ["A股", "美股", "港股"], key="track_add_market")
        with add_col3:
            new_pt = st.number_input("涨跌幅阈值(%)", min_value=0.1, max_value=20.0, value=5.0, step=0.5, key="track_add_pt")
        with add_col4:
            new_vr = st.number_input("成交量倍数", min_value=1.1, max_value=10.0, value=2.0, step=0.1, key="track_add_vr")
        with add_col5:
            st.write("")
            st.write("")
            if st.button("添加追踪", type="primary", key="track_add_btn"):
                if new_code.strip():
                    add_tracked_stock(new_code, new_market, price_threshold=new_pt, volume_ratio=new_vr)
                    st.success(f"已添加: {new_code.strip().upper()} ({new_market}) 阈值:±{new_pt}%/{new_vr}倍")
                    st.rerun()

        st.divider()

        st.subheader("追踪中的股票")
        stocks = get_tracked_stocks()
        if not stocks:
            st.info("尚未添加任何追踪股票。请在上方添加。")
        else:
            for stock in stocks:
                snap = get_latest_snapshot(stock['code'])
                snap_count = get_snapshot_count(stock['code'])
                sc1, sc2, sc3, sc4, sc5 = st.columns([2, 1.2, 1.5, 1.3, 0.5])
                with sc1:
                    name_display = stock.get('name') or '名称待获取'
                    st.write(f"**{stock['code']}** ({stock['market']}) {name_display}")
                    st.caption(f"已采集快照: {snap_count}")
                with sc2:
                    if snap and snap.get('price', 0) > 0:
                        delta = snap.get('change_pct', 0)
                        st.metric("最新价", f"{snap['price']:.2f}", f"{delta:+.2f}%")
                    else:
                        st.metric("最新价", "—", "无数据")
                with sc3:
                    cur_pt = stock.get('price_threshold', 5.0)
                    cur_vr = stock.get('volume_ratio', 2.0)
                    pt_input = st.number_input(
                        "涨幅阈值(%)", min_value=0.1, max_value=20.0,
                        value=float(cur_pt), step=0.5,
                        key=f"pt_{stock['code']}")
                    vr_input = st.number_input(
                        "量能倍数", min_value=1.1, max_value=10.0,
                        value=float(cur_vr), step=0.1,
                        key=f"vr_{stock['code']}")
                    if pt_input != float(cur_pt) or vr_input != float(cur_vr):
                        update_stock_thresholds(stock['code'], pt_input, vr_input)
                        st.toast(f"{stock['code']} 阈值已更新: ±{pt_input}% / {vr_input}倍")
                with sc4:
                    if snap:
                        st.caption(f"状态: {snap.get('market_status', 'N/A')}")
                        ts = snap.get('timestamp', '')
                        st.caption(f"采集: {ts[-8:] if ts else 'N/A'}")
                    else:
                        st.caption("尚未采集")
                with sc5:
                    st.write("")
                    if st.button("删除", key=f"del_{stock['code']}"):
                        remove_tracked_stock(stock['code'])
                        st.rerun()

        st.divider()

        st.caption("提示: 每只股票的被动埋点阈值可在上方股票列表中独立设置")

        st.divider()

        st.subheader("追踪控制")
        tracking_active = is_tracking_running()

        cc1, cc2, cc3 = st.columns([1, 1, 2])
        with cc1:
            if not tracking_active:
                if st.button("开始追踪", type="primary", key="start_track"):
                    set_setting('tracking_active', 'true')
                    start_background_tracking(interval=300)
                    st.rerun()
            else:
                if st.button("停止追踪", type="secondary", key="stop_track"):
                    set_setting('tracking_active', 'false')
                    stop_background_tracking()
                    st.rerun()
        with cc2:
            if st.button("立即采集一次", key="manual_collect"):
                with st.spinner("正在采集数据..."):
                    result = run_tracking_cycle()
                if result['stocks'] > 0:
                    sig_text = f", {result['signals']} 个新信号" if result['signals'] > 0 else ""
                    st.success(f"采集完成: {result['stocks']} 只股票{sig_text}")
                    for d in result.get('details', []):
                        icon = "🟢" if d.get('change_pct', 0) >= 0 else "🔴"
                        st.text(f"  {icon} {d['name']} ({d['code']})  价格:{d['price']:.2f}  涨跌:{d['change_pct']:+.2f}%  信号:{d['signals']}")
                else:
                    st.warning("未采集到数据，请先添加追踪股票或检查网络")
                st.rerun()

        if tracking_active:
            st.success("后台追踪运行中 — 每 5 分钟自动采集，关闭页面也不中断")
            st.caption(f"上次采集: {_tracking_status.get('last_run', 'N/A')} | 采集股票数: {_tracking_status.get('last_stocks', 0)} | 错误次数: {_tracking_status.get('errors', 0)}")
            # 如果上次有信号, 刷新显示
            last_run = _tracking_status.get('last_run', '')
            if last_run:
                st.info(f"最近一次采集: {last_run} | 股票: {_tracking_status['last_stocks']} | 新信号: {_tracking_status['last_signals']}")
        else:
            st.caption("点击「开始追踪」开启后台自动采集（关闭页面也不中断），或「立即采集一次」手动执行")

    # ---- Tab 2: 被动信号事件 ----
    with tab2:
        signals = get_recent_signals(limit=50)
        if not signals:
            st.info("暂无被动信号事件。追踪运行后会根据自定义阈值自动检测并记录。")
        else:
            import pandas as pd
            df_signals = pd.DataFrame(signals)
            df_signals['display'] = df_signals.apply(
                lambda r: f"{r['name']} ({r['code']}) - {r['description']}", axis=1)
            for _, row in df_signals.iterrows():
                sig_type = row['signal_type']
                if sig_type == 'price_limit':
                    color = "🔴" if '大跌' in row['description'] else "🟢"
                else:
                    color = "🟡"
                st.markdown(f"{color} **{row['name']}** ({row['code']}) | {row['description']} | 价格:{row['price']} | {row['timestamp']}")
            st.caption(f"共 {len(signals)} 条信号 (最近 50 条)")

    # ---- Tab 3: 历史走势 ----
    with tab3:
        stocks = get_tracked_stocks()
        if not stocks:
            st.info("请先添加追踪股票。")
        else:
            stock_options = {f"{s['code']} ({s['market']})": s['code'] for s in stocks}
            selected_label = st.selectbox("选择股票", list(stock_options.keys()))
            selected_code = stock_options[selected_label]

            hours = st.slider("查看时长 (小时)", 1, 72, 24)
            history = get_price_history(selected_code, hours)

            if len(history) < 2:
                st.info("数据不足，请等待追踪运行后查看。")
            else:
                import pandas as pd
                df = pd.DataFrame(history)
                df['timestamp'] = pd.to_datetime(df['timestamp'])

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=df['timestamp'], y=df['price'],
                    mode='lines+markers', name='价格',
                    line=dict(color='#4B3FE3', width=2),
                ))
                fig.update_layout(
                    title=f"{selected_code} 价格走势 (近{hours}小时)",
                    xaxis_title="时间", yaxis_title="价格",
                    template="plotly_white", height=400,
                )
                st.plotly_chart(fig, use_container_width=True)

                with st.expander("查看原始快照数据"):
                    st.dataframe(df[['timestamp', 'price', 'change_pct', 'volume', 'market_status']],
                                 use_container_width=True, hide_index=True)


# @st.fragment 方式已弃用, 改为后台线程 (见上方 _background_worker)
# 后台线程不依赖浏览器 WebSocket 连接, 关闭页面也不中断


# ============================================================
# 数据库浏览页面 UI
# ============================================================

def render_database_page():
    init_db()
    st.header("数据库浏览")
    st.caption(f"数据库文件: `{DB_PATH}`")

    tab1, tab2, tab3, tab4 = st.tabs([
        f"追踪列表",
        f"行情快照",
        f"被动信号事件",
        "自定义查询",
    ])

    # --- Tab 1: tracked_stocks ---
    with tab1:
        conn = get_db()
        rows = conn.execute("SELECT * FROM tracked_stocks ORDER BY added_at DESC").fetchall()
        conn.close()
        if rows:
            df = pd.DataFrame([dict(r) for r in rows])
            st.dataframe(df, use_container_width=True, hide_index=True)
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("导出 CSV", csv, "tracked_stocks.csv", "text/csv; charset=utf-8")
        else:
            st.info("追踪列表为空")

    # --- Tab 2: price_snapshots ---
    with tab2:
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            code_filter = st.text_input("按股票代码筛选", "", key="snap_code")
        with fc2:
            date_from = st.date_input("起始日期", value=None, key="snap_from")
        with fc3:
            date_to = st.date_input("结束日期", value=None, key="snap_to")

        conn = get_db()
        query = "SELECT * FROM price_snapshots WHERE 1=1"
        params = []
        if code_filter:
            query += " AND code LIKE ?"
            params.append(f"%{code_filter.upper()}%")
        if date_from:
            query += " AND timestamp >= ?"
            params.append(date_from.strftime("%Y-%m-%d"))
        if date_to:
            query += " AND timestamp <= ?"
            params.append(date_to.strftime("%Y-%m-%d") + " 23:59:59")
        query += " ORDER BY id DESC LIMIT 500"
        rows = conn.execute(query, params).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM price_snapshots WHERE 1=1" +
            (" AND code LIKE ?" if code_filter else "") +
            (" AND timestamp >= ?" if date_from else "") +
            (" AND timestamp <= ?" if date_to else ""),
            [p for p in params if not str(p).startswith("%") or True]
        ).fetchone()
        conn.close()

        if rows:
            df = pd.DataFrame([dict(r) for r in rows])
            st.caption(f"显示最近 {len(rows)} 条（共 {total['cnt']} 条）")
            st.dataframe(df, use_container_width=True, hide_index=True, height=400)
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("导出 CSV", csv, "price_snapshots.csv", "text/csv; charset=utf-8")
        else:
            st.info("暂无快照数据")

    # --- Tab 3: signal_events ---
    with tab3:
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM signal_events ORDER BY id DESC LIMIT 200"
        ).fetchall()
        conn.close()
        if rows:
            df = pd.DataFrame([dict(r) for r in rows])
            st.dataframe(df, use_container_width=True, hide_index=True, height=400)
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("导出 CSV", csv, "signal_events.csv", "text/csv; charset=utf-8")
        else:
            st.info("暂无被动信号事件")

    # --- Tab 4: Custom SQL ---
    with tab4:
        st.caption("输入 SQL 查询语句（仅支持 SELECT）")
        sql = st.text_area(
            "SQL",
            "SELECT * FROM price_snapshots ORDER BY id DESC LIMIT 20",
            height=100,
            key="custom_sql",
        )
        if st.button("执行查询", key="run_sql"):
            sql_stripped = sql.strip().lower()
            if not sql_stripped.startswith("select"):
                st.error("仅允许 SELECT 查询")
            else:
                try:
                    conn = get_db()
                    rows = conn.execute(sql).fetchall()
                    conn.close()
                    if rows:
                        df = pd.DataFrame([dict(r) for r in rows])
                        st.dataframe(df, use_container_width=True, hide_index=True)
                        csv = df.to_csv(index=False).encode('utf-8-sig')
                        st.download_button("导出 CSV", csv, "query_result.csv", "text/csv; charset=utf-8")
                    else:
                        st.info("查询结果为空")
                except Exception as e:
                    st.error(f"SQL 错误: {e}")
