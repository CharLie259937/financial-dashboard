# -*- coding: utf-8 -*-
"""
量化分析数据库模块
管理 quant_data.db: daily_quotes, passive_signals, active_events, stock_pool
依赖: pip install akshare pandas numpy requests
"""
import sqlite3
import pandas as pd
import numpy as np
import akshare as ak
import requests
import re
import json
from datetime import datetime, timedelta

DB_PATH = "quant_data.db"

_session = requests.Session()
_session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})


def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# 股票池管理
# ============================================================

def add_to_pool(code, market, name="", sector="", price_threshold=5.0, volume_ratio=2.0):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO stock_pool(code, market, name, sector, is_active, price_threshold, volume_ratio, added_at) "
        "VALUES(?,?,?,?,1,?,?,?)",
        (code.strip().upper(), market, name, sector, price_threshold, volume_ratio,
         datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    conn.commit()
    conn.close()


def get_stock_pool(active_only=True):
    conn = get_db()
    if active_only:
        rows = conn.execute("SELECT * FROM stock_pool WHERE is_active=1 ORDER BY market, code").fetchall()
    else:
        rows = conn.execute("SELECT * FROM stock_pool ORDER BY market, code").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def remove_from_pool(code):
    conn = get_db()
    conn.execute("DELETE FROM stock_pool WHERE code=?", (code,))
    conn.commit()
    conn.close()


def set_pool_active(code, is_active):
    conn = get_db()
    conn.execute("UPDATE stock_pool SET is_active=? WHERE code=?", (1 if is_active else 0, code))
    conn.commit()
    conn.close()


# ============================================================
# 日线数据采集
# ============================================================

def _get_a_share_prefix(code):
    if code.startswith('6'):
        return 'sh'
    elif code.startswith(('0', '3')):
        return 'sz'
    elif code.startswith(('8', '4')):
        return 'bj'
    return 'sh'


def collect_a_share_daily(code, days=365):
    """采集A股日线数据"""
    code = code.strip()
    prefix = _get_a_share_prefix(code)
    # 方案1: akshare
    try:
        df = ak.stock_zh_a_daily(symbol=f"{prefix}{code}", adjust="qfq")
        if df is not None and len(df) > 0:
            df = df.tail(days).copy()
            df['date'] = pd.to_datetime(df['date'])
            _save_daily_quotes(df, code, "A股", data_source="akshare")
            return len(df)
    except Exception:
        pass
    # 方案2: 新浪财经
    try:
        url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
        params = {"symbol": f"{prefix}{code}", "scale": "240", "ma": "no", "datalen": str(days + 20)}
        resp = _session.get(url, params=params, timeout=15,
                            headers={"Referer": "https://finance.sina.com.cn"})
        data = json.loads(resp.text)
        if data:
            df = pd.DataFrame(data)
            df['day'] = pd.to_datetime(df['day'])
            for col in ['open', 'high', 'low', 'close']:
                df[col] = pd.to_numeric(df[col])
            df['volume'] = pd.to_numeric(df['volume'])
            df = df.rename(columns={'day': 'date'}).tail(days)
            _save_daily_quotes(df, code, "A股", data_source="sina")
            return len(df)
    except Exception:
        pass
    return 0


def collect_us_daily(symbol, days=365):
    """采集美股日线数据"""
    symbol = symbol.strip().upper()
    try:
        df = ak.stock_us_daily(symbol=symbol, adjust="qfq")
        if df is not None and len(df) > 0:
            df = df.tail(days).copy()
            df['date'] = pd.to_datetime(df['date'])
            _save_daily_quotes(df, symbol, "美股", data_source="akshare")
            return len(df)
    except Exception:
        pass
    return 0


def collect_hk_daily(code, days=365):
    """采集港股日线数据"""
    code = code.strip().zfill(5)
    try:
        df = ak.stock_hk_daily(symbol=code, adjust="qfq")
        if df is not None and len(df) > 0:
            df = df.tail(days).copy()
            df['date'] = pd.to_datetime(df['date'])
            _save_daily_quotes(df, code, "港股", data_source="akshare")
            return len(df)
    except Exception:
        pass
    return 0


def _save_daily_quotes(df, code, market, data_source="manual"):
    """将 DataFrame 存入 daily_quotes 表"""
    conn = get_db()
    count = 0
    for _, row in df.iterrows():
        trade_date = row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date'])[:10]
        try:
            conn.execute(
                "INSERT OR REPLACE INTO daily_quotes "
                "(trade_date, code, market, name, open, high, low, close, volume, amount, data_source) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (trade_date, code, market, '',
                 float(row.get('open', 0)), float(row.get('high', 0)),
                 float(row.get('low', 0)), float(row.get('close', 0)),
                 int(row.get('volume', 0)), float(row.get('amount', 0)),
                 data_source)
            )
            count += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    return count


def batch_collect_daily(stocks=None, days=365):
    """批量采集多只股票日线数据"""
    if stocks is None:
        stocks = get_stock_pool(active_only=True)
    results = []
    for s in stocks:
        code = s['code'] if isinstance(s, dict) else s
        market = s.get('market', 'A股') if isinstance(s, dict) else 'A股'
        if market == "A股":
            n = collect_a_share_daily(code, days)
        elif market == "美股":
            n = collect_us_daily(code, days)
        elif market == "港股":
            n = collect_hk_daily(code, days)
        else:
            n = 0
        results.append({'code': code, 'market': market, 'rows': n})
    return results


# ============================================================
# 技术指标计算
# ============================================================

def calc_all_indicators(df):
    """在 DataFrame 上计算所有技术指标, 返回扩展后的 df"""
    df = df.copy()
    df = df.sort_values('date').reset_index(drop=True)
    close = df['close']
    high = df['high']
    low = df['low']

    # 均线
    for p in [5, 10, 20, 60]:
        df[f'MA{p}'] = close.rolling(window=p).mean()

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df['DIF'] = ema12 - ema26
    df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
    df['MACD'] = (df['DIF'] - df['DEA']) * 2

    # RSI (6, 12, 24)
    for period in [6, 12, 24]:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df[f'RSI{period}'] = 100 - (100 / (1 + rs))

    # KDJ
    low_9 = low.rolling(window=9).min()
    high_9 = high.rolling(window=9).max()
    rsv = (close - low_9) / (high_9 - low_9).replace(0, np.nan) * 100
    rsv = rsv.fillna(50)
    df['K'] = rsv.ewm(com=2, adjust=False).mean()
    df['D'] = df['K'].ewm(com=2, adjust=False).mean()
    df['J'] = 3 * df['K'] - 2 * df['D']

    # BOLL
    boll_mid = close.rolling(window=20).mean()
    boll_std = close.rolling(window=20).std()
    df['BOLL_UP'] = boll_mid + 2 * boll_std
    df['BOLL_MID'] = boll_mid
    df['BOLL_LOW'] = boll_mid - 2 * boll_std

    # 涨跌幅
    df['change_pct'] = (close.pct_change() * 100).round(2)

    # 成交量均线
    df['VOL_MA5'] = df['volume'].rolling(window=5).mean()
    df['VOL_MA10'] = df['volume'].rolling(window=10).mean()

    return df


# ============================================================
# 被动信号检测
# ============================================================

def detect_signals(df, code, market, name=""):
    """
    从带技术指标的 DataFrame 中检测信号, 存入 passive_signals 表
    返回检测到的信号列表
    """
    signals = []
    if len(df) < 30:
        return signals

    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]
        trade_date = curr['date'].strftime('%Y-%m-%d') if hasattr(curr['date'], 'strftime') else str(curr['date'])[:10]
        trigger_time = f"{trade_date} 15:00:00"

        # 1. MACD 金叉/死叉
        if pd.notna(prev['DIF']) and pd.notna(prev['DEA']) and pd.notna(curr['DIF']) and pd.notna(curr['DEA']):
            if prev['DIF'] <= prev['DEA'] and curr['DIF'] > curr['DEA']:
                signals.append({
                    'signal_type': 'macd_cross', 'signal_subtype': '金叉',
                    'direction': 'bullish', 'price': curr['close'],
                    'indicator_value': curr['DIF'], 'indicator_name': 'MACD_DIF',
                    'threshold': 0, 'threshold_type': 'macd_diff',
                    'description': f"MACD金叉 DIF:{curr['DIF']:.4f} > DEA:{curr['DEA']:.4f}",
                    'trade_date': trade_date,
                })
            elif prev['DIF'] >= prev['DEA'] and curr['DIF'] < curr['DEA']:
                signals.append({
                    'signal_type': 'macd_cross', 'signal_subtype': '死叉',
                    'direction': 'bearish', 'price': curr['close'],
                    'indicator_value': curr['DIF'], 'indicator_name': 'MACD_DIF',
                    'threshold': 0, 'threshold_type': 'macd_diff',
                    'description': f"MACD死叉 DIF:{curr['DIF']:.4f} < DEA:{curr['DEA']:.4f}",
                    'trade_date': trade_date,
                })

        # 2. RSI 超买/超卖
        if pd.notna(curr['RSI6']):
            if curr['RSI6'] < 30:
                signals.append({
                    'signal_type': 'rsi_oversold', 'signal_subtype': '超卖',
                    'direction': 'bullish', 'price': curr['close'],
                    'indicator_value': curr['RSI6'], 'indicator_name': 'RSI6',
                    'threshold': 30, 'threshold_type': 'rsi_value',
                    'description': f"RSI6超卖 {curr['RSI6']:.1f} < 30",
                    'trade_date': trade_date,
                })
            elif curr['RSI6'] > 70:
                signals.append({
                    'signal_type': 'rsi_overbought', 'signal_subtype': '超买',
                    'direction': 'bearish', 'price': curr['close'],
                    'indicator_value': curr['RSI6'], 'indicator_name': 'RSI6',
                    'threshold': 70, 'threshold_type': 'rsi_value',
                    'description': f"RSI6超买 {curr['RSI6']:.1f} > 70",
                    'trade_date': trade_date,
                })

        # 3. KDJ 金叉/死叉
        if pd.notna(prev['K']) and pd.notna(prev['D']) and pd.notna(curr['K']) and pd.notna(curr['D']):
            if prev['K'] <= prev['D'] and curr['K'] > curr['D']:
                signals.append({
                    'signal_type': 'kdj_cross', 'signal_subtype': '金叉',
                    'direction': 'bullish', 'price': curr['close'],
                    'indicator_value': curr['K'], 'indicator_name': 'KDJ_K',
                    'threshold': 20, 'threshold_type': 'kdj_value',
                    'description': f"KDJ金叉 K:{curr['K']:.1f} > D:{curr['D']:.1f}",
                    'trade_date': trade_date,
                })
            elif prev['K'] >= prev['D'] and curr['K'] < curr['D']:
                signals.append({
                    'signal_type': 'kdj_cross', 'signal_subtype': '死叉',
                    'direction': 'bearish', 'price': curr['close'],
                    'indicator_value': curr['K'], 'indicator_name': 'KDJ_K',
                    'threshold': 80, 'threshold_type': 'kdj_value',
                    'description': f"KDJ死叉 K:{curr['K']:.1f} < D:{curr['D']:.1f}",
                    'trade_date': trade_date,
                })

        # 4. BOLL 突破/跌破
        if pd.notna(curr['BOLL_UP']) and pd.notna(curr['BOLL_LOW']):
            if prev['close'] <= prev['BOLL_UP'] and curr['close'] > curr['BOLL_UP']:
                signals.append({
                    'signal_type': 'boll_break', 'signal_subtype': '突破上轨',
                    'direction': 'bullish', 'price': curr['close'],
                    'indicator_value': curr['close'], 'indicator_name': 'BOLL_UP',
                    'threshold': curr['BOLL_UP'], 'threshold_type': 'price',
                    'description': f"突破布林上轨 价格:{curr['close']:.2f} > 上轨:{curr['BOLL_UP']:.2f}",
                    'trade_date': trade_date,
                })
            elif prev['close'] >= prev['BOLL_LOW'] and curr['close'] < curr['BOLL_LOW']:
                signals.append({
                    'signal_type': 'boll_break', 'signal_subtype': '跌破下轨',
                    'direction': 'bearish', 'price': curr['close'],
                    'indicator_value': curr['close'], 'indicator_name': 'BOLL_LOW',
                    'threshold': curr['BOLL_LOW'], 'threshold_type': 'price',
                    'description': f"跌破布林下轨 价格:{curr['close']:.2f} < 下轨:{curr['BOLL_LOW']:.2f}",
                    'trade_date': trade_date,
                })

        # 5. 放量突破 (成交量超过5日均量2倍)
        if pd.notna(curr['VOL_MA5']) and curr['VOL_MA5'] > 0 and curr['volume'] > 0:
            vol_ratio = curr['volume'] / curr['VOL_MA5']
            if vol_ratio >= 2.0 and abs(curr['change_pct']) >= 3.0:
                direction = 'bullish' if curr['change_pct'] > 0 else 'bearish'
                signals.append({
                    'signal_type': 'volume_surge', 'signal_subtype': '放量突破',
                    'direction': direction, 'price': curr['close'],
                    'indicator_value': vol_ratio, 'indicator_name': 'VOL/MA5',
                    'threshold': 2.0, 'threshold_type': 'volume_ratio',
                    'description': f"放量{('上涨' if direction=='bullish' else '下跌')} 倍:{vol_ratio:.1f} 涨幅:{curr['change_pct']:+.2f}%",
                    'trade_date': trade_date,
                })

        # 6. 涨跌幅信号
        if abs(curr['change_pct']) >= 5.0:
            direction = 'bullish' if curr['change_pct'] > 0 else 'bearish'
            signals.append({
                'signal_type': 'price_limit', 'signal_subtype': '大涨' if direction=='bullish' else '大跌',
                'direction': direction, 'price': curr['close'],
                'indicator_value': curr['change_pct'], 'indicator_name': 'change_pct',
                'threshold': 5.0, 'threshold_type': 'price_pct',
                'description': f"{'大涨' if direction=='bullish' else '大跌'} {curr['change_pct']:+.2f}%",
                'trade_date': trade_date,
            })

    # 存入数据库
    if signals:
        conn = get_db()
        for sig in signals:
            conn.execute(
                "INSERT INTO passive_signals "
                "(code, market, name, trigger_time, signal_type, signal_subtype, direction, "
                "price, volume, indicator_value, indicator_name, threshold, threshold_type, "
                "description, trade_date) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (code, market, name, sig.get('trade_date', '') + ' 15:00:00', sig['signal_type'],
                 sig['signal_subtype'], sig['direction'], sig['price'],
                 0, sig['indicator_value'], sig['indicator_name'],
                 sig['threshold'], sig['threshold_type'],
                 sig['description'], sig['trade_date'])
            )
        conn.commit()
        conn.close()

    return signals


def run_signal_detection(stocks=None):
    """对股票池中所有股票运行信号检测"""
    if stocks is None:
        stocks = get_stock_pool(active_only=True)
    results = []
    for s in stocks:
        code = s['code']
        market = s.get('market', 'A股')
        name = s.get('name', '')
        df = get_daily_quotes(code, days=400)
        if df is not None and len(df) > 30:
            df = calc_all_indicators(df)
            sigs = detect_signals(df, code, market, name)
            results.append({'code': code, 'name': name, 'signals': len(sigs)})
        else:
            results.append({'code': code, 'name': name, 'signals': 0})
    return results


# ============================================================
# 主动事件管理
# ============================================================

def add_active_event(code, market, name, event_time, event_type, event_subtype,
                    direction, impact_level, title, content="", source="",
                    source_type="manual", related_codes=""):
    conn = get_db()
    conn.execute(
        "INSERT INTO active_events "
        "(code, market, name, event_time, event_type, event_subtype, direction, "
        "impact_level, title, content, source, source_type, related_codes, trade_date) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (code, market, name, event_time, event_type, event_subtype,
         direction, impact_level, title, content, source, source_type,
         related_codes, event_time[:10] if event_time else None)
    )
    conn.commit()
    conn.close()


def get_active_events(code=None, event_type=None, limit=50):
    conn = get_db()
    query = "SELECT * FROM active_events WHERE 1=1"
    params = []
    if code:
        query += " AND code=?"
        params.append(code)
    if event_type:
        query += " AND event_type=?"
        params.append(event_type)
    query += " ORDER BY event_time DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_active_event(event_id):
    conn = get_db()
    conn.execute("DELETE FROM active_events WHERE id=?", (event_id,))
    conn.commit()
    conn.close()


# ============================================================
# 查询函数
# ============================================================

def get_daily_quotes(code, days=365, start_date=None, end_date=None):
    """从数据库获取日线数据"""
    conn = get_db()
    if start_date and end_date:
        rows = conn.execute(
            "SELECT * FROM daily_quotes WHERE code=? AND trade_date>=? AND trade_date<=? ORDER BY trade_date",
            (code, start_date, end_date)
        ).fetchall()
    else:
        since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        rows = conn.execute(
            "SELECT * FROM daily_quotes WHERE code=? AND trade_date>=? ORDER BY trade_date",
            (code, since)
        ).fetchall()
    conn.close()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df['date'] = pd.to_datetime(df['trade_date'])
    return df


def get_passive_signals(code=None, signal_type=None, direction=None, limit=100):
    conn = get_db()
    query = "SELECT * FROM passive_signals WHERE 1=1"
    params = []
    if code:
        query += " AND code=?"
        params.append(code)
    if signal_type:
        query += " AND signal_type=?"
        params.append(signal_type)
    if direction:
        query += " AND direction=?"
        params.append(direction)
    query += " ORDER BY trigger_time DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_signal_stats():
    """信号统计"""
    conn = get_db()
    stats = {}
    stats['total'] = conn.execute("SELECT COUNT(*) as c FROM passive_signals").fetchone()['c']
    stats['by_type'] = conn.execute(
        "SELECT signal_type, signal_subtype, COUNT(*) as c FROM passive_signals GROUP BY signal_type, signal_subtype"
    ).fetchall()
    stats['by_direction'] = conn.execute(
        "SELECT direction, COUNT(*) as c FROM passive_signals GROUP BY direction"
    ).fetchall()
    stats['by_code'] = conn.execute(
        "SELECT code, name, COUNT(*) as c FROM passive_signals GROUP BY code ORDER BY c DESC LIMIT 10"
    ).fetchall()
    conn.close()
    return {k: ([dict(r) for r in v] if isinstance(v, list) else v) for k, v in stats.items()}


def get_data_coverage():
    """数据覆盖范围统计"""
    conn = get_db()
    result = {}
    result['total_quotes'] = conn.execute("SELECT COUNT(*) as c FROM daily_quotes").fetchone()['c']
    result['total_codes'] = conn.execute("SELECT COUNT(DISTINCT code) as c FROM daily_quotes").fetchone()['c']
    result['total_signals'] = conn.execute("SELECT COUNT(*) as c FROM passive_signals").fetchone()['c']
    result['total_events'] = conn.execute("SELECT COUNT(*) as c FROM active_events").fetchone()['c']
    date_range = conn.execute("SELECT MIN(trade_date) as min_d, MAX(trade_date) as max_d FROM daily_quotes").fetchone()
    result['date_range'] = f"{date_range['min_d']} ~ {date_range['max_d']}" if date_range['min_d'] else "无数据"
    result['by_market'] = conn.execute(
        "SELECT market, COUNT(DISTINCT code) as codes, COUNT(*) as rows FROM daily_quotes GROUP BY market"
    ).fetchall()
    result['pool_count'] = conn.execute("SELECT COUNT(*) as c FROM stock_pool WHERE is_active=1").fetchone()['c']
    conn.close()
    return {k: ([dict(r) for r in v] if isinstance(v, list) else v) for k, v in result.items()}


def get_stock_list_with_data():
    """获取所有有日线数据的股票列表"""
    conn = get_db()
    rows = conn.execute(
        "SELECT code, market, name, COUNT(*) as days, "
        "MIN(trade_date) as start, MAX(trade_date) as end "
        "FROM daily_quotes GROUP BY code, market ORDER BY market, code"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]



