# -*- coding: utf-8 -*-
"""
量化分析数据库模块
管理 quant_data.db: daily_quotes, passive_signals, active_events, stock_pool
依赖: pip install akshare pandas numpy requests
"""
import sqlite3
import os
import re
import bisect
import pandas as pd
import numpy as np
import requests
import json
from datetime import datetime, timedelta, time as dtime
from curl_cffi import requests as cffi_requests

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quant_data.db")

_session = requests.Session()
_session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})


def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS stock_pool(
        code TEXT PRIMARY KEY,
        market TEXT NOT NULL,
        name TEXT DEFAULT '',
        sector TEXT DEFAULT '',
        is_active INTEGER DEFAULT 1,
        price_threshold REAL DEFAULT 5.0,
        volume_ratio REAL DEFAULT 2.0,
        added_at TEXT
    );

    CREATE TABLE IF NOT EXISTS daily_quotes(
        trade_date TEXT NOT NULL,
        code TEXT NOT NULL,
        market TEXT,
        name TEXT DEFAULT '',
        open REAL, high REAL, low REAL, close REAL,
        volume INTEGER, amount REAL,
        data_source TEXT DEFAULT 'manual',
        PRIMARY KEY(trade_date, code)
    );
    CREATE INDEX IF NOT EXISTS idx_quotes_code ON daily_quotes(code);
    CREATE INDEX IF NOT EXISTS idx_quotes_date ON daily_quotes(trade_date);

    CREATE TABLE IF NOT EXISTS benchmark_index(
        market TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        code TEXT DEFAULT '',
        name TEXT DEFAULT '',
        open REAL, high REAL, low REAL, close REAL,
        volume REAL, amount REAL,
        PRIMARY KEY(market, trade_date)
    );

    CREATE TABLE IF NOT EXISTS passive_signals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL,
        market TEXT,
        name TEXT DEFAULT '',
        trigger_time TEXT,
        signal_type TEXT,
        signal_subtype TEXT,
        direction TEXT,
        price REAL,
        volume INTEGER,
        indicator_value REAL,
        indicator_name TEXT,
        threshold REAL,
        threshold_type TEXT,
        description TEXT,
        trade_date TEXT,
        source TEXT DEFAULT 'live'
    );
    CREATE INDEX IF NOT EXISTS idx_sig_code ON passive_signals(code);
    CREATE INDEX IF NOT EXISTS idx_sig_type ON passive_signals(signal_type);

    CREATE TABLE IF NOT EXISTS active_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL,
        market TEXT,
        name TEXT DEFAULT '',
        event_time TEXT,
        event_type TEXT,
        event_subtype TEXT,
        direction TEXT,
        impact_level TEXT,
        title TEXT,
        content TEXT,
        source TEXT,
        source_type TEXT DEFAULT 'manual',
        related_codes TEXT,
        trade_date TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_evt_code ON active_events(code);
    CREATE INDEX IF NOT EXISTS idx_evt_type ON active_events(event_type);

    CREATE TABLE IF NOT EXISTS daily_feature_base(
        trade_date TEXT NOT NULL,
        code TEXT NOT NULL,
        market TEXT,
        open REAL, high REAL, low REAL, close REAL,
        volume INTEGER, amount REAL,
        change_pct REAL, turnover_rate REAL,
        pe_ratio REAL, pb_ratio REAL,
        total_market_cap REAL, circ_market_cap REAL, net_inflow REAL,
        sig_macd_cross INTEGER DEFAULT 0,
        sig_rsi_oversold INTEGER DEFAULT 0,
        sig_rsi_overbought INTEGER DEFAULT 0,
        sig_kdj_cross INTEGER DEFAULT 0,
        sig_boll_break INTEGER DEFAULT 0,
        sig_volume_surge INTEGER DEFAULT 0,
        sig_price_limit INTEGER DEFAULT 0,
        evt_earnings INTEGER DEFAULT 0,
        evt_dividend INTEGER DEFAULT 0,
        evt_policy INTEGER DEFAULT 0,
        evt_industry INTEGER DEFAULT 0,
        evt_announcement INTEGER DEFAULT 0,
        evt_macro INTEGER DEFAULT 0,
        PRIMARY KEY(trade_date, code)
    );
    CREATE INDEX IF NOT EXISTS idx_dfb_code ON daily_feature_base(code);
    CREATE INDEX IF NOT EXISTS idx_dfb_date ON daily_feature_base(trade_date);

    CREATE TABLE IF NOT EXISTS daily_indicators(
        code TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        market TEXT,
        ma5 REAL, ma10 REAL, ma20 REAL, ma60 REAL,
        dif REAL, dea REAL, macd_bar REAL,
        rsi14 REAL,
        kdj_k REAL, kdj_d REAL, kdj_j REAL,
        boll_up REAL, boll_mid REAL, boll_low REAL,
        volatility20 REAL, atr14 REAL,
        vol_ma5 REAL, vol_ma20 REAL, obv REAL,
        change_pct REAL, ret_3d REAL, ret_5d REAL, ret_20d REAL,
        PRIMARY KEY(code, trade_date)
    );
    CREATE INDEX IF NOT EXISTS idx_di_date ON daily_indicators(trade_date);

    -- 模块10: 股票池动态扩容 (前向资格事件 + 离池快照冻结)
    CREATE TABLE IF NOT EXISTS forward_pool_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL, market TEXT, name TEXT,
        event TEXT NOT NULL,
        eff_date TEXT NOT NULL,
        source TEXT,
        run_ts TEXT
    );
    CREATE TABLE IF NOT EXISTS forward_stock_px(
        code TEXT NOT NULL, trade_date TEXT NOT NULL,
        open REAL, close REAL,
        PRIMARY KEY(code, trade_date)
    );
    CREATE TABLE IF NOT EXISTS forward_stock_sig(
        code TEXT NOT NULL, trade_date TEXT NOT NULL,
        signal_type TEXT NOT NULL, signal_subtype TEXT,
        PRIMARY KEY(code, trade_date, signal_type, signal_subtype)
    );
    """)
    conn.commit()
    conn.close()


def _m10_migrate_events():
    """模块10 一次性迁移: 存量池股票补 join 事件(幂等: 事件表空且池非空才写)
    eff_date = max(FORWARD_START, added_at日期部分) — 原始股=FORWARD_START,
    迁移前已入池的新股=其 added_at(资格从实际入池日起算, 历史信号不追溯)"""
    conn = get_db()
    has_events = conn.execute(
        "SELECT 1 FROM forward_pool_events LIMIT 1").fetchone()
    if has_events:
        conn.close()
        return {'migrated': 0}
    pool = conn.execute(
        "SELECT code, market, name, added_at FROM stock_pool").fetchall()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for r in pool:
        added_day = (r['added_at'] or '')[:10]
        eff = max(FORWARD_START, added_day) if added_day else FORWARD_START
        conn.execute(
            "INSERT INTO forward_pool_events(code, market, name, event, "
            "eff_date, source, run_ts) VALUES(?,?,?,?,?,?,?)",
            (r['code'], r['market'], r['name'], 'join', eff, 'migrate', now))
    conn.commit()
    conn.close()
    return {'migrated': len(pool)}


init_db()


def _migrate_db():
    """向 daily_quotes 表添加扩展字段（涨跌幅/换手率/估值/资金流）"""
    conn = get_db()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_quotes)").fetchall()]
    new_cols = {
        'change_pct': 'REAL DEFAULT 0',
        'amplitude': 'REAL DEFAULT 0',
        'turnover_rate': 'REAL DEFAULT 0',
        'pe_ratio': 'REAL DEFAULT 0',
        'pb_ratio': 'REAL DEFAULT 0',
        'total_market_cap': 'REAL DEFAULT 0',
        'circ_market_cap': 'REAL DEFAULT 0',
        'net_inflow': 'REAL DEFAULT 0',
    }
    for col, col_type in new_cols.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE daily_quotes ADD COLUMN {col} {col_type}")

    # 模块8: passive_signals 增加 source 列('live'实采 / 'replay'回放), 存量行默认 live
    sig_cols = [r[1] for r in conn.execute("PRAGMA table_info(passive_signals)").fetchall()]
    if 'source' not in sig_cols:
        conn.execute("ALTER TABLE passive_signals ADD COLUMN source TEXT DEFAULT 'live'")

    conn.commit()
    conn.close()


_migrate_db()


# ============================================================
# 股票池管理
# ============================================================

def add_to_pool(code, market, name="", sector="", price_threshold=5.0, volume_ratio=2.0):
    """入池(模块10): 港股代码归一化为5位(collect_hk_daily 落库即 zfill(5),
    池代码与行情代码不一致会使采集/指标/信号全部链接失败 — 孤儿股根因);
    新入池/重新激活追加 join 资格事件, 返回归一化后的代码"""
    conn = get_db()
    code = code.strip().upper()
    if market == '港股':
        code = code.zfill(5)
    existed = conn.execute("SELECT is_active FROM stock_pool WHERE code=?",
                           (code,)).fetchone()
    conn.execute(
        "INSERT OR REPLACE INTO stock_pool(code, market, name, sector, is_active, price_threshold, volume_ratio, added_at) "
        "VALUES(?,?,?,?,1,?,?,?)",
        (code, market, name, sector, price_threshold, volume_ratio,
         datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    conn.commit()
    conn.close()
    # 模块10: 前向资格事件 — 新入池/重新激活追加 join(eff=今天);
    # 已在池且活跃的重复添加不追加(防重复事件); 数据引导由 onboard_pool_stock 负责
    if not (existed and existed[0] == 1):
        _m10_event(code, 'join', 'add_to_pool')
    return code


def get_stock_pool(active_only=True):
    conn = get_db()
    if active_only:
        rows = conn.execute("SELECT * FROM stock_pool WHERE is_active=1 ORDER BY market, code").fetchall()
    else:
        rows = conn.execute("SELECT * FROM stock_pool ORDER BY market, code").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _pool_code_set(active_only=True):
    """当前池代码集合 — 所有采集/分析模块的股票单一事实源"""
    conn = get_db()
    sql = "SELECT code FROM stock_pool" + (" WHERE is_active=1" if active_only else "")
    codes = {r[0] for r in conn.execute(sql)}
    conn.close()
    return codes


def _pool_sql_filter(codes, alias=''):
    """构造 'alias.code IN (?,?,...)' 片段; 池为空返回恒假条件"""
    if not codes:
        return "1=0", []
    ph = ",".join("?" * len(codes))
    col = f"{alias}.code" if alias else "code"
    return f"{col} IN ({ph})", sorted(codes)


def get_stock_from_pool(code):
    conn = get_db()
    row = conn.execute("SELECT * FROM stock_pool WHERE code=?", (code,)).fetchone()
    conn.close()
    return dict(row) if row else None


def remove_from_pool(code):
    """移除股票并级联删除其全部个股数据(行情/信号/事件/因子/宽表)
    2026-09-03 起移除即清理: 否则剔除股残留数据继续进入分析(问题日志#6)
    2026-09-07 模块10: 生效前冻结前向快照(px+sig)+leave事件(追加式),
    已入库前向净值仍可由快照逐日复现(追加守卫零失配); 快照失败则中止移除
    停用(不删数据)请用 set_pool_active"""
    code = code.strip()
    snap = _snapshot_forward_stock(code)
    _m10_event(code, 'leave', 'remove_from_pool')
    conn = get_db()
    removed = {}
    for table in ('daily_quotes', 'passive_signals', 'active_events',
                  'daily_indicators', 'daily_feature_base'):
        cur = conn.execute(f"DELETE FROM {table} WHERE code=?", (code,))
        removed[table] = cur.rowcount
    conn.execute("DELETE FROM stock_pool WHERE code=?", (code,))
    conn.commit()
    conn.close()
    removed['forward_snapshot'] = snap
    return removed


def set_pool_active(code, is_active):
    """切换活跃状态(模块10): 停用=前向快照+leave(数据保留但不进分析/前向);
    再激活=新 join(资格从当天重新起算, 旧区间已由快照冻结, 互不污染)"""
    conn = get_db()
    conn.execute("UPDATE stock_pool SET is_active=? WHERE code=?", (1 if is_active else 0, code))
    conn.commit()
    conn.close()
    if not is_active:
        _snapshot_forward_stock(code)
        _m10_event(code, 'leave', 'set_pool_active')
    elif not _stock_open_membership(code):
        _m10_event(code, 'join', 'set_pool_active')


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


def _yahoo_symbol_for(code, market):
    """Yahoo日线兜底符号映射(数据源审计2026-09-10): 港股4位无前导零
    (00700->0700.HK; 注意 00700.HK 是僵尸别名·2019年停更), A股沪.SS/深.SZ(北交无), 美股复用_yahoo_symbol"""
    code = code.strip()
    if market == "美股":
        return _yahoo_symbol(code)
    if market == "港股":
        try:
            return str(int(code)).zfill(4) + '.HK'
        except ValueError:
            return None
    if market == "A股":
        suffix = {'sh': '.SS', 'sz': '.SZ'}.get(_get_a_share_prefix(code))
        return f"{code}{suffix}" if suffix else None
    return None


def _yahoo_chart_daily(code, market, days=365):
    """Yahoo v8/finance/chart 日线兜底(akshare/新浪均失败后):
    - 必须 period1/period2 取数, 禁用 range=max(超量会被降采样, AAPL 42年仅回169根)
    - adjclose/close 因子缩放 OHLC = 前复权口径, 与库内新浪qfq水平差实测<0.1%
    - INSERT OR IGNORE: 只补缺失交易日, 绝不改写既有行(兜底源不污染主源数据)
    - 守卫① exchange=YHD 僵尸符号拒绝; 守卫② 末档较库内陈旧>7天拒绝;
      守卫③ 重叠日收盘偏差>2% 拒绝(防符号映射错/口径错)"""
    sym = _yahoo_symbol_for(code, market)
    if not sym:
        print(f"  [_yahoo_chart_daily] {code}({market}): 无Yahoo符号映射, 跳过兜底")
        return 0
    try:
        p2 = int(datetime.now().timestamp()) + 86400
        p1 = p2 - int((days + 30) * 1.6 * 86400)
        r = _session.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
            params={"period1": p1, "period2": p2, "interval": "1d"}, timeout=20)
        j = r.json().get('chart', {})
        if j.get('error'):
            print(f"  [_yahoo_chart_daily] {code}: {j['error']}")
            return 0
        rr = (j.get('result') or [None])[0]
        if not rr:
            print(f"  [_yahoo_chart_daily] {code}: {sym} 无数据")
            return 0
        meta = rr.get('meta', {})
        if meta.get('exchangeName') == 'YHD':
            print(f"  [_yahoo_chart_daily] {code}: 僵尸符号 {sym}(YHD别名) 拒绝")
            return 0
        ts = rr.get('timestamp') or []
        q = (rr.get('indicators', {}).get('quote') or [{}])[0]
        adj = rr.get('indicators', {}).get('adjclose')
        adj = adj[0].get('adjclose') if adj else None
        if not ts or not adj:
            print(f"  [_yahoo_chart_daily] {code}: 无K线或无adjclose")
            return 0
        df = pd.DataFrame({"ts": ts, "open": q.get("open"), "high": q.get("high"),
                           "low": q.get("low"), "close": q.get("close"),
                           "volume": q.get("volume")})
        if len(adj) >= len(df):
            df["adj"] = adj[:len(df)]
        else:
            df["adj"] = list(adj) + [None] * (len(df) - len(adj))
        df = df.dropna(subset=["close", "adj"]).copy()
        if not len(df):
            return 0
        factor = df["adj"].astype(float) / df["close"].astype(float)
        for col in ("open", "high", "low", "close"):
            df[col] = (df[col].astype(float) * factor).round(4)
        df["date"] = pd.to_datetime(df["ts"], unit="s").dt.strftime("%Y-%m-%d")
        df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
        conn = get_db()
        last = conn.execute("SELECT trade_date, close FROM daily_quotes WHERE code=? "
                            "ORDER BY trade_date DESC LIMIT 1", (code,)).fetchone()
        conn.close()
        if last:
            stale_before = (pd.to_datetime(last["trade_date"]) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
            if df["date"].max() < stale_before:
                print(f"  [_yahoo_chart_daily] {code}: Yahoo末档{df['date'].max()} "
                      f"较库内{last['trade_date']}陈旧>7天, 拒绝")
                return 0
            overlap = df[df["date"] == last["trade_date"]]
            if len(overlap):
                dev = abs(float(overlap.iloc[0]["close"]) - float(last["close"])) / float(last["close"])
                if dev > 0.02:
                    print(f"  [_yahoo_chart_daily] {code}: 重叠日收盘偏差{dev*100:.1f}%>2%, 拒绝落库")
                    return 0
        df["change_pct"] = (df["close"].pct_change() * 100).round(2)
        df["amplitude"] = ((df["high"] - df["low"]) / df["close"].shift(1) * 100).round(2)
        df = df.tail(days)
        return _save_daily_quotes(df, code, market, data_source="yahoo", replace=False)
    except Exception as e:
        print(f"  [_yahoo_chart_daily] {code}: {e}")
        return 0


def collect_a_share_daily(code, days=365):
    code = code.strip()
    prefix = _get_a_share_prefix(code)
    try:
        import akshare as ak
        df = ak.stock_zh_a_daily(symbol=f"{prefix}{code}", adjust="qfq")
        if df is not None and len(df) > 0:
            df = df.tail(days).copy()
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)
            if 'turnover' in df.columns and 'turnover_rate' not in df.columns:
                df['turnover_rate'] = df['turnover'] * 100
            df['change_pct'] = (df['close'].pct_change() * 100).round(2)
            df['amplitude'] = ((df['high'] - df['low']) / df['close'].shift(1) * 100).round(2)
            _save_daily_quotes(df, code, "A股", data_source="akshare")
            return len(df)
    except Exception as e:
        print(f"  [collect_a_share_daily] {code}: {e}")
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
            df = df.rename(columns={'day': 'date'}).sort_values('date').reset_index(drop=True)
            df['change_pct'] = (df['close'].pct_change() * 100).round(2)
            df['amplitude'] = ((df['high'] - df['low']) / df['close'].shift(1) * 100).round(2)
            df = df.tail(days)
            _save_daily_quotes(df, code, "A股", data_source="sina")
            return len(df)
    except Exception as e:
        print(f"  [collect_a_share_daily/sina] {code}: {e}")
    return _yahoo_chart_daily(code, "A股", days)


def collect_us_daily(symbol, days=365):
    symbol = symbol.strip().upper()
    try:
        import akshare as ak
        df = ak.stock_us_daily(symbol=symbol, adjust="qfq")
        if df is not None and len(df) > 0:
            df = df.tail(days).copy()
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)
            df['change_pct'] = (df['close'].pct_change() * 100).round(2)
            df['amplitude'] = ((df['high'] - df['low']) / df['close'].shift(1) * 100).round(2)
            _save_daily_quotes(df, symbol, "美股", data_source="akshare")
            return len(df)
    except Exception as e:
        print(f"  [collect_us_daily] {symbol}: {e}")
    return _yahoo_chart_daily(symbol, "美股", days)


def collect_hk_daily(code, days=365):
    code = code.strip().zfill(5)
    try:
        import akshare as ak
        df = ak.stock_hk_daily(symbol=code, adjust="qfq")
        if df is not None and len(df) > 0:
            df = df.tail(days).copy()
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)
            df['change_pct'] = (df['close'].pct_change() * 100).round(2)
            df['amplitude'] = ((df['high'] - df['low']) / df['close'].shift(1) * 100).round(2)
            _save_daily_quotes(df, code, "港股", data_source="akshare")
            return len(df)
    except Exception as e:
        print(f"  [collect_hk_daily] {code}: {e}")
    return _yahoo_chart_daily(code, "港股", days)


def _save_daily_quotes(df, code, market, data_source="manual", replace=True):
    conn = get_db()
    count = 0
    sql = ("INSERT OR REPLACE INTO daily_quotes " if replace
           else "INSERT OR IGNORE INTO daily_quotes ")
    for _, row in df.iterrows():
        trade_date = row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date'])[:10]
        try:
            cur = conn.execute(
                sql +
                "(trade_date, code, market, name, open, high, low, close, volume, amount, "
                "change_pct, amplitude, turnover_rate, pe_ratio, pb_ratio, "
                "total_market_cap, circ_market_cap, net_inflow, data_source) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (trade_date, code, market, '',
                 float(row.get('open', 0)), float(row.get('high', 0)),
                 float(row.get('low', 0)), float(row.get('close', 0)),
                 int(row.get('volume', 0)), float(row.get('amount', 0)),
                 float(row.get('change_pct', 0)), float(row.get('amplitude', 0)),
                 float(row.get('turnover_rate', 0)), float(row.get('pe', 0)),
                 float(row.get('pb', 0)), float(row.get('total_mv', 0)),
                 float(row.get('circ_mv', 0)), float(row.get('net_inflow', 0)),
                 data_source)
            )
            count += max(cur.rowcount, 0)
        except Exception as e:
            print(f"  [_save_daily_quotes] {code} {trade_date}: {e}")
    conn.commit()
    conn.close()
    return count


def batch_collect_daily(stocks=None, days=365):
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
# 扩展数据采集: 估值/资金流/分红/财报
# ============================================================

# --- 美股数据源: Yahoo Finance (估值快照/历史分红/财报日期) ---
_yahoo_session = None
_yahoo_crumb = None
_yahoo_crumb_time = 0


def _yahoo_symbol(code):
    """Yahoo符号格式: BRK.B -> BRK-B"""
    return code.strip().upper().replace('.', '-')


def _get_yahoo_session():
    """获取带crumb的Yahoo Finance会话(缓存1小时, crumb约1天有效)"""
    global _yahoo_session, _yahoo_crumb, _yahoo_crumb_time
    import time as _time
    if _yahoo_session and _yahoo_crumb and (_time.time() - _yahoo_crumb_time) < 3600:
        return _yahoo_session, _yahoo_crumb
    sess = requests.Session()
    sess.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
    try:
        sess.get("https://fc.yahoo.com", timeout=10)
        crumb = sess.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10).text.strip()
        if crumb and 'Unauthorized' not in crumb and len(crumb) < 30:
            _yahoo_session, _yahoo_crumb, _yahoo_crumb_time = sess, crumb, _time.time()
            return sess, crumb
    except Exception as e:
        print(f"[_get_yahoo_session] {e}")
    return None, None


def fetch_us_valuation_yahoo(code):
    """Yahoo美股估值快照: PE(TTM)/PB/总市值/股本/股息率/下次财报日"""
    sess, crumb = _get_yahoo_session()
    if not sess:
        return {}
    try:
        r = sess.get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{_yahoo_symbol(code)}",
            params={"modules": "price,summaryDetail,defaultKeyStatistics,calendarEvents",
                    "crumb": crumb},
            timeout=12)
        if r.status_code != 200:
            return {}
        result = r.json().get('quoteSummary', {}).get('result', [])
        if not result:
            return {}
        d = result[0]
        price = d.get('price', {})
        sd = d.get('summaryDetail', {})
        ks = d.get('defaultKeyStatistics', {})
        cal = d.get('calendarEvents', {})

        cur_price = price.get('regularMarketPrice', {}).get('raw') or 0
        market_cap = price.get('marketCap', {}).get('raw') or 0
        pe = sd.get('trailingPE', {}).get('raw')
        pb = sd.get('priceToBook', {}).get('raw')
        if pb is None:
            bv = ks.get('bookValue', {}).get('raw')
            if bv and cur_price:
                pb = cur_price / bv
        shares = (ks.get('sharesOutstanding', {}).get('raw')
                  or price.get('sharesOutstanding', {}).get('raw') or 0)
        div_yield = sd.get('dividendYield', {}).get('raw') or 0
        if 0 < div_yield < 1:
            div_yield *= 100
        edates = cal.get('earnings', {}).get('earningsDate', [])
        next_earnings = edates[0].get('fmt') if edates else None
        return {
            'price': cur_price, 'pe': pe, 'pb': pb, 'market_cap': market_cap,
            'shares': shares, 'dividend_yield': round(div_yield, 4),
            'next_earnings': next_earnings,
        }
    except Exception as e:
        print(f"[fetch_us_valuation_yahoo] {code}: {e}")
        return {}


def fetch_us_dividends_yahoo(code, years=1):
    """Yahoo美股历史分红: [(除息日, 每股金额), ...]"""
    sess, _ = _get_yahoo_session()
    if not sess:
        return []
    try:
        r = sess.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{_yahoo_symbol(code)}",
            params={"range": f"{years}y", "interval": "1d", "events": "div"},
            timeout=12)
        if r.status_code != 200:
            return []
        result = r.json().get('chart', {}).get('result', [])
        if not result:
            return []
        divs = result[0].get('events', {}).get('dividends', {}) or {}
        out = []
        for k, v in divs.items():
            d = datetime.fromtimestamp(int(k)).strftime('%Y-%m-%d')
            out.append((d, v.get('amount', 0)))
        return sorted(out)
    except Exception as e:
        print(f"[fetch_us_dividends_yahoo] {code}: {e}")
        return []


def collect_us_valuation(code):
    """美股估值采集(Yahoo): 当前快照 + 历史回填
    历史值按价格比例估算: PE=close/EPS(当前), 市值=close*当前股本 (假设期间股本与盈利不变)
    """
    code = code.strip().upper()
    snap = fetch_us_valuation_yahoo(code)
    if not snap or not snap.get('price'):
        print(f"[collect_us_valuation] {code}: Yahoo无数据")
        return 0

    cur_price = snap['price']
    pe_now = snap.get('pe') or 0
    pb_now = snap.get('pb') or 0
    shares = snap.get('shares') or 0
    eps = cur_price / pe_now if pe_now else 0
    bvps = cur_price / pb_now if pb_now else 0

    conn = get_db()
    rows = conn.execute(
        "SELECT trade_date, close FROM daily_quotes WHERE code=? ORDER BY trade_date",
        (code,)).fetchall()
    count = 0
    for r in rows:
        close = r['close'] or 0
        if not close:
            continue
        pe_est = round(close / eps, 4) if eps else 0
        pb_est = round(close / bvps, 4) if bvps else 0
        mc_est = round(close * shares, 2) if shares else 0
        conn.execute(
            "UPDATE daily_quotes SET pe_ratio=?, pb_ratio=?, total_market_cap=?, "
            "circ_market_cap=? WHERE trade_date=? AND code=?",
            (pe_est, pb_est, mc_est, mc_est, r['trade_date'], code))
        count += 1
    conn.commit()
    conn.close()
    return count


def collect_us_events(code, name=""):
    """美股事件采集(Yahoo): 历史分红(除息日) → active_events
    (财报采集 2026-09-03 停用: Yahoo 仅提供"下次财报日"未来日期, 非历史披露日)"""
    code = code.strip().upper()
    total = 0
    conn = get_db()

    for d, amount in fetch_us_dividends_yahoo(code, years=1):
        exists = conn.execute(
            "SELECT COUNT(*) as c FROM active_events "
            "WHERE code=? AND trade_date=? AND event_type='dividend'",
            (code, d)).fetchone()['c']
        if exists:
            continue
        title = f"美股分红 每股${amount}"
        conn.execute(
            "INSERT INTO active_events "
            "(code, market, name, event_time, event_type, event_subtype, direction, "
            "impact_level, title, content, source, source_type, related_codes, trade_date) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, "美股", name, d + " 00:00:00", 'dividend', '分红', 'positive',
             'minor', title, f"每股分红 ${amount}", 'yahoo', 'auto', code, d))
        total += 1

    conn.commit()
    conn.close()
    return total


def collect_stock_valuation(code, market="A股"):
    """采集估值数据(PE/PB/市值)并更新 daily_quotes"""
    code = code.strip()
    try:
        import akshare as ak
        indicators = {
            "市盈率(TTM)": "pe_ratio",
            "市净率": "pb_ratio",
            "总市值": "total_market_cap",
            "流通市值": "circ_market_cap",
        }

        if market == "美股":
            return collect_us_valuation(code)

        merged = None
        for ak_name, col_name in indicators.items():
            try:
                if market == "A股":
                    df = ak.stock_zh_valuation_baidu(symbol=code, indicator=ak_name, period="近一年")
                elif market == "港股":
                    df = ak.stock_hk_valuation_baidu(symbol=code, indicator=ak_name, period="近一年")
                if df is not None and len(df) > 0:
                    df = df.rename(columns={'value': col_name})
                    if merged is None:
                        merged = df
                    else:
                        merged = pd.merge(merged, df[['date', col_name]], on='date', how='outer')
            except Exception as e:
                print(f"  [collect_stock_valuation/{ak_name}] {code} ({market}): {e}")
                continue

        if merged is not None and len(merged) > 0:
            _merge_valuation_into_quotes(merged, code, market)
            return len(merged)
    except Exception as e:
        print(f"[collect_stock_valuation] {code} ({market}): {e}")
    return 0


def _merge_valuation_into_quotes(df, code, market):
    """将估值数据合并到已存在的 daily_quotes 记录中"""
    conn = get_db()
    count = 0
    for _, row in df.iterrows():
        trade_date = row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date'])[:10]
        try:
            pe = float(row.get('pe_ratio', 0) or 0)
            pb = float(row.get('pb_ratio', 0) or 0)
            tmc = float(row.get('total_market_cap', 0) or 0)
            cmc = float(row.get('circ_market_cap', 0) or 0)
            if cmc == 0:
                cmc = tmc
            conn.execute(
                "UPDATE daily_quotes SET pe_ratio=?, pb_ratio=?, "
                "total_market_cap=?, circ_market_cap=? "
                "WHERE trade_date=? AND code=?",
                (pe, pb, tmc, cmc, trade_date, code)
            )
            count += 1
        except Exception as e:
            print(f"  [_merge_valuation] {code} {trade_date}: {e}")
    conn.commit()
    conn.close()
    return count


def get_valuation_summary():
    """获取池内股票的最新估值数据"""
    pool_f, pool_args = _pool_sql_filter(_pool_code_set())
    conn = get_db()
    rows = conn.execute(
        "SELECT q.code, q.market, q.name, q.close, q.pe_ratio, q.pb_ratio, "
        "q.total_market_cap, q.circ_market_cap, q.trade_date "
        "FROM daily_quotes q "
        "WHERE q.trade_date = (SELECT MAX(q2.trade_date) FROM daily_quotes q2 WHERE q2.code = q.code) "
        f"AND {pool_f} "
        "ORDER BY q.code",
        pool_args
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_valuation_history(code, days=365):
    """获取某只股票的估值历史数据"""
    code = code.strip().upper()
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    conn = get_db()
    rows = conn.execute(
        "SELECT trade_date, code, close, pe_ratio, pb_ratio, "
        "total_market_cap, circ_market_cap "
        "FROM daily_quotes WHERE code=? AND trade_date>=? "
        "AND (pe_ratio > 0 OR pb_ratio > 0) "
        "ORDER BY trade_date",
        (code, since)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def backfill_hk_turnover(code):
    """港股换手率回填: turnover = amount / circ_market_cap * 100
    依赖已采集的流通市值数据"""
    code = code.strip().upper()
    conn = get_db()
    rows = conn.execute(
        "SELECT trade_date, amount, circ_market_cap FROM daily_quotes "
        "WHERE code=? AND amount > 0 AND circ_market_cap > 0 "
        "AND (turnover_rate IS NULL OR turnover_rate = 0)",
        (code,)
    ).fetchall()
    count = 0
    for r in rows:
        tr = r['amount'] / (r['circ_market_cap'] * 1e8) * 100
        if 0 < tr < 100:
            conn.execute(
                "UPDATE daily_quotes SET turnover_rate=? "
                "WHERE trade_date=? AND code=?",
                (round(tr, 4), r['trade_date'], code)
            )
            count += 1
    conn.commit()
    conn.close()
    return count


def backfill_us_turnover(code):
    """美股换手率回填: turnover = volume / sharesOutstanding * 100
    股本取自Yahoo(假设期间不变, 与估值回填同假设)"""
    code = code.strip().upper()
    shares = None
    try:
        sess, crumb = _get_yahoo_session()
        if not sess:
            return 0
        resp = sess.get(
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{_yahoo_symbol(code)}",
            params={'modules': 'defaultKeyStatistics,summaryDetail',
                    'crumb': crumb},
            timeout=10)
        d = resp.json().get('quoteSummary', {}).get('result', [{}])[0]
        shares = (d.get('defaultKeyStatistics', {})
                  .get('sharesOutstanding', {}) or {}).get('raw')
    except Exception as e:
        print(f"[backfill_us_turnover] {code} yahoo: {e}")
        return 0
    if not shares:
        return 0

    conn = get_db()
    rows = conn.execute(
        "SELECT trade_date, volume FROM daily_quotes "
        "WHERE code=? AND volume > 0 "
        "AND (turnover_rate IS NULL OR turnover_rate = 0)",
        (code,)
    ).fetchall()
    count = 0
    for r in rows:
        tr = r['volume'] / shares * 100
        if 0 < tr < 100:
            conn.execute(
                "UPDATE daily_quotes SET turnover_rate=? "
                "WHERE trade_date=? AND code=?",
                (round(tr, 4), r['trade_date'], code)
            )
            count += 1
    conn.commit()
    conn.close()
    return count


def collect_capital_flow(code, market="A股"):
    """采集个股资金流向(主力净流入)并更新 daily_quotes"""
    code = code.strip()
    if market != "A股":
        return 0

    # 方案1: 新浪资金流历史接口 (主力源, 通用性强)
    try:
        import json as _json
        prefix = _get_a_share_prefix(code)
        all_rows = []
        for page in (1, 2):
            resp = _session.get(
                "https://vip.stock.finance.sina.com.cn/quotes_service/api/"
                "json_v2.php/MoneyFlow.ssl_qsfx_lscjfb",
                params={'page': page, 'num': '60', 'sort': 'opendate', 'asc': '0',
                        'daima': f"{prefix}{code}"},
                timeout=10,
                headers={'Referer': 'https://finance.sina.com.cn'})
            if resp.text.strip().startswith('['):
                all_rows += _json.loads(resp.text)
        if all_rows:
            conn = get_db()
            count = 0
            for row in all_rows:
                trade_date = str(row.get('opendate', ''))[:10]
                if not trade_date:
                    continue
                net_inflow = float(row.get('netamount', 0) or 0)
                conn.execute(
                    "UPDATE daily_quotes SET net_inflow=? "
                    "WHERE trade_date=? AND code=?",
                    (net_inflow, trade_date, code)
                )
                count += 1
            conn.commit()
            conn.close()
            return count
    except Exception as e:
        print(f"[collect_capital_flow] {code} sina: {e}")

    # 方案2: 东方财富直接 API (备用)
    try:
        import requests as _req
        secid = f"1.{code}" if code.startswith('6') else f"0.{code}"
        url = "https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get"
        params = {
            "secid": secid, "lmt": "0", "klt": "101",
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }
        resp = _req.get(url, params=params, timeout=10,
                       headers={"User-Agent": "Mozilla/5.0",
                               "Referer": "https://data.eastmoney.com/"})
        data = resp.json()
        klines = (data.get("data") or {}).get("klines") or []
        if klines:
            conn = get_db()
            count = 0
            for line in klines:
                parts = line.split(",")
                if len(parts) < 2:
                    continue
                trade_date = parts[0]
                net_inflow = float(parts[1] or 0)
                conn.execute(
                    "UPDATE daily_quotes SET net_inflow=? "
                    "WHERE trade_date=? AND code=?",
                    (net_inflow, trade_date, code)
                )
                count += 1
            conn.commit()
            conn.close()
            return count
    except Exception as e:
        print(f"[collect_capital_flow] {code} eastmoney direct: {e}")

    # 方案2: akshare API (备用)
    import time
    prefix = _get_a_share_prefix(code)
    for attempt in range(2):
        try:
            import akshare as ak
            df = ak.stock_individual_fund_flow(stock=code, market=prefix)
            if df is not None and len(df) > 0:
                df = df.tail(365).copy()
                conn = get_db()
                count = 0
                for _, row in df.iterrows():
                    trade_date = str(row.get('日期', ''))[:10]
                    if not trade_date:
                        continue
                    net_inflow = float(row.get('主力净流入-净额', 0) or 0)
                    conn.execute(
                        "UPDATE daily_quotes SET net_inflow=? "
                        "WHERE trade_date=? AND code=?",
                        (net_inflow, trade_date, code)
                    )
                    count += 1
                conn.commit()
                conn.close()
                return count
        except Exception as e:
            print(f"[collect_capital_flow] {code} akshare attempt {attempt+1}: {e}")
            if attempt < 1:
                time.sleep(1)
    return 0


def collect_dividend_events(code, market="A股", name=""):
    """采集分红送转记录 → active_events"""
    code = code.strip()
    try:
        import akshare as ak

        if market == "A股":
            df = ak.stock_history_dividend_detail(symbol=code)
            if df is not None and len(df) > 0:
                conn = get_db()
                count = 0
                for _, row in df.iterrows():
                    ev_time = str(row.get('公告日期', '') or row.get('除权除息日', '') or '')[:10]
                    if not ev_time:
                        continue
                    title = f"分红 送{row.get('送股',0)}转{row.get('转增',0)}派{row.get('派息',0)} ({row.get('进度','')})"
                    conn.execute(
                        "INSERT OR REPLACE INTO active_events "
                        "(code, market, name, event_time, event_type, event_subtype, "
                        "direction, impact_level, title, content, source, source_type, "
                        "related_codes, trade_date) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (code, market, name, ev_time + " 00:00:00",
                         'dividend', '分红送转', 'positive', 'minor',
                         title, str(row.to_dict()), 'akshare', 'auto',
                         code, ev_time)
                    )
                    count += 1
                conn.commit()
                conn.close()
                return count
        elif market == "港股":
            df = ak.stock_hk_dividend_payout_em(symbol=code)
            if df is not None and len(df) > 0:
                conn = get_db()
                count = 0
                for _, row in df.iterrows():
                    ev_time = str(row.get('最新公告日期', '') or row.get('除净日', '') or '')[:10]
                    if not ev_time:
                        continue
                    title = f"港股分红 {row.get('分红方案', '')} ({row.get('分配类型', '')})"
                    conn.execute(
                        "INSERT OR REPLACE INTO active_events "
                        "(code, market, name, event_time, event_type, event_subtype, "
                        "direction, impact_level, title, content, source, source_type, "
                        "related_codes, trade_date) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (code, market, name, ev_time + " 00:00:00",
                         'dividend', '分红送转', 'positive', 'minor',
                         title, str(row.to_dict()), 'akshare', 'auto',
                         code, ev_time)
                    )
                    count += 1
                conn.commit()
                conn.close()
                return count
        else:
            return 0
    except Exception as e:
        print(f"[collect_dividend_events] {code} ({market}): {e}")
    return 0


def batch_collect_valuation(stocks=None):
    if stocks is None:
        stocks = get_stock_pool(active_only=True)
    results = []
    for s in stocks:
        code = s['code']
        market = s.get('market', 'A股')
        n = collect_stock_valuation(code, market)
        results.append({'code': code, 'market': market, 'rows': n})
    return results


def batch_collect_capital_flow(stocks=None):
    """批量采集资金流(A股) + 回填港美股换手率"""
    if stocks is None:
        stocks = get_stock_pool(active_only=True)
    results = []
    for s in stocks:
        code = s['code']
        market = s.get('market', 'A股')
        n = collect_capital_flow(code, market)
        entry = {'code': code, 'market': market, 'rows': n}
        if market == "港股":
            entry['turnover_backfilled'] = backfill_hk_turnover(code)
        elif market == "美股":
            entry['turnover_backfilled'] = backfill_us_turnover(code)
        results.append(entry)
    return results


def batch_collect_events(stocks=None):
    """批量采集主动事件 — 仅分红 (财报采集 2026-09-03 停用:
    港股源 REPORT_DATE 为会计年度期末而非披露日, 详见 模块6开发计划.md 问题日志#10)"""
    if stocks is None:
        stocks = get_stock_pool(active_only=True)
    results = []
    for s in stocks:
        code = s['code']
        market = s.get('market', 'A股')
        name = s.get('name', '')
        if market == "美股":
            n = collect_us_events(code, name)
            results.append({'code': code, 'dividends': n,
                            'source': 'yahoo'})
        else:
            d = collect_dividend_events(code, market, name)
            results.append({'code': code, 'dividends': d})
    return results


def get_data_type_status():
    """返回各数据类型的采集状态，用于看板展示"""
    conn = get_db()
    status = {}

    # 行情数据 (daily_quotes, 仅当前池)
    pool_f, pool_args = _pool_sql_filter(_pool_code_set())
    total = conn.execute(
        f"SELECT COUNT(*) as c FROM daily_quotes WHERE {pool_f}", pool_args).fetchone()['c']
    has_ohlc = conn.execute(
        f"SELECT COUNT(*) as c FROM daily_quotes WHERE open > 0 AND close > 0 AND {pool_f}",
        pool_args).fetchone()['c']
    has_valuation = conn.execute(
        f"SELECT COUNT(*) as c FROM daily_quotes WHERE (pe_ratio > 0 OR pb_ratio > 0) AND {pool_f}",
        pool_args).fetchone()['c']
    has_capital = conn.execute(
        f"SELECT COUNT(*) as c FROM daily_quotes WHERE net_inflow != 0 AND {pool_f}",
        pool_args).fetchone()['c']
    has_turnover = conn.execute(
        f"SELECT COUNT(*) as c FROM daily_quotes WHERE turnover_rate > 0 AND {pool_f}",
        pool_args).fetchone()['c']
    status['quotes_ohlc'] = {'label': '日线OHLCV', 'count': has_ohlc, 'total': total,
                              'table': 'daily_quotes', 'desc': '开盘/最高/最低/收盘/成交量/成交额'}
    status['quotes_valuation'] = {'label': '估值数据', 'count': has_valuation, 'total': total,
                                   'table': 'daily_quotes', 'desc': 'PE/PB/总市值/流通市值'}
    status['quotes_capital'] = {'label': '资金流向', 'count': has_capital, 'total': total,
                                 'table': 'daily_quotes', 'desc': '主力净流入金额'}
    status['quotes_turnover'] = {'label': '换手率', 'count': has_turnover, 'total': total,
                                  'table': 'daily_quotes', 'desc': '每日换手率'}

    # 被动信号 (passive_signals, 仅当前池)
    sig_total = conn.execute(
        f"SELECT COUNT(*) as c FROM passive_signals WHERE {pool_f}", pool_args).fetchone()['c']
    sig_types = conn.execute(
        f"SELECT signal_type, COUNT(*) as c FROM passive_signals WHERE {pool_f} GROUP BY signal_type",
        pool_args).fetchall()
    status['signals'] = {'label': '被动信号', 'count': sig_total, 'total': sig_total,
                          'table': 'passive_signals', 'desc': 'MACD/RSI/KDJ/BOLL/量价异动',
                          'by_type': [dict(r) for r in sig_types]}

    # 主动事件 (active_events, 仅当前池)
    evt_total = conn.execute(
        f"SELECT COUNT(*) as c FROM active_events WHERE {pool_f}", pool_args).fetchone()['c']
    evt_auto = conn.execute(
        f"SELECT COUNT(*) as c FROM active_events WHERE source_type='auto' AND {pool_f}",
        pool_args).fetchone()['c']
    evt_manual = conn.execute(
        f"SELECT COUNT(*) as c FROM active_events WHERE (source_type='manual' OR source_type IS NULL "
        f"OR source_type='') AND {pool_f}", pool_args).fetchone()['c']
    evt_types = conn.execute(
        f"SELECT event_type, COUNT(*) as c FROM active_events WHERE {pool_f} GROUP BY event_type",
        pool_args).fetchall()
    status['events_auto'] = {'label': '自动事件(分红)', 'count': evt_auto, 'total': evt_total,
                              'table': 'active_events', 'desc': 'akshare/yahoo自动抓取的分红事件(财报采集已停用)'}
    status['events_manual'] = {'label': '手动事件', 'count': evt_manual, 'total': evt_total,
                                'table': 'active_events', 'desc': '手动录入的政策/公告/行业新闻',
                                'by_type': [dict(r) for r in evt_types]}

    conn.close()
    return status


# ============================================================
# 技术指标计算
# ============================================================

# --- 模块2: 基础技术因子 (11类指标/26列, 全部滚动计算, 无未来函数) ---
FACTOR_COLS = [
    'ma5', 'ma10', 'ma20', 'ma60',
    'dif', 'dea', 'macd_bar',
    'rsi14',
    'kdj_k', 'kdj_d', 'kdj_j',
    'boll_up', 'boll_mid', 'boll_low',
    'volatility20', 'atr14',
    'vol_ma5', 'vol_ma20', 'obv',
    'change_pct', 'ret_3d', 'ret_5d', 'ret_20d',
]


def calc_factors(df):
    """模块2因子计算: 输入含 date/open/high/low/close/volume 的DataFrame
    规则: 前复权价格、滚动窗口逐天计算、初始窗口期保留NaN
    """
    df = df.copy()
    df = df.sort_values('date').reset_index(drop=True)
    close = df['close']
    high = df['high']
    low = df['low']
    vol = df['volume']

    # --- 趋势类: 5/10/20/60日均线 ---
    for p in [5, 10, 20, 60]:
        df[f'ma{p}'] = close.rolling(window=p).mean()

    # --- 趋势类: MACD(12,26,9) ---
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df['dif'] = ema12 - ema26
    df['dea'] = df['dif'].ewm(span=9, adjust=False).mean()
    df['macd_bar'] = (df['dif'] - df['dea']) * 2

    # --- 震荡类: RSI(14, Wilder平滑) ---
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df['rsi14'] = 100 - 100 / (1 + rs)

    # --- 震荡类: KDJ(9,3,3) ---
    low9 = low.rolling(window=9).min()
    high9 = high.rolling(window=9).max()
    rsv = (close - low9) / (high9 - low9).replace(0, np.nan) * 100
    rsv = rsv.fillna(50)
    df['kdj_k'] = rsv.ewm(com=2, adjust=False).mean()
    df['kdj_d'] = df['kdj_k'].ewm(com=2, adjust=False).mean()
    df['kdj_j'] = 3 * df['kdj_k'] - 2 * df['kdj_d']

    # --- 震荡类: 布林带(20, 2σ) ---
    boll_mid = close.rolling(window=20).mean()
    boll_std = close.rolling(window=20).std()
    df['boll_mid'] = boll_mid
    df['boll_up'] = boll_mid + 2 * boll_std
    df['boll_low'] = boll_mid - 2 * boll_std

    # --- 波动类: 20日收益率波动率(%) / ATR(14) ---
    daily_ret = close.pct_change()
    df['volatility20'] = daily_ret.rolling(window=20).std() * 100
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    df['atr14'] = tr.rolling(window=14).mean()

    # --- 量能类: 5/20日均量 / OBV能量潮 ---
    df['vol_ma5'] = vol.rolling(window=5).mean()
    df['vol_ma20'] = vol.rolling(window=20).mean()
    direction = np.sign(close.diff()).fillna(0)
    df['obv'] = (direction * vol).cumsum()

    # --- 收益类: 当日/过去3/5/20日累计涨跌幅(%) ---
    df['change_pct'] = (close.pct_change() * 100).round(4)
    for n in [3, 5, 20]:
        df[f'ret_{n}d'] = (close.pct_change(periods=n) * 100).round(4)

    return df


def generate_indicators(code):
    """计算单只股票因子 → daily_indicators 表 (前复权日线来自daily_quotes)"""
    code = code.strip()
    conn = get_db()
    rows = conn.execute(
        "SELECT trade_date, code, market, open, high, low, close, volume "
        "FROM daily_quotes WHERE code=? ORDER BY trade_date",
        (code,)
    ).fetchall()
    conn.close()
    if not rows:
        return 0

    df = pd.DataFrame([dict(r) for r in rows])
    market = df['market'].iloc[0] if 'market' in df.columns else None
    df = df.rename(columns={'trade_date': 'date'})
    df['date'] = pd.to_datetime(df['date'])
    out = calc_factors(df)

    conn = get_db()
    count = 0
    for row in out.itertuples(index=False):
        trade_date = pd.Timestamp(row.date).strftime('%Y-%m-%d')
        vals = []
        for c in FACTOR_COLS:
            v = getattr(row, c)
            vals.append(None if pd.isna(v) else float(v))
        conn.execute(
            "INSERT OR REPLACE INTO daily_indicators "
            "(code, trade_date, market, " + ",".join(FACTOR_COLS) + ") "
            "VALUES(?,?,?," + ",".join(["?"] * len(FACTOR_COLS)) + ")",
            (code, trade_date, market, *vals)
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def batch_generate_indicators(stocks=None):
    """批量计算因子 (默认仅当前活跃池)"""
    if stocks is None:
        stocks = get_stock_pool(active_only=True)
    results = []
    for s in stocks:
        n = generate_indicators(s['code'])
        results.append({'code': s['code'], 'market': s.get('market', ''), 'rows': n})
    return results


def get_indicators(code, days=365):
    """查询因子数据 (含行情), 用于可视化"""
    code = code.strip()
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    conn = get_db()
    rows = conn.execute(
        "SELECT i.trade_date, i.code, i.market, q.open, q.high, q.low, q.close, "
        "q.volume, q.turnover_rate, " + ",".join("i." + c for c in FACTOR_COLS) + " "
        "FROM daily_indicators i "
        "LEFT JOIN daily_quotes q ON q.code=i.code AND q.trade_date=i.trade_date "
        "WHERE i.code=? AND i.trade_date>=? ORDER BY i.trade_date",
        (code, since)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_indicator_stats():
    """各股票因子覆盖统计"""
    conn = get_db()
    pool_f, pool_args = _pool_sql_filter(_pool_code_set())
    rows = conn.execute(
        f"SELECT code, market, COUNT(*) as total, "
        "MIN(trade_date) as min_date, MAX(trade_date) as max_date, "
        "SUM(CASE WHEN ma60 IS NOT NULL THEN 1 ELSE 0 END) as ma60_ok, "
        "SUM(CASE WHEN rsi14 IS NOT NULL THEN 1 ELSE 0 END) as rsi14_ok, "
        "SUM(CASE WHEN boll_up IS NOT NULL THEN 1 ELSE 0 END) as boll_ok, "
        "SUM(CASE WHEN obv IS NOT NULL THEN 1 ELSE 0 END) as obv_ok "
        f"FROM daily_indicators WHERE {pool_f} GROUP BY code, market ORDER BY code",
        pool_args
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================
# 模块3: 埋点信号历史胜率与收益统计
# ============================================================

HOLD_PERIODS = [1, 3, 5, 10]


def _load_price_map():
    """加载当前池股票 (code -> {date: close}) 用于未来N日收益计算"""
    pool_f, pool_args = _pool_sql_filter(_pool_code_set())
    conn = get_db()
    rows = conn.execute(
        f"SELECT code, trade_date, close FROM daily_quotes WHERE {pool_f} "
        "ORDER BY code, trade_date", pool_args
    ).fetchall()
    conn.close()
    price_map = {}
    for r in rows:
        price_map.setdefault(r['code'], {})[r['trade_date']] = r['close']
    # 每只股票的有序交易日列表, 用于取未来第N个交易日
    date_map = {code: sorted(d.keys()) for code, d in price_map.items()}
    return price_map, date_map


def _future_return(price_map, date_map, code, trade_date, n_days):
    """未来N日涨跌幅 = (未来第N交易日收盘 - 信号日收盘) / 信号日收盘
    信号日或未来日不在行情中(停牌/数据缺失/超出末尾)返回None"""
    if code not in price_map:
        return None
    dates = date_map[code]
    if trade_date not in price_map[code]:
        return None
    try:
        idx = dates.index(trade_date)
    except ValueError:
        return None
    if idx + n_days >= len(dates):
        return None
    base = price_map[code][trade_date]
    future = price_map[code][dates[idx + n_days]]
    if not base:
        return None
    return (future - base) / base * 100


def _win_stats(returns):
    """核心统计: 触发数/上涨数/胜率/平均/盈亏平均/盈亏比/最大盈亏"""
    rets = [r for r in returns if r is not None]
    n = len(rets)
    if n == 0:
        return None
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    win_rate = len(wins) / n * 100
    avg_ret = sum(rets) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    profit_loss_ratio = round(avg_win / avg_loss, 3) if avg_loss > 0 else None
    return {
        'triggers': n, 'win_count': len(wins), 'win_rate': round(win_rate, 2),
        'avg_return': round(avg_ret, 4),
        'avg_win': round(avg_win, 4), 'avg_loss': round(avg_loss, 4),
        'profit_loss_ratio': profit_loss_ratio,
        'max_win': round(max(rets), 4), 'max_loss': round(min(rets), 4),
    }


def _random_baseline(price_map, date_map, n_days, exclude_last_n=None):
    """随机基准: 全量交易日买入持有N日的胜率与平均收益"""
    return _win_stats(_baseline_returns(price_map, date_map, n_days))


def _baseline_returns(price_map, date_map, n_days):
    """随机基准原始收益序列"""
    returns = []
    for code, dates in date_map.items():
        n_len = len(dates)
        for i in range(n_len - n_days):
            base = price_map[code][dates[i]]
            future = price_map[code][dates[i + n_days]]
            if base:
                returns.append((future - base) / base * 100)
    return returns


def compute_random_baselines(reverse=False):
    """计算4个持有周期的随机基准
    reverse=True: 反向口径基准(bearish信号对照用) — 上涨收益取负后统计,
    胜率含义变为'随机下跌率', 与反向评估的bearish信号同口径可比"""
    price_map, date_map = _load_price_map()
    out = {}
    for n in HOLD_PERIODS:
        rets = _baseline_returns(price_map, date_map, n)
        if reverse:
            rets = [-r for r in rets]
        out[n] = _win_stats(rets)
    return out


def compute_passive_signal_stats(source=None):
    """被动埋点统计: 按 信号类型|子类型|方向 分组 × 4持有周期
    附随机基准对比 → 超额胜率/超额收益
    bearish 信号采用反向评估口径: 未来收益取负后统计,
    胜率=看跌正确率(下跌次数/总次数), 平均收益=平均跌幅,
    对照基准为反向口径随机基准(随机下跌率)
    source: None=合并口径 / 'live'=仅实采 / 'replay'=仅回放 (模块8双口径)"""
    price_map, date_map = _load_price_map()
    baselines = compute_random_baselines()
    rev_baselines = compute_random_baselines(reverse=True)

    pool_f, pool_args = _pool_sql_filter(_pool_code_set())
    conn = get_db()
    src_sql = " AND source=? " if source else " "
    sig_rows = conn.execute(
        "SELECT code, market, trade_date, signal_type, signal_subtype, direction "
        f"FROM passive_signals WHERE {pool_f}{src_sql}", pool_args + ([source] if source else [])
    ).fetchall()
    conn.close()

    groups = {}
    for s in sig_rows:
        key = (s['signal_type'], s['signal_subtype'], s['direction'])
        groups.setdefault(key, []).append(s)

    results = []
    for (sig_type, subtype, direction), sigs in groups.items():
        reverse = (direction == 'bearish')
        stats = {}
        for n in HOLD_PERIODS:
            rets = [_future_return(price_map, date_map, s['code'], s['trade_date'], n)
                    for s in sigs]
            if reverse:
                rets = [-r if r is not None else None for r in rets]
            st = _win_stats(rets)
            if st is None:
                continue
            base = (rev_baselines if reverse else baselines).get(n)
            if base:
                st['excess_win_rate'] = round(st['win_rate'] - base['win_rate'], 2)
                st['excess_return'] = round(st['avg_return'] - base['avg_return'], 4)
                st['baseline_win_rate'] = base['win_rate']
                st['baseline_return'] = base['avg_return']
            st['eval_mode'] = 'reverse' if reverse else 'normal'
            stats[n] = st
        if stats:
            results.append({
                'signal_type': sig_type, 'signal_subtype': subtype,
                'direction': direction, 'total_signals': len(sigs),
                'stats': stats,
            })
    results.sort(key=lambda x: -x['total_signals'])
    return results, baselines


def compute_active_event_stats():
    """主动埋点统计: 事件类型×方向×影响等级 三维交叉 + 盘前/盘中/盘后分组
    bearish(利空)事件采用反向评估口径: 收益取负统计, 胜率=看跌正确率"""
    price_map, date_map = _load_price_map()
    baselines = compute_random_baselines()
    rev_baselines = compute_random_baselines(reverse=True)

    pool_f, pool_args = _pool_sql_filter(_pool_code_set())
    conn = get_db()
    evt_rows = conn.execute(
        "SELECT code, market, event_time, trade_date, event_type, direction, impact_level "
        f"FROM active_events WHERE {pool_f}", pool_args
    ).fetchall()
    conn.close()

    def _period(t):
        # 'YYYY-MM-DD HH:MM:SS' 取时间部分
        hhmm = (t or '') [11:16]
        if hhmm < '09:30':
            return '盘前'
        elif hhmm < '15:00':
            return '盘中'
        return '盘后'

    dim_groups = {}
    time_groups = {}
    for e in evt_rows:
        key = (e['event_type'], e['direction'], e['impact_level'])
        dim_groups.setdefault(key, []).append(e)
        tkey = (e['event_type'], _period(e['event_time'] or e['trade_date']))
        time_groups.setdefault(tkey, []).append(e)

    def _group_results(groups):
        out = []
        for key, evts in groups.items():
            reverse = (key[1] == 'bearish')
            stats = {}
            for n in HOLD_PERIODS:
                # 事件归属交易日: 盘后事件按下一交易日对齐(wide_table同规则)
                # 此处 trade_date 字段入库时已按该规则映射
                rets = [_future_return(price_map, date_map, e['code'], e['trade_date'], n)
                        for e in evts]
                if reverse:
                    rets = [-r if r is not None else None for r in rets]
                st = _win_stats(rets)
                if st is None:
                    continue
                base = (rev_baselines if reverse else baselines).get(n)
                if base:
                    st['excess_win_rate'] = round(st['win_rate'] - base['win_rate'], 2)
                    st['excess_return'] = round(st['avg_return'] - base['avg_return'], 4)
                    st['baseline_win_rate'] = base['win_rate']
                    st['baseline_return'] = base['avg_return']
                st['eval_mode'] = 'reverse' if reverse else 'normal'
                stats[n] = st
            if stats:
                out.append({'key': key, 'total_events': len(evts), 'stats': stats,
                            'eval_mode': 'reverse' if reverse else 'normal'})
        out.sort(key=lambda x: -x['total_events'])
        return out

    return {
        'by_dimension': _group_results(dim_groups),
        'by_time': _group_results(time_groups),
        'baselines': baselines,
    }


# 有效信号池入选标准
POOL_CRITERIA = {'win_rate': 55.0, 'profit_loss_ratio': 1.2, 'min_triggers': 30}


def build_effective_signal_pool(passive_stats, rsi24_verdict=None):
    """筛选核心有效信号池:
    胜率>55% 且 盈亏比>1.2 且 触发样本≥30 (任一持有周期达标即入选)
    小样本(<30)单独标记为'参考'
    rsi24_verdict: validate_rsi24_timesplit() 结果(模块8预注册规则),
    转正→'有效(时间分割通过)', 淘汰→'淘汰(时间分割未过)', 其余维持观察"""
    pool = []
    for r in passive_stats:
        best = None
        for n, st in r['stats'].items():
            ok = (st['win_rate'] > POOL_CRITERIA['win_rate']
                  and st['profit_loss_ratio'] is not None
                  and st['profit_loss_ratio'] > POOL_CRITERIA['profit_loss_ratio']
                  and st['triggers'] >= POOL_CRITERIA['min_triggers'])
            if ok and (best is None or st['win_rate'] > best[1]['win_rate']):
                best = (n, st)
        entry = {
            'signal_type': r['signal_type'], 'signal_subtype': r['signal_subtype'],
            'direction': r['direction'], 'total_signals': r['total_signals'],
            'eval_mode': 'reverse' if r['direction'] == 'bearish' else 'normal',
            'status': '参考(小样本)' if r['total_signals'] < POOL_CRITERIA['min_triggers'] else '候选',
        }
        if best:
            n, st = best
            entry.update({
                'status': '有效',
                'best_period': f'{n}日',
                'best_win_rate': st['win_rate'],
                'best_pl_ratio': st['profit_loss_ratio'],
                'best_avg_return': st['avg_return'],
                'excess_win_rate': st.get('excess_win_rate'),
                'excess_return': st.get('excess_return'),
            })
        # RSI24 观察信号的状态由预注册时间分割规则裁决(模块8)
        if r['signal_type'].startswith('rsi24'):
            v = (rsi24_verdict or {}).get('signals', {}).get(r['signal_type'])
            if v and v.get('verdict') == '转正' and entry['status'] == '有效':
                entry['status'] = '有效(时间分割通过)'
            elif v and v.get('verdict') == '淘汰':
                entry['status'] = '淘汰(时间分割未过)'
            elif entry['status'] == '有效':
                entry['status'] = '观察(验证中)'
        pool.append(entry)
    pool.sort(key=lambda x: (x['status'] != '有效' and x['status'] != '有效(时间分割通过)',
                             -x.get('total_signals', 0)))
    return pool


def save_signal_effect_report(passive_by_caliber, event_stats, pool):
    """《埋点信号效果总表》→ signal_effect_report 表
    模块8: 被动信号按三口径入库({'合并'/'replay'/'live': stats}), 主动事件仅'合并'口径"""
    conn = get_db()
    conn.execute("DROP TABLE IF EXISTS signal_effect_report")
    conn.execute("""
        CREATE TABLE signal_effect_report(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            group_key TEXT NOT NULL,
            caliber TEXT DEFAULT '合并',
            direction TEXT,
            eval_mode TEXT,
            total_count INTEGER,
            hold_days INTEGER,
            triggers INTEGER,
            win_rate REAL,
            baseline_win_rate REAL,
            excess_win_rate REAL,
            avg_return REAL,
            baseline_return REAL,
            excess_return REAL,
            avg_win REAL,
            avg_loss REAL,
            profit_loss_ratio REAL,
            max_win REAL,
            max_loss REAL,
            status TEXT,
            generated_at TEXT
        )
    """)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for caliber, passive_stats in passive_by_caliber.items():
        for r in passive_stats:
            gk = f"{r['signal_type']}|{r['signal_subtype']}"
            status = ('参考(小样本)'
                      if r['total_signals'] < POOL_CRITERIA['min_triggers'] else '')
            pool_st = next((p for p in pool if p['signal_type'] == r['signal_type']
                            and p['signal_subtype'] == r['signal_subtype']
                            and p['direction'] == r['direction']), {})
            # 分口径行的状态沿用合并口径池的判定, 但'有效'级降为'对照'——
            # 资格判定只以合并口径为准, 分口径行仅供来源对照
            cal_status = pool_st.get('status', status)
            if caliber != '合并' and cal_status in ('有效', '有效(时间分割通过)'):
                cal_status = '对照(合并口径有效)'
            for n, st in r['stats'].items():
                conn.execute(
                    "INSERT INTO signal_effect_report "
                    "(category, group_key, caliber, direction, eval_mode, total_count, "
                    "hold_days, triggers, win_rate, baseline_win_rate, excess_win_rate, "
                    "avg_return, baseline_return, excess_return, avg_win, avg_loss, "
                    "profit_loss_ratio, max_win, max_loss, status, generated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ('passive', gk, caliber, r['direction'], st.get('eval_mode', 'normal'),
                     r['total_signals'], n,
                     st['triggers'], st['win_rate'], st.get('baseline_win_rate'),
                     st.get('excess_win_rate'), st['avg_return'],
                     st.get('baseline_return'), st.get('excess_return'),
                     st['avg_win'], st['avg_loss'], st['profit_loss_ratio'],
                     st['max_win'], st['max_loss'], cal_status, now)
                )

    for r in event_stats['by_dimension']:
        gk = '|'.join(str(k) for k in r['key'])
        # 状态阶梯与被动信号一致: 小样本→参考, 任一周期达标→有效, 其余→候选
        best_ev = None
        for n, st in r['stats'].items():
            ok = (st['win_rate'] > POOL_CRITERIA['win_rate']
                  and st['profit_loss_ratio'] is not None
                  and st['profit_loss_ratio'] > POOL_CRITERIA['profit_loss_ratio']
                  and st['triggers'] >= POOL_CRITERIA['min_triggers'])
            if ok and (best_ev is None or st['win_rate'] > best_ev[1]['win_rate']):
                best_ev = (n, st)
        ev_status = ('参考(小样本)'
                     if r['total_events'] < POOL_CRITERIA['min_triggers'] else '候选')
        if best_ev:
            ev_status = '有效'
        for n, st in r['stats'].items():
            conn.execute(
                "INSERT INTO signal_effect_report "
                "(category, group_key, caliber, direction, eval_mode, total_count, "
                "hold_days, triggers, win_rate, baseline_win_rate, excess_win_rate, "
                "avg_return, baseline_return, excess_return, avg_win, avg_loss, "
                "profit_loss_ratio, max_win, max_loss, status, generated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ('active', gk, '合并', r['key'][1], st.get('eval_mode', 'normal'),
                 r['total_events'], n,
                 st['triggers'], st['win_rate'], st.get('baseline_win_rate'),
                 st.get('excess_win_rate'), st['avg_return'],
                 st.get('baseline_return'), st.get('excess_return'),
                 st['avg_win'], st['avg_loss'], st['profit_loss_ratio'],
                 st['max_win'], st['max_loss'], ev_status, now)
            )

    conn.commit()
    conn.close()


def run_module3_analysis():
    """模块3完整执行入口: 被动统计(三口径)+主动统计+有效池+RSI24时间分割+入库"""
    passive_by_cal = {}
    baselines = None
    for cal, src in [('合并', None), ('replay', 'replay'), ('live', 'live')]:
        stats, baselines = compute_passive_signal_stats(source=src)
        passive_by_cal[cal] = stats
    event_stats = compute_active_event_stats()
    rsi24_verdict = validate_rsi24_timesplit()
    pool = build_effective_signal_pool(passive_by_cal['合并'], rsi24_verdict)
    save_signal_effect_report(passive_by_cal, event_stats, pool)
    return {
        'passive': passive_by_cal['合并'],
        'passive_by_caliber': passive_by_cal,
        'events': event_stats,
        'pool': pool,
        'baselines': baselines,
        'rev_baselines': compute_random_baselines(reverse=True),
        'rsi24_verdict': rsi24_verdict,
    }


def compute_per_stock_signal_stats():
    """按股票分组统计: 同类信号在不同股票上的表现差异
    返回 {code: {signal|subtype|direction: {周期: stats}}} + 每股随机基准"""
    price_map, date_map = _load_price_map()
    pool_f, pool_args = _pool_sql_filter(_pool_code_set())
    conn = get_db()
    sig_rows = conn.execute(
        "SELECT code, market, trade_date, signal_type, signal_subtype, direction "
        f"FROM passive_signals WHERE {pool_f}", pool_args
    ).fetchall()
    conn.close()

    # 每股随机基准 (正向 + 反向口径, bearish 信号对照反向基准)
    stock_baselines = {}
    stock_rev_baselines = {}
    for code in date_map:
        dates = date_map[code]
        stock_baselines[code] = {
            n: _random_baseline({code: price_map[code]}, {code: dates}, n)
            for n in HOLD_PERIODS
        }
        stock_rev_baselines[code] = {
            n: _win_stats([-r for r in _baseline_returns(
                {code: price_map[code]}, {code: dates}, n)])
            for n in HOLD_PERIODS
        }

    # code -> key -> sigs
    by_stock = {}
    for s in sig_rows:
        key = (s['signal_type'], s['signal_subtype'], s['direction'])
        by_stock.setdefault(s['code'], {}).setdefault(key, []).append(s)

    results = {}
    for code, key_groups in by_stock.items():
        code_res = {}
        for (sig_type, subtype, direction), sigs in key_groups.items():
            reverse = (direction == 'bearish')
            stats = {}
            for n in HOLD_PERIODS:
                rets = [_future_return(price_map, date_map, s['code'], s['trade_date'], n)
                        for s in sigs]
                if reverse:
                    rets = [-r if r is not None else None for r in rets]
                st = _win_stats(rets)
                if st is None:
                    continue
                base = (stock_rev_baselines if reverse else stock_baselines).get(code, {}).get(n)
                if base:
                    st['excess_win_rate'] = round(st['win_rate'] - base['win_rate'], 2)
                    st['excess_return'] = round(st['avg_return'] - base['avg_return'], 4)
                    st['baseline_win_rate'] = base['win_rate']
                    st['baseline_return'] = base['avg_return']
                st['eval_mode'] = 'reverse' if reverse else 'normal'
                stats[n] = st
            if stats:
                code_res[(sig_type, subtype, direction)] = stats
        if code_res:
            results[code] = code_res
    return results, stock_baselines, stock_rev_baselines


def compute_portfolio_profile():
    """投资组合画像: 股票池构成 + 各股趋势特征 + 样本贡献占比"""
    conn = get_db()
    pool = conn.execute(
        "SELECT code, market, name, sector FROM stock_pool WHERE is_active=1").fetchall()
    quotes = conn.execute(
        "SELECT code, trade_date, close FROM daily_quotes "
        "ORDER BY code, trade_date").fetchall()
    sig_counts = conn.execute(
        "SELECT code, COUNT(*) n FROM passive_signals GROUP BY code").fetchall()
    conn.close()
    # 仅统计当前池(quotes 过滤; 池内 code 才进画像, 信号占比分母同口径)
    pool_codes = {p['code'] for p in pool}
    quotes = [q for q in quotes if q['code'] in pool_codes]
    sig_counts = [r for r in sig_counts if r['code'] in pool_codes]

    by_code = {}
    for q in quotes:
        by_code.setdefault(q['code'], []).append((q['trade_date'], q['close']))

    sig_map = {r['code']: r['n'] for r in sig_counts}
    total_sig = sum(sig_map.values()) or 1
    pool_map = {p['code']: dict(p) for p in pool}

    profiles = []
    for code, series in sorted(by_code.items()):
        closes = [c for _, c in series]
        n = len(closes)
        if n < 2 or not closes[0]:
            continue
        first, last = closes[0], closes[-1]
        total_ret = (last - first) / first * 100
        ann = ((last / first) ** (252 / max(n - 1, 1)) - 1) * 100
        rets = np.diff(closes) / closes[:-1]
        vol = float(np.std(rets) * (252 ** 0.5) * 100) if len(rets) > 1 else 0.0
        if ann > 15:
            trend = '强势上涨'
        elif ann > 5:
            trend = '温和上涨'
        elif ann > -5:
            trend = '震荡'
        elif ann > -15:
            trend = '温和下跌'
        else:
            trend = '强势下跌'
        p = pool_map.get(code, {})
        profiles.append({
            'code': code, 'market': p.get('market', '未入池'), 'name': p.get('name', ''),
            'sector': p.get('sector', ''),
            'days': n, 'start': series[0][0], 'end': series[-1][0],
            'total_return': round(total_ret, 2), 'annualized': round(ann, 2),
            'volatility': round(vol, 2), 'trend': trend,
            'signals': sig_map.get(code, 0),
            'signal_share': round(sig_map.get(code, 0) / total_sig * 100, 1),
        })

    market_counts = {}
    for p in profiles:
        market_counts[p['market']] = market_counts.get(p['market'], 0) + 1
    trends = [p['trend'] for p in profiles]
    n_up = sum(1 for t in trends if '上涨' in t)
    n_down = sum(1 for t in trends if '下跌' in t)
    if n_up and not n_down:
        mix = '整体偏多头环境'
    elif n_down and not n_up:
        mix = '整体偏空头环境'
    elif n_up == n_down and n_up > 0:
        mix = '多空混合环境'
    else:
        mix = '以震荡为主'
    summary = {
        'n_stocks': len(profiles), 'markets': market_counts, 'env': mix,
        'n_up': n_up, 'n_down': n_down, 'n_flat': len(trends) - n_up - n_down,
    }
    return profiles, summary


def compute_stock_diff_analysis(per_stock, stock_baselines):
    """股票表现差异分析:
    1. 信号适应性: 每股按触发数加权的平均超额胜率(信号在该股整体是否有效)
    2. 信号一致性: 同一信号在多少股票上超额为正(普适信号/失效信号/个股依赖信号)"""
    stock_adapt = {}
    for code, groups in per_stock.items():
        for n in HOLD_PERIODS:
            tot_trig, tot_excess = 0, 0.0
            for key, stats in groups.items():
                st = stats.get(n)
                if st and st.get('excess_win_rate') is not None:
                    tot_trig += st['triggers']
                    tot_excess += st['excess_win_rate'] * st['triggers']
            if tot_trig:
                stock_adapt.setdefault(code, {})[n] = {
                    'avg_excess': round(tot_excess / tot_trig, 2),
                    'triggers': tot_trig,
                }

    all_keys = {}
    for code, groups in per_stock.items():
        for key in groups:
            all_keys.setdefault(key, {})[code] = groups[key]

    consistency = []
    for key, code_stats in all_keys.items():
        n = 3
        rows = []
        for code, stats in code_stats.items():
            st = stats.get(n)
            if st and st.get('excess_win_rate') is not None:
                rows.append({'code': code, 'excess': st['excess_win_rate'],
                             'win_rate': st['win_rate'], 'triggers': st['triggers']})
        if len(rows) < 2:
            continue
        pos = sum(1 for r in rows if r['excess'] > 0)
        if pos == len(rows):
            kind = '普适有效'
        elif pos == 0:
            kind = '普适失效'
        else:
            kind = '个股依赖'
        consistency.append({
            'signal': f"{key[0]}|{key[1]}|{key[2]}", 'period': n,
            'positive': pos, 'total': len(rows), 'kind': kind,
            'best_stock': max(rows, key=lambda r: r['excess']),
            'worst_stock': min(rows, key=lambda r: r['excess']),
            'detail': sorted(rows, key=lambda r: -r['excess']),
        })
    consistency.sort(key=lambda x: (-x['positive'], -x['total']))
    return {'stock_adapt': stock_adapt, 'consistency': consistency}


def calc_all_indicators(df):
    df = df.copy()
    df = df.sort_values('date').reset_index(drop=True)
    close = df['close']
    high = df['high']
    low = df['low']

    for p in [5, 10, 20, 60]:
        df[f'MA{p}'] = close.rolling(window=p).mean()

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df['DIF'] = ema12 - ema26
    df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
    df['MACD'] = (df['DIF'] - df['DEA']) * 2

    for period in [6, 12, 24]:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df[f'RSI{period}'] = 100 - (100 / (1 + rs))

    low_9 = low.rolling(window=9).min()
    high_9 = high.rolling(window=9).max()
    rsv = (close - low_9) / (high_9 - low_9).replace(0, np.nan) * 100
    rsv = rsv.fillna(50)
    df['K'] = rsv.ewm(com=2, adjust=False).mean()
    df['D'] = df['K'].ewm(com=2, adjust=False).mean()
    df['J'] = 3 * df['K'] - 2 * df['D']

    boll_mid = close.rolling(window=20).mean()
    boll_std = close.rolling(window=20).std()
    df['BOLL_UP'] = boll_mid + 2 * boll_std
    df['BOLL_MID'] = boll_mid
    df['BOLL_LOW'] = boll_mid - 2 * boll_std

    df['change_pct'] = (close.pct_change() * 100).round(2)
    df['VOL_MA5'] = df['volume'].rolling(window=5).mean()
    df['VOL_MA10'] = df['volume'].rolling(window=10).mean()

    return df


# ============================================================
# 被动信号检测
# ============================================================

def detect_signals(df, code, market, name="", source="live"):
    signals = []
    if len(df) < 30:
        return signals

    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]
        trade_date = curr['date'].strftime('%Y-%m-%d') if hasattr(curr['date'], 'strftime') else str(curr['date'])[:10]

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

        # 2b. RSI24 超卖/超买 (慢速周期, 并行观察信号: 2026-08 阈值研究 v2 发现
        #     RSI24<30 10日超额+14.9pp 远强于 RSI6, 但时间切分验证未完成(后30%仅3触发),
        #     以观察身份与 RSI6 并行积累样本, 不替换现行信号)
        if pd.notna(curr['RSI24']):
            if curr['RSI24'] < 30:
                signals.append({
                    'signal_type': 'rsi24_oversold', 'signal_subtype': '超卖',
                    'direction': 'bullish', 'price': curr['close'],
                    'indicator_value': curr['RSI24'], 'indicator_name': 'RSI24',
                    'threshold': 30, 'threshold_type': 'rsi_value',
                    'description': f"RSI24超卖(观察) {curr['RSI24']:.1f} < 30",
                    'trade_date': trade_date,
                })
            elif curr['RSI24'] > 70:
                signals.append({
                    'signal_type': 'rsi24_overbought', 'signal_subtype': '超买',
                    'direction': 'bearish', 'price': curr['close'],
                    'indicator_value': curr['RSI24'], 'indicator_name': 'RSI24',
                    'threshold': 70, 'threshold_type': 'rsi_value',
                    'description': f"RSI24超买(观察) {curr['RSI24']:.1f} > 70",
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

        # 5. 放量突破
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

    if signals:
        conn = get_db()
        for sig in signals:
            # 幂等: 同股同日同信号已存在则跳过, 防止管线重跑造成重复入库
            exists = conn.execute(
                "SELECT 1 FROM passive_signals WHERE code=? AND trade_date=? "
                "AND signal_type=? AND signal_subtype=? AND direction=? LIMIT 1",
                (code, sig['trade_date'], sig['signal_type'],
                 sig['signal_subtype'], sig['direction'])
            ).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO passive_signals "
                "(code, market, name, trigger_time, signal_type, signal_subtype, direction, "
                "price, volume, indicator_value, indicator_name, threshold, threshold_type, "
                "description, trade_date, source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (code, market, name,
                 sig.get('trade_date', '') + ' 15:00:00',
                 sig['signal_type'], sig['signal_subtype'], sig['direction'],
                 sig['price'], 0, sig['indicator_value'], sig['indicator_name'],
                 sig['threshold'], sig['threshold_type'],
                 sig['description'], sig['trade_date'], source)
            )
        conn.commit()
        conn.close()

    return signals


def run_signal_detection(stocks=None):
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
    conn = get_db()
    pool_f, pool_args = _pool_sql_filter(_pool_code_set())
    rows = conn.execute(
        f"SELECT code, market, name, COUNT(*) as days, "
        "MIN(trade_date) as start, MAX(trade_date) as end "
        f"FROM daily_quotes WHERE {pool_f} GROUP BY code, market ORDER BY market, code",
        pool_args
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ============================================================
# 数据清洗: 宽表生成 (时间对齐 + 哑变量展开)
# ============================================================

SIGNAL_TYPES = ['macd_cross', 'rsi_oversold', 'rsi_overbought',
                'kdj_cross', 'boll_break', 'volume_surge', 'price_limit']
EVENT_TYPES = ['earnings', 'dividend', 'policy', 'industry', 'announcement', 'macro']


def _get_next_trade_date(conn, code, current_date):
    """查找某只股票在指定日期之后的下一个交易日"""
    row = conn.execute(
        "SELECT MIN(trade_date) as d FROM daily_quotes "
        "WHERE code=? AND trade_date>? ORDER BY trade_date LIMIT 1",
        (code, current_date)
    ).fetchone()
    return row['d'] if row and row['d'] else current_date


def _is_after_hours(event_time):
    """判断事件时间是否为盘后(>=15:00)"""
    if not event_time or len(event_time) < 10:
        return False
    time_part = event_time[11:] if len(event_time) > 10 else "00:00:00"
    return time_part >= "15:00:00"


def generate_wide_table(code, days=365):
    """
    生成宽表: 以日线行情为基准, 左连接被动信号和主动事件
    - 被动信号: 按信号类型展开为0/1哑变量, 归属到触发当日
    - 主动事件: 按事件类型展开为0/1哑变量, 盘后事件归属到下一交易日
    """
    code = code.strip().upper()
    conn = get_db()

    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    quotes = conn.execute(
        "SELECT trade_date, code, market, open, high, low, close, "
        "volume, amount, change_pct, turnover_rate, pe_ratio, pb_ratio, "
        "total_market_cap, circ_market_cap, net_inflow "
        "FROM daily_quotes WHERE code=? AND trade_date>=? ORDER BY trade_date",
        (code, since)
    ).fetchall()

    if not quotes:
        conn.close()
        return 0

    trade_dates = [r['trade_date'] for r in quotes]

    # 2. 被动信号: 按信号类型+交易日汇总为0/1
    signals = conn.execute(
        "SELECT signal_type, trade_date FROM passive_signals "
        "WHERE code=? AND trade_date>=? ORDER BY trade_date",
        (code, since)
    ).fetchall()

    sig_matrix = {}
    for sig_type in SIGNAL_TYPES:
        sig_matrix[sig_type] = {td: 0 for td in trade_dates}

    for sig in signals:
        sig_type = sig['signal_type']
        td = sig['trade_date']
        if sig_type in sig_matrix and td in sig_matrix[sig_type]:
            sig_matrix[sig_type][td] = 1

    # 3. 主动事件: 盘后事件后移到下一交易日, 按事件类型展开为0/1
    events = conn.execute(
        "SELECT event_type, event_time, trade_date FROM active_events "
        "WHERE code=? ORDER BY event_time",
        (code,)
    ).fetchall()

    evt_matrix = {}
    for evt_type in EVENT_TYPES:
        evt_matrix[evt_type] = {td: 0 for td in trade_dates}

    for evt in events:
        evt_type = evt['event_type']
        evt_time = evt['event_time'] or ''
        orig_date = evt['trade_date'] or (evt_time[:10] if evt_time else '')

        if not orig_date:
            continue

        if _is_after_hours(evt_time):
            mapped_date = _get_next_trade_date(conn, code, orig_date)
        else:
            mapped_date = orig_date

        if evt_type in evt_matrix and mapped_date in evt_matrix[evt_type]:
            evt_matrix[evt_type][mapped_date] = 1

    conn.close()

    # 4. 写入 daily_feature_base
    conn = get_db()
    count = 0
    for q in quotes:
        td = q['trade_date']
        sig_vals = [sig_matrix[st][td] for st in SIGNAL_TYPES]
        evt_vals = [evt_matrix[et][td] for et in EVENT_TYPES]

        try:
            conn.execute(
                "INSERT OR REPLACE INTO daily_feature_base "
                "(trade_date, code, market, open, high, low, close, volume, amount, "
                "change_pct, turnover_rate, pe_ratio, pb_ratio, "
                "total_market_cap, circ_market_cap, net_inflow, "
                "sig_macd_cross, sig_rsi_oversold, sig_rsi_overbought, "
                "sig_kdj_cross, sig_boll_break, sig_volume_surge, sig_price_limit, "
                "evt_earnings, evt_dividend, evt_policy, evt_industry, "
                "evt_announcement, evt_macro) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (td, code, q['market'], q['open'], q['high'], q['low'], q['close'],
                 q['volume'], q['amount'], q['change_pct'], q['turnover_rate'],
                 q['pe_ratio'], q['pb_ratio'], q['total_market_cap'],
                 q['circ_market_cap'], q['net_inflow'],
                 *sig_vals, *evt_vals)
            )
            count += 1
        except Exception:
            pass

    conn.commit()
    conn.close()
    return count


def batch_generate_wide_tables(stocks=None, days=365):
    """批量生成宽表 — 默认仅当前活跃池(2026-09-03 起, 池为唯一事实源)"""
    if stocks is None:
        stocks = get_stock_pool(active_only=True)
    results = []
    for s in stocks:
        code = s['code']
        market = s.get('market', 'A股')
        n = generate_wide_table(code, days)
        results.append({'code': code, 'market': market, 'rows': n})
    return results


def get_feature_base(code, days=365):
    """从 daily_feature_base 读取特征宽表, 返回 DataFrame"""
    code = code.strip().upper()
    conn = get_db()
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    rows = conn.execute(
        "SELECT * FROM daily_feature_base WHERE code=? AND trade_date>=? ORDER BY trade_date",
        (code, since)
    ).fetchall()
    conn.close()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


def get_feature_base_stats():
    """daily_feature_base 统计信息 (仅当前池)"""
    conn = get_db()
    pool_f, pool_args = _pool_sql_filter(_pool_code_set())
    stats = {}
    stats['total_rows'] = conn.execute(
        f"SELECT COUNT(*) as c FROM daily_feature_base WHERE {pool_f}", pool_args).fetchone()['c']
    stats['total_codes'] = conn.execute(
        f"SELECT COUNT(DISTINCT code) as c FROM daily_feature_base WHERE {pool_f}", pool_args).fetchone()['c']
    stats['signal_rows'] = conn.execute(
        f"SELECT COUNT(*) as c FROM daily_feature_base WHERE "
        "(sig_macd_cross=1 OR sig_rsi_oversold=1 OR sig_rsi_overbought=1 OR "
        "sig_kdj_cross=1 OR sig_boll_break=1 OR sig_volume_surge=1 OR sig_price_limit=1) "
        f"AND {pool_f}", pool_args).fetchone()['c']
    stats['event_rows'] = conn.execute(
        f"SELECT COUNT(*) as c FROM daily_feature_base WHERE "
        "(evt_earnings=1 OR evt_dividend=1 OR evt_policy=1 OR "
        "evt_industry=1 OR evt_announcement=1 OR evt_macro=1) "
        f"AND {pool_f}", pool_args).fetchone()['c']
    stats['signal_breakdown'] = conn.execute(
        f"SELECT 'sig_macd_cross' as col, COALESCE(SUM(sig_macd_cross),0) as c FROM daily_feature_base WHERE {pool_f} "
        f"UNION ALL SELECT 'sig_rsi_oversold', COALESCE(SUM(sig_rsi_oversold),0) FROM daily_feature_base WHERE {pool_f} "
        f"UNION ALL SELECT 'sig_rsi_overbought', COALESCE(SUM(sig_rsi_overbought),0) FROM daily_feature_base WHERE {pool_f} "
        f"UNION ALL SELECT 'sig_kdj_cross', COALESCE(SUM(sig_kdj_cross),0) FROM daily_feature_base WHERE {pool_f} "
        f"UNION ALL SELECT 'sig_boll_break', COALESCE(SUM(sig_boll_break),0) FROM daily_feature_base WHERE {pool_f} "
        f"UNION ALL SELECT 'sig_volume_surge', COALESCE(SUM(sig_volume_surge),0) FROM daily_feature_base WHERE {pool_f} "
        f"UNION ALL SELECT 'sig_price_limit', COALESCE(SUM(sig_price_limit),0) FROM daily_feature_base WHERE {pool_f}",
        pool_args * 7).fetchall()
    stats['event_breakdown'] = conn.execute(
        f"SELECT 'evt_earnings' as col, COALESCE(SUM(evt_earnings),0) as c FROM daily_feature_base WHERE {pool_f} "
        f"UNION ALL SELECT 'evt_dividend', COALESCE(SUM(evt_dividend),0) FROM daily_feature_base WHERE {pool_f} "
        f"UNION ALL SELECT 'evt_policy', COALESCE(SUM(evt_policy),0) FROM daily_feature_base WHERE {pool_f} "
        f"UNION ALL SELECT 'evt_industry', COALESCE(SUM(evt_industry),0) FROM daily_feature_base WHERE {pool_f} "
        f"UNION ALL SELECT 'evt_announcement', COALESCE(SUM(evt_announcement),0) FROM daily_feature_base WHERE {pool_f} "
        f"UNION ALL SELECT 'evt_macro', COALESCE(SUM(evt_macro),0) FROM daily_feature_base WHERE {pool_f}",
        pool_args * 6).fetchall()
    conn.close()
    stats['signal_breakdown'] = [dict(r) for r in stats['signal_breakdown']]
    stats['event_breakdown'] = [dict(r) for r in stats['event_breakdown']]
    return stats


# ============================================================
# 事件研究共享统计核心 (原模块4, 个股级事件研究已于 2026-09-03 删除)
# 保留范围: 事件窗/估计窗常量 + 基准指数采集(benchmark_index) + _t_test/_agg_group
# 现役消费者: 模块6宏观事件研究(指数AR) + 模块5回测B2大盘对照
# 删除原因: 个股主动事件数据源不合格——财报日期实为会计年度末(同S3根因),
#           有效样本仅10个(7分红+3财报), 分组后统计无意义; 详见 模块6开发计划.md 问题日志#10
# ============================================================

# 事件窗 [-5, +10]: 事件前5个交易日至事件后10个交易日 (Day 0 = 事件归属交易日)
EVENT_WIN = list(range(-5, 11))
# 估计窗 [-120, -10]: 用于回归估算个股正常收益水平
EST_WIN = (-120, -10)
# 估计窗最少有效观测数(标准120日, 行情不足时降级至60日, 再少则跳过该事件)
MIN_EST_OBS = 60
# 显著性检验的CAR窗口: 事件当日 / 后1日 / 后3日 / 后5日 / 后10日 / 全窗口
CAR_WINDOWS = [(0, 0), (0, 1), (0, 3), (0, 5), (0, 10), (-5, 10)]

# 各市场基准指数
BENCHMARKS = {
    'A股': {'name': '沪深300', 'ak_symbol': 'sh000300'},
    '港股': {'name': '恒生指数', 'ak_symbol': 'HSI'},
    '美股': {'name': '标普500', 'ak_symbol': '.INX'},
}


def collect_benchmark_index(market):
    """采集市场基准指数日线 → benchmark_index 表
    A股: 沪深300 | 港股: 恒生指数 | 美股: 标普500"""
    cfg = BENCHMARKS.get(market)
    if not cfg:
        print(f"  [collect_benchmark_index] 未知市场: {market}")
        return 0
    df = None
    try:
        import akshare as ak
        if market == 'A股':
            df = ak.stock_zh_index_daily(symbol=cfg['ak_symbol'])
        elif market == '港股':
            df = ak.stock_hk_index_daily_sina(symbol=cfg['ak_symbol'])
        else:
            df = ak.index_us_stock_sina(symbol=cfg['ak_symbol'])
    except Exception as e:
        print(f"  [collect_benchmark_index] {market} akshare: {e}")
    if (df is None or len(df) == 0) and market == 'A股':
        # 新浪K线兜底
        try:
            url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
            params = {"symbol": cfg['ak_symbol'], "scale": "240", "ma": "no", "datalen": "600"}
            resp = _session.get(url, params=params, timeout=15,
                                headers={"Referer": "https://finance.sina.com.cn"})
            data = json.loads(resp.text)
            if data:
                df = pd.DataFrame(data).rename(columns={'day': 'date'})
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    df[col] = pd.to_numeric(df[col])
                df['amount'] = 0.0
        except Exception as e:
            print(f"  [collect_benchmark_index] {market} sina: {e}")
    if df is None or len(df) == 0:
        return 0
    conn = get_db()
    count = 0
    for _, row in df.iterrows():
        d = row['date']
        trade_date = d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)[:10]
        try:
            conn.execute(
                "INSERT OR REPLACE INTO benchmark_index "
                "(market, trade_date, code, name, open, high, low, close, volume, amount) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (market, trade_date, cfg['ak_symbol'], cfg['name'],
                 float(row.get('open') or 0), float(row.get('high') or 0),
                 float(row.get('low') or 0), float(row.get('close') or 0),
                 float(row.get('volume') or 0), float(row.get('amount') or 0))
            )
            count += 1
        except Exception as e:
            print(f"  [collect_benchmark_index] {market} {trade_date}: {e}")
    conn.commit()
    conn.close()
    return count


def batch_collect_benchmark():
    """采集全部市场基准指数"""
    out = []
    for market, cfg in BENCHMARKS.items():
        n = collect_benchmark_index(market)
        out.append({'market': market, 'name': cfg['name'], 'rows': n})
    return out


def get_benchmark_status():
    """基准指数数据覆盖情况"""
    conn = get_db()
    status = {}
    for market, cfg in BENCHMARKS.items():
        row = conn.execute(
            "SELECT COUNT(*) as c, MIN(trade_date) as d0, MAX(trade_date) as d1 "
            "FROM benchmark_index WHERE market=?", (market,)).fetchone()
        status[market] = {
            'name': cfg['name'], 'code': cfg['ak_symbol'],
            'rows': row['c'], 'start': row['d0'] or '—', 'end': row['d1'] or '—',
        }
    conn.close()
    return status


def _t_test(values):
    """单样本t检验(均值 vs 0), 返回 (t统计量, p值)"""
    vals = [v for v in values if v is not None]
    n = len(vals)
    if n < 2:
        return None, None
    mean = float(np.mean(vals))
    sd = float(np.std(vals, ddof=1))
    if sd == 0:
        return None, None
    t = mean / (sd / np.sqrt(n))
    try:
        from scipy import stats as scistats
        p = float(2 * scistats.t.sf(abs(t), df=n - 1))
    except Exception as e:
        print(f"  [_t_test/scipy] {e}")
        p = None
    return t, p


def _sig_stars(p):
    """显著性星号: *** p<0.01 · ** p<0.05 · * p<0.1"""
    if p is None:
        return ''
    if p < 0.01:
        return '***'
    if p < 0.05:
        return '**'
    if p < 0.1:
        return '*'
    return ''


def _agg_group(key, evs, kind):
    """一组事件的聚合: CAAR曲线 + 各窗口CAR均值与t检验"""
    label = '|'.join(str(k) for k in key)
    # 曲线: 每个相对日的平均超额收益AAR 与累计平均超额收益CAAR
    rel_days, aar, caar, n_by_day = [], [], [], []
    cum = 0.0
    for rel in EVENT_WIN:
        vals = [ev['ar'][rel] for ev in evs if rel in ev['ar']]
        rel_days.append(rel)
        n_by_day.append(len(vals))
        if vals:
            m = float(np.mean(vals))
            aar.append(m)
            cum += m
            caar.append(cum)
        else:
            aar.append(None)
            caar.append(None)
    # 窗口统计: 仅纳入窗口内全部交易日都有效的事件
    windows = []
    for t1, t2 in CAR_WINDOWS:
        car_vals = []
        for ev in evs:
            rels = range(t1, t2 + 1)
            if all(r in ev['ar'] for r in rels):
                car_vals.append(sum(ev['ar'][r] for r in rels))
        n = len(car_vals)
        t_stat, p_value = _t_test(car_vals)
        windows.append({
            'window': f'[{t1},{t2:+d}]', 'n': n,
            'car_mean': round(float(np.mean(car_vals)) * 100, 4) if car_vals else None,
            't_stat': round(t_stat, 3) if t_stat is not None else None,
            'p_value': round(p_value, 4) if p_value is not None else None,
            'sig': _sig_stars(p_value),
        })
    return {
        'kind': kind, 'key': list(key), 'label': label,
        'event_type': key[0], 'total_events': len(evs),
        'curve': {'rel_days': rel_days, 'aar': aar, 'caar': caar, 'n_by_day': n_by_day},
        'windows': windows,
    }


# ============================================================
# 模块5: 信号策略回测与基准对比 (Event-driven Backtest Engine)
# 无未来数据红线: T日收盘确认信号 → T+1开盘成交(买入与卖出均如此)
# 槽位制资金模型: K个槽各占1/K资金, 每槽独立复利, 满仓时新信号放弃
# ============================================================

BT_MAX_POSITIONS = 3   # 最大同时持仓数
BT_FEE = 0.0           # 单边费率(买入+卖出各计一次)

# 回测策略定义: match=被动信号(类型,子类型)触发
# S1a~S1d/S2/S1e 选型冻结于 2026-08-29(见 模块5开发计划.md 2.2 更新注):
# 信号表去重后其统计后盾已变薄, 但依新统计重选=用同一数据先看答案再选题, 故不重选
# S3(事件·财报) 已于 2026-09-02 删除: active_events 财报数据源不合格——
# 港股"事件"日期实为会计年度末(非披露日)、09889 映射成东莞农商银行、AAPL 仅一条未来日期,
# oos 段 0 事件系采集缺口而非真空期, full 段 4 笔交易中 2 笔日期本身错误 → 双段均不可信
BT_STRATEGIES = {
    'S1a': {'name': '单信号·大跌持有10日', 'match': [('price_limit', '大跌')], 'hold': 10},
    'S1b': {'name': '单信号·大跌持有5日', 'match': [('price_limit', '大跌')], 'hold': 5},
    'S1c': {'name': '单信号·跌破下轨持有5日', 'match': [('boll_break', '跌破下轨')], 'hold': 5},
    'S1d': {'name': '单信号·KDJ金叉持有3日', 'match': [('kdj_cross', '金叉')], 'hold': 3},
    'S2': {'name': '组合·超跌反弹持有5日',
           'match': [('boll_break', '跌破下轨'), ('price_limit', '大跌'),
                     ('rsi_oversold', '超卖')], 'hold': 5},
    'S1e': {'name': '观察·RSI24超卖持有10日',
            'match': [('rsi24_oversold', '超卖')], 'hold': 10, 'observation': True},
    # 模块9 overlay 变体(2026-09-06 预注册设计, 见 模块9开发计划.md 2.3):
    # 触发/持有期/槽位/费率与原版完全一致, 唯一差异=宏观封锁日不开新仓(只做减法);
    # 家族选择=FDR存活组(q=0.05分层BH, n>=30, car<0), 配置冻结于 macro_overlay_meta;
    # 回测 full/oos 均为机制对照(选择泄漏), 唯一裁决=forward test
    'S2M': {'name': '组合·超跌反弹持有5日·宏观封锁',
            'match': [('boll_break', '跌破下轨'), ('price_limit', '大跌'),
                      ('rsi_oversold', '超卖')], 'hold': 5, 'overlay': True},
    'S1aM': {'name': '单信号·大跌持有10日·宏观封锁',
             'match': [('price_limit', '大跌')], 'hold': 10, 'overlay': True},
}

# D2.0 知识截止日协议(冻结于 2026-08-31, 详见 模块5开发计划.md):
# 全部现行选型(阈值/池标准/策略集)在 2025-07~2026-08 全窗口选出,
# 回测全窗口=样本内复述(收益系统性高估), 故结果强制 full/oos 双栏;
# 冻结日后禁止依回测结果回头调参, 严格样本外=2026-09 起的 forward test
BT_KNOWLEDGE_CUTOFF = '2026-05-01'        # 知识截止日(样本内最后一天, 266信号日70%分位)
BT_FREEZE_DATE = '2026-08-31'             # 参数冻结日
BT_FEE_SENSITIVITY = [0.0, 0.001, 0.003]  # 费率敏感性档位(单边)
BT_SEGMENTS = [('full', None), ('oos', BT_KNOWLEDGE_CUTOFF)]


def _load_backtest_data():
    """回测数据加载: 宽表行情 + 被动信号日级触发集 + 基准指数序列
    信号强制从 passive_signals 读取并 DISTINCT 日级去重(保留方向),
    避免日内多次触发导致样本虚增; 行情仅取 open/close 均可用的行;
    2026-09-03 起仅加载当前活跃池股票(池为唯一事实源)"""
    conn = get_db()
    pool_f, pool_args = _pool_sql_filter(_pool_code_set())
    stocks = {}
    for r in conn.execute(
            f"SELECT code, market, trade_date, open, close FROM daily_feature_base "
            f"WHERE {pool_f} ORDER BY code, trade_date", pool_args).fetchall():
        st = stocks.setdefault(r['code'], {
            'market': r['market'], 'dates': [], 'opens': [], 'closes': [], 'date_idx': {}})
        if r['open'] and r['close']:
            st['date_idx'][r['trade_date']] = len(st['dates'])
            st['dates'].append(r['trade_date'])
            st['opens'].append(r['open'])
            st['closes'].append(r['close'])
    calendar = sorted(set(d for s in stocks.values() for d in s['dates']))
    signals = set()
    for r in conn.execute(
            "SELECT DISTINCT code, trade_date, signal_type, signal_subtype "
            f"FROM passive_signals WHERE {pool_f}", pool_args).fetchall():
        signals.add((r['code'], r['trade_date'], r['signal_type'], r['signal_subtype']))
    index_series = {}
    for m in ['A股', '港股', '美股']:
        rows = conn.execute(
            "SELECT trade_date, close FROM benchmark_index WHERE market=? "
            "ORDER BY trade_date", (m,)).fetchall()
        ds = [r['trade_date'] for r in rows]
        cs = [r['close'] for r in rows]
        index_series[m] = {'dates': ds, 'closes': cs,
                           'date_idx': {d: i for i, d in enumerate(ds)}}
    conn.close()
    return {'stocks': stocks, 'calendar': calendar,
            'signals': signals, 'index_series': index_series}


def _collect_triggers(data, spec):
    """按策略定义收集触发点 [(code, trigger_date)]
    同股同日多信号只触发一次(组合策略口径); 事件类直接查 active_events"""
    pairs = set()
    if 'match' in spec:
        ms = set(spec['match'])
        for (code, date, st, sst) in data['signals']:
            if (st, sst) in ms:
                pairs.add((code, date))
    else:
        conn = get_db()
        pool_f, pool_args = _pool_sql_filter(_pool_code_set())
        rows = conn.execute(
            "SELECT DISTINCT code, trade_date FROM active_events "
            f"WHERE event_type=? AND {pool_f}", (spec['event_type'],) + tuple(pool_args)
        ).fetchall()
        conn.close()
        for r in rows:
            pairs.add((r['code'], r['trade_date']))
    out = []
    for code, date in pairs:
        st = data['stocks'].get(code)
        if st and date in st['date_idx']:
            out.append((code, date))
    out.sort()
    return out


def _run_strategy_bt(data, triggers, hold, max_pos=BT_MAX_POSITIONS, fee=BT_FEE,
                     blocked_days=None, drop_incomplete=True):
    """槽位制事件回测核心:
    T日触发 → T+1开盘买入 → 持有hold个交易日 → 开盘卖出
    drop_incomplete=True(默认, 回测口径): 数据末端无法完成完整持仓的信号直接放弃(end_dropped)
    drop_incomplete=False(前向口径): 出场日未到的持仓挂仓盯市(按最新收盘计市值, s_date=None
    永不触发平仓分支), 不生成交易记录 — 出场日行情终局后由后续确定性重放补录(幂等键防重);
    开仓日本身超界的触发仍按 end_dropped 跳过(仓位尚未建立, 无可盯市)
    blocked_days(模块9 overlay): {code: set(开仓日)} — 命中封锁日的开仓放弃(macro_blocked),
    只拒新仓不影响持仓; 默认 None 行为与模块5完全一致(向后兼容)
    返回 {'equity','trades','rejected','end_dropped','fund_util','n_triggers',
          'macro_blocked','open_positions'}"""
    # 1) 触发 → 交易计划(先计算索引, 过滤超界)
    plans = []
    end_dropped = 0
    for code, tdate in triggers:
        st = data['stocks'].get(code)
        if not st:
            continue
        i = st['date_idx'].get(tdate)
        if i is None:
            continue
        b, s = i + 1, i + 1 + hold
        if s >= len(st['dates']):
            if drop_incomplete or b >= len(st['dates']):
                end_dropped += 1
                continue
            plans.append({'code': code, 'trig_date': tdate, 'b': b, 's': None,
                          'b_date': st['dates'][b], 's_date': None})
            continue
        plans.append({'code': code, 'trig_date': tdate, 'b': b, 's': s,
                      'b_date': st['dates'][b], 's_date': st['dates'][s]})
    plans.sort(key=lambda p: (p['b_date'], p['trig_date']))

    # 2) 日历遍历: 先平仓(释放槽) 再开仓 后收盘估值
    K = max(1, max_pos)
    slots = [1.0 / K] * K
    slot_busy = [None] * K
    trades, equity, fund_util = [], [], []
    rejected = 0
    macro_blocked = 0
    for d in data['calendar']:
        for k in range(K):
            pos = slot_busy[k]
            if pos and pos['s_date'] == d:
                st = data['stocks'][pos['code']]
                entry_eff = pos['entry_open'] * (1 + fee)
                exit_eff = st['opens'][pos['s']] * (1 - fee)
                ret = exit_eff / entry_eff - 1
                # 模块3统计口径(信号日收盘买入)对照: 隔夜跳空成本 = 回测收益 - 统计收益
                stat_ret = st['closes'][pos['trig_i'] + hold] / st['closes'][pos['trig_i']] - 1
                slots[k] *= (1 + ret)
                trades.append({
                    'code': pos['code'], 'market': st['market'],
                    'trigger_date': pos['trig_date'], 'entry_date': pos['b_date'],
                    'entry_price': round(pos['entry_open'], 4),
                    'exit_date': pos['s_date'],
                    'exit_price': round(st['opens'][pos['s']], 4),
                    'ret': ret, 'stat_ret': stat_ret, 'gap_cost': ret - stat_ret,
                    'hold_days': hold,
                })
                slot_busy[k] = None
        for p in plans:
            if p['b_date'] == d:
                if blocked_days and d in blocked_days.get(p['code'], ()):
                    macro_blocked += 1
                    continue
                free = next((k for k in range(K) if slot_busy[k] is None), None)
                if free is None:
                    rejected += 1
                    continue
                st = data['stocks'][p['code']]
                slot_busy[free] = {
                    'code': p['code'], 'trig_date': p['trig_date'], 'trig_i': st['date_idx'][p['trig_date']],
                    'b': p['b'], 's': p['s'], 'b_date': p['b_date'], 's_date': p['s_date'],
                    'entry_open': st['opens'][p['b']], 'last_mv': None,
                }
        total = 0.0
        busy_v = 0.0
        for k in range(K):
            pos = slot_busy[k]
            if pos is None:
                total += slots[k]
            else:
                st = data['stocks'][pos['code']]
                ci = st['date_idx'].get(d)
                if ci is not None:
                    pos['last_mv'] = slots[k] * st['closes'][ci] / pos['entry_open']
                elif pos['last_mv'] is None:
                    pos['last_mv'] = slots[k]
                total += pos['last_mv']
                busy_v += pos['last_mv']
        equity.append(total)
        fund_util.append(busy_v / total if total > 0 else 0.0)
    return {'equity': equity, 'trades': trades, 'rejected': rejected,
            'end_dropped': end_dropped, 'fund_util': fund_util,
            'n_triggers': len(triggers), 'macro_blocked': macro_blocked,
            'open_positions': sum(1 for x in slot_busy if x is not None)}


def _perf_metrics(equity, dates, trades=None, fund_util=None):
    """统一绩效指标: 收益/风险/效率/交易/稳健"""
    eq = np.asarray(equity, dtype=float)
    n = len(eq)
    if n < 2:
        return {}
    total = eq[-1] / eq[0] - 1
    ann = (eq[-1] / eq[0]) ** (252 / max(n - 1, 1)) - 1
    daily = np.diff(eq) / eq[:-1]
    vol = float(np.std(daily, ddof=1) * np.sqrt(252)) if n > 2 else None
    sharpe = ann / vol if vol and vol > 0 else None
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1
    mdd = float(dd.min())
    mdd_end = int(np.argmin(dd))
    mdd_start = int(np.argmax(eq[:mdd_end + 1])) if mdd_end > 0 else 0
    day_win = float((daily > 0).mean())
    half = n // 2
    ann_1 = (eq[half] / eq[0]) ** (252 / max(half, 1)) - 1
    ann_2 = (eq[-1] / eq[half]) ** (252 / max(n - 1 - half, 1)) - 1
    m = {
        'total_return': round(total, 6), 'annual_return': round(ann, 6),
        'annual_vol': round(vol, 6) if vol is not None else None,
        'sharpe': round(sharpe, 4) if sharpe is not None else None,
        'max_drawdown': round(mdd, 6),
        'mdd_start': dates[mdd_start] if dates else None,
        'mdd_end': dates[mdd_end] if dates else None,
        'daily_win_rate': round(day_win, 4),
        'seg1_annual': round(ann_1, 6), 'seg2_annual': round(ann_2, 6),
    }
    if trades:
        rets = [t['ret'] for t in trades]
        stats = [t['stat_ret'] for t in trades if t.get('stat_ret') is not None]
        m.update({
            'n_trades': len(trades),
            'avg_trade_ret': round(float(np.mean(rets)), 6),
            'avg_stat_ret': round(float(np.mean(stats)), 6) if stats else None,
            'avg_gap_cost': round(float(np.mean([t['ret'] - t['stat_ret']
                                                 for t in trades
                                                 if t.get('stat_ret') is not None])), 6),
        })
    if fund_util:
        m['avg_fund_util'] = round(float(np.mean(fund_util)), 4)
    return m


def _buy_hold_baseline(data, start_date=None):
    """B1 买入持有基准: 各股以开盘价建仓后不动, 等权1/N, 休市沿用前值
    start_date(oos段)=知识截止日: 各股在其自身首个 >start_date 交易日再建仓"""
    codes = sorted(data['stocks'].keys())
    if not codes:
        return []
    w = 1.0 / len(codes)
    entry_idx = {}
    for c in codes:
        st = data['stocks'][c]
        if start_date is None:
            entry_idx[c] = 0
        else:
            entry_idx[c] = next((i for i, d in enumerate(st['dates'])
                                 if d > start_date), None)
    ratio = {c: None for c in codes}
    equity = []
    for d in data['calendar']:
        v = 0.0
        for c in codes:
            st = data['stocks'][c]
            ei = entry_idx.get(c)
            ci = st['date_idx'].get(d)
            if ci is None or ei is None or ci < ei:
                v += w * (ratio[c] if ratio[c] is not None else 1.0)
            else:
                ratio[c] = st['closes'][ci] / st['opens'][ei]
                v += w * ratio[c]
        equity.append(v)
    return equity


def _index_baseline(data):
    """B2 大盘指数基准: 三市场指数各1/3资金独立复利, 休市日贡献0"""
    markets = ['A股', '港股', '美股']
    ratio = {m: 1.0 for m in markets}
    equity = []
    for d in data['calendar']:
        for m in markets:
            ser = data['index_series'].get(m)
            if not ser:
                continue
            i = ser['date_idx'].get(d)
            if i is not None and i > 0:
                ratio[m] *= ser['closes'][i] / ser['closes'][i - 1]
        equity.append(sum(ratio.values()) / len(markets))
    return equity


def run_backtest(strategy_id, max_pos=BT_MAX_POSITIONS, fee=BT_FEE,
                 data=None, start_date=None, blocked_days=None):
    """单策略回测入口(不写库, 供批量执行与看板调用)
    start_date(oos段)=知识截止日: 仅统计 trade_date > start_date 的触发,
    日历截取至该日之后, 净值从 1 重起
    blocked_days(模块9): overlay 策略未显式传入时自动从台账加载"""
    data = data if data is not None else _load_backtest_data()
    if start_date is not None:
        data = _filter_bt_data(data, start_date)
    spec = BT_STRATEGIES[strategy_id]
    triggers = _collect_triggers(data, spec)
    if start_date is not None:
        triggers = [(c, d) for (c, d) in triggers if d > start_date]
    if spec.get('overlay') and blocked_days is None:
        blocked_days = _load_overlay_blocked(data)
    if not spec.get('overlay'):
        blocked_days = None
    res = _run_strategy_bt(data, triggers, spec['hold'], max_pos, fee,
                           blocked_days=blocked_days)
    res['strategy_id'] = strategy_id
    res['strategy_name'] = spec['name']
    res['metrics'] = _perf_metrics(res['equity'], data['calendar'],
                                   res['trades'], res['fund_util'])
    return res


def run_all_backtests(max_pos=BT_MAX_POSITIONS, fee=BT_FEE,
                      start_date=None, data=None):
    """批量执行: 全部策略 + B1/B2 双基准(不写库)
    start_date(oos段)=知识截止日: 触发/日历/净值截取至该日之后, 基准同窗口重算"""
    data = data if data is not None else _load_backtest_data()
    if start_date is not None:
        data = _filter_bt_data(data, start_date)
    strategies = {}
    for sid in BT_STRATEGIES:
        strategies[sid] = run_backtest(sid, max_pos, fee, data, start_date)
    bh_eq = _buy_hold_baseline(data, start_date)
    idx_eq = _index_baseline(data)
    baselines = {
        'B1': {'name': '基准·买入持有等权',
               'equity': bh_eq,
               'metrics': _perf_metrics(bh_eq, data['calendar'])},
        'B2': {'name': '基准·大盘指数等权',
               'equity': idx_eq,
               'metrics': _perf_metrics(idx_eq, data['calendar'])},
    }
    return {
        'range': {'start': data['calendar'][0], 'end': data['calendar'][-1],
                  'n_days': len(data['calendar']), 'n_stocks': len(data['stocks'])},
        'params': {'max_positions': max_pos, 'fee': fee},
        'segment': 'oos' if start_date is not None else 'full',
        'strategies': strategies, 'baselines': baselines,
    }


# ============================================================
# 模块 6: 宏观经济日历采集与事件研究 (2026-09-03)
# 数据源: 百度股市通 sapi finance.pae.baidu.com (审计见 经济日历数据源审计.md)
# 红线: 不用 akshare news_economic_baidu (分页缺指纹→WAF 403, 默认日期硬编码)
# ============================================================
MACRO_API = "https://finance.pae.baidu.com/sapi/v1/financecalendar"
MACRO_BACKFILL_START = '2025-07-29'        # 与模块5回测窗口起点对齐
MACRO_STAR_HIGH = 2                        # 重要性别: 源数据仅1/2两级, 2=重点关注
MACRO_RECENT_REFRESH = 3                   # 最近N天强制重采(实际值陆续填充)
MACRO_REGION_INDEX = {                     # 事件地区 → 研究指数(溢出视角)
    '美国': ['.INX', 'HSI', 'sh000300'],
    '中国': ['sh000300', 'HSI'],
    '中国香港': ['HSI'],
}
MACRO_FAMILY_RULES = [                     # 事件家族: 顺序敏感, 首个命中生效
    ('核心CPI|核心消费者物价', '核心CPI'),
    ('CPI|消费者物价', 'CPI'),
    ('核心PPI', '核心PPI'),
    ('PPI|生产者物价', 'PPI'),
    ('PCE', 'PCE'),
    ('非农|就业人数|ADP', '就业报告'),
    ('失业率', '失业率'),
    ('初请|初申|申请失业金|领取失业金', '初请失业金'),
    ('利率决议|联邦基金利率|FOMC|利率决定|美联储公布|央行利率|基准利率', '利率决议'),
    ('GDP|国内生产总值', 'GDP'),
    ('PMI', 'PMI'),
    ('零售销售', '零售销售'),
    ('贸易帐|贸易余额|进出口', '贸易帐'),
    ('消费者信心|密歇根|消费者情绪', '消费者信心'),
    ('成屋销售|新屋销售|房屋开工|营建许可|新屋开工|房价指数', '房地产数据'),
    ('个人收入|个人支出|个人消费', '个人收支'),
    ('工业产出|制造业指数|工厂订单|耐用品订单|工业产出', '工业数据'),
    ('货币供应|M2', '货币供应'),
]
_macro_cookie_cache = {'cookie': None, 'ts': 0.0}  # 已废弃: sapi 实测无需 cookie(2026-09-03 调试), 保留占位避免外部引用报错


def fetch_macro_day(date_iso, cate='economic_data'):
    """自写客户端: 单日事件采集, 全请求带浏览器指纹 + 手动分页, 无需 cookie
    (2026-09-03 实测: sapi 只校验浏览器指纹, 裸请求即 200, 翻页 101/101 完整)
    返回原始 item 列表(可能为空列表=当日无事件); 彻底失败抛异常"""
    import time as _time
    hdr = {
        "accept": "application/vnd.finance-web.v1+json",
        "origin": "https://finance.baidu.com",
        "referer": "https://finance.baidu.com/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    }
    rows, total = [], 0
    for pn in range(6):                     # 单日最多600条防御上限
        r = cffi_requests.get(
            MACRO_API,
            params={"start_date": date_iso, "end_date": date_iso, "pn": str(pn),
                    "rn": "100", "cate": cate, "finClientType": "pc"},
            headers=hdr, impersonate="chrome110", timeout=20)
        r.raise_for_status()
        info = r.json().get("Result", {}).get("calendarInfo", [])
        item = next((i for i in info if i.get("date") == date_iso), None)
        if item is None:
            break
        total = item.get("total", 0)
        lst = item.get("list") or []
        rows.extend(lst)
        if len(rows) >= total or not lst:
            break
        _time.sleep(1.5)                    # 翻页节流
    return rows


def create_macro_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS macro_events(
            trade_date TEXT NOT NULL,
            time TEXT,
            region TEXT NOT NULL,
            title TEXT NOT NULL,
            star INTEGER,
            pub_val TEXT,
            forecast_val TEXT,
            former_val TEXT,
            country TEXT,
            time_period TEXT,
            UNIQUE(trade_date, time, region, title)
        );
        CREATE TABLE IF NOT EXISTS macro_study_results(
            group_id TEXT NOT NULL,
            region TEXT, family TEXT, index_code TEXT, direction TEXT,
            window TEXT, n INTEGER, car_mean REAL,
            t_stat REAL, p_value REAL, sig TEXT, run_ts TEXT
        );
        CREATE TABLE IF NOT EXISTS macro_study_curves(
            group_id TEXT NOT NULL, rel_day INTEGER,
            aar REAL, caar REAL, n INTEGER, run_ts TEXT
        );
        CREATE TABLE IF NOT EXISTS macro_study_events(
            trade_date TEXT, time TEXT, region TEXT, family TEXT, title TEXT,
            index_code TEXT, t_day TEXT, included INTEGER, reason TEXT, run_ts TEXT
        );
        CREATE TABLE IF NOT EXISTS macro_study_meta(
            key TEXT PRIMARY KEY, value TEXT
        );
    """)
    conn.commit()


def save_macro_day(date_iso, items):
    """单日事件入库(幂等: UNIQUE键 + INSERT OR REPLACE, 重采覆盖更新实际值)"""
    conn = get_db()
    create_macro_tables(conn)
    n = 0
    for x in items:
        try:
            star = int(x.get('star')) if x.get('star') not in (None, '') else None
        except (TypeError, ValueError):
            star = None
        conn.execute(
            "INSERT OR REPLACE INTO macro_events "
            "(trade_date, time, region, title, star, pub_val, forecast_val, "
            " former_val, country, time_period) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (x.get('date'), x.get('time') or '', x.get('region') or '',
             x.get('title') or '', star,
             x.get('pubVal'), x.get('indicateVal'), x.get('formerVal'),
             x.get('country'), x.get('timePeriod')))
        n += 1
    conn.commit()
    conn.close()
    return n


def collect_macro_events(dates, force=False, log=print, sleep_day=2.5):
    """批量采集: 已入库日期跳过(最近MACRO_RECENT_REFRESH天除外, 其实际值会补填)
    返回 {'ok','skipped','failed','rows'}"""
    import time as _time
    conn = get_db()
    create_macro_tables(conn)
    have = {r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM macro_events")}
    conn.close()
    today = datetime.now().strftime('%Y-%m-%d')
    recent = [(datetime.strptime(today, '%Y-%m-%d') - timedelta(days=i)
               ).strftime('%Y-%m-%d') for i in range(MACRO_RECENT_REFRESH)]
    stats = {'ok': 0, 'skipped': 0, 'failed': 0, 'rows': 0}
    for d in dates:
        if d in have and not force and d not in recent:
            stats['skipped'] += 1
            continue
        rows = None
        for attempt in (1, 2):             # 失败等待后重试一次
            try:
                rows = fetch_macro_day(d)
                break
            except Exception as e:
                if attempt == 2:
                    log(f"  [macro] {d} 采集失败: {e}")
                else:
                    _time.sleep(8)
        if rows is None:
            stats['failed'] += 1
            continue
        stats['rows'] += save_macro_day(d, rows)
        stats['ok'] += 1
        _time.sleep(sleep_day)             # 日间节流
    return stats


def backfill_macro_events(start=MACRO_BACKFILL_START, end=None, log=print):
    """全窗口回填: 默认 回测窗口起点 ~ 今日; 幂等可断点续采"""
    end = end or datetime.now().strftime('%Y-%m-%d')
    d0 = datetime.strptime(start, '%Y-%m-%d')
    d1 = datetime.strptime(end, '%Y-%m-%d')
    dates = [(d0 + timedelta(days=i)).strftime('%Y-%m-%d')
             for i in range((d1 - d0).days + 1)]
    log(f"[macro] 回填 {start} ~ {end}: {len(dates)} 天")
    stats = collect_macro_events(dates, log=log)
    log(f"[macro] 完成: 新采 {stats['ok']} 天 / {stats['rows']} 行, "
        f"跳过 {stats['skipped']}, 失败 {stats['failed']}")
    return stats


def get_macro_status():
    """采集状态: 总行数/覆盖区间/高重要性数/地区清单"""
    conn = get_db()
    create_macro_tables(conn)
    r = conn.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) "
                     "FROM macro_events").fetchone()
    hi = conn.execute("SELECT COUNT(*) FROM macro_events WHERE star=?",
                      (MACRO_STAR_HIGH,)).fetchone()[0]
    regions = [x[0] for x in conn.execute(
        "SELECT DISTINCT region FROM macro_events WHERE region != '' "
        "ORDER BY region")]
    conn.close()
    return {'rows': r[0], 'start': r[1], 'end': r[2],
            'high': hi, 'regions': regions}


def query_macro_events(date_from, date_to, regions=None, star_min=None):
    """看板日历浏览器查询"""
    conn = get_db()
    create_macro_tables(conn)
    sql = ("SELECT trade_date, time, region, title, star, pub_val, "
           "forecast_val, former_val FROM macro_events "
           "WHERE trade_date >= ? AND trade_date <= ?")
    args = [date_from, date_to]
    if regions:
        sql += f" AND region IN ({','.join('?' * len(regions))})"
        args += list(regions)
    if star_min:
        sql += " AND star >= ?"
        args.append(star_min)
    sql += " ORDER BY trade_date, time, region"
    df = pd.read_sql_query(sql, conn, params=args)
    conn.close()
    return df


# ---- 事件 → 交易日映射 (北京时间语义, 见 模块6开发计划.md 3.3) ----
def _us_nth_sunday(year, month, n):
    d = datetime(year, month, 1)
    first_sun = 1 + (6 - d.weekday()) % 7   # weekday: Mon=0..Sun=6
    return first_sun + 7 * (n - 1)


def _us_offset_hours(d):
    """北京→美东时差: 夏令时(EDT)12h, 冬令时(EST)13h
    EDT: 3月第2个周日 ~ 11月第1个周日"""
    s = datetime(d.year, 3, _us_nth_sunday(d.year, 3, 2)).date()
    e = datetime(d.year, 11, _us_nth_sunday(d.year, 11, 1)).date()
    return 12 if s <= d < e else 13


def macro_event_trading_day(ev_date, ev_time, market, cal):
    """事件(北京时间日期+时刻) → 该市场的交易日(吸附到日历cal, 排序date列表)
    规则: 事件当地时刻 ≤ 收盘(A股15:00/港股16:00/美股16:00 ET) → 当地当日,
    否则次日; 美股先做北京→ET换算(含夏令时, 冬令时北京00:30=前一日11:30 ET)"""
    t = ev_time if ev_time else dtime(12, 0)
    dt = datetime.combine(ev_date, t)
    if market == 'us':
        et = dt - timedelta(hours=_us_offset_hours(dt.date()))
        if (et.hour, et.minute) <= (16, 0):
            cand = et.date()
        else:
            cand = et.date() + timedelta(days=1)
    elif market == 'cn':
        cand = dt.date() if (dt.hour, dt.minute) <= (15, 0) \
            else dt.date() + timedelta(days=1)
    elif market == 'hk':
        cand = dt.date() if (dt.hour, dt.minute) <= (16, 0) \
            else dt.date() + timedelta(days=1)
    else:
        return None
    for d in cal:                           # 吸附到下一个交易日(含当日)
        if d >= cand:
            return d
    return None


# ---- 事件研究: 常数均值模型 (标的=指数自身, 区别于模块4的市场模型) ----
def _macro_family(title):
    for pat, fam in MACRO_FAMILY_RULES:
        if re.search(pat, title):
            return fam
    return '其他'


def _macro_num(s):
    """解析公布/预期/前值: '2.92'/'51.5'/'13.5万'/'-0.3%' → float"""
    if s is None:
        return None
    t = str(s).strip().replace(',', '').replace('%', '')
    if not t or t in {'-', '—', '前值', '预期', '公布'}:
        return None
    m = re.match(r'^([+-]?[\d.]+)\s*(万|亿)?', t)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    if m.group(2) == '万':
        v *= 1e4
    elif m.group(2) == '亿':
        v *= 1e8
    return v


def _macro_direction(pub, fcst):
    p, f = _macro_num(pub), _macro_num(fcst)
    if p is None or f is None:
        return None
    if p > f:
        return '高于预期'
    if p < f:
        return '低于预期'
    return '符合预期'


def _macro_ar(rets, i0):
    """常数均值模型 AR: 估计窗[-120,-10]均值μ, AR_t = R_t − μ
    复用模块4的 EST_WIN/EVENT_WIN/MIN_EST_OBS 口径"""
    est_lo, est_hi = max(1, i0 + EST_WIN[0]), i0 + EST_WIN[1]
    est = [rets[i] for i in range(est_lo, est_hi + 1) if i < len(rets)]
    if len(est) < MIN_EST_OBS:
        return None, '估计窗样本不足'
    mu = float(np.mean(est))
    ar = {}
    for rel in EVENT_WIN:
        i = i0 + rel
        if 1 <= i < len(rets):
            ar[rel] = rets[i] - mu
    if 0 not in ar:
        return None, '无Day0行情'
    return ar, None


def run_macro_study(log=print, end_date=None):
    """宏观事件研究: 高重要性事件 × 映射指数, 家族分组 + 意外方向拆分
    AR/CAR/曲线统计复用共享统计核心 _agg_group / _t_test; 结果写 macro_study_* 表
    end_date(模块9诊断对照): 只纳入 trade_date <= end_date 的事件; 默认 None=全窗口
    注意: 本函数整表重建 macro_study_*, 诊断对照后必须以 end_date=None 重跑恢复全窗口"""
    conn = get_db()
    create_macro_tables(conn)
    # 指数序列: code → (dates列表, rets列表, date→idx)
    idx = {}
    for code, market in [('.INX', 'us'), ('HSI', 'hk'), ('sh000300', 'cn')]:
        rows = conn.execute(
            "SELECT trade_date, close FROM benchmark_index "
            "WHERE code=? ORDER BY trade_date", (code,)).fetchall()
        if not rows:
            log(f"  [macro] 指数 {code} 无数据, 跳过")
            continue
        dates = [datetime.strptime(r['trade_date'], '%Y-%m-%d').date()
                 for r in rows]
        closes = [r['close'] for r in rows]
        rets = [None] + [closes[i] / closes[i - 1] - 1
                         for i in range(1, len(closes))]
        idx[code] = {'market': market, 'dates': dates, 'rets': rets,
                     'dmap': {d: i for i, d in enumerate(dates)}}
    if not idx:
        conn.close()
        raise RuntimeError("benchmark_index 无指数数据, 先采集三大指数")
    # 高重要性事件 → 逐事件×逐指数映射交易日并算 AR
    ev_sql = ("SELECT trade_date, time, region, title, pub_val, forecast_val "
              "FROM macro_events WHERE star=?")
    ev_args = [MACRO_STAR_HIGH]
    if end_date:
        ev_sql += " AND trade_date <= ?"
        ev_args.append(end_date)
    evs = conn.execute(ev_sql + " ORDER BY trade_date, time",
                       ev_args).fetchall()
    groups = {}                             # (region, family, index, direction) → [ev]
    ev_rows, run_ts = [], datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for r in evs:
        if r['region'] not in MACRO_REGION_INDEX:
            continue
        fam = _macro_family(r['title'])
        direction = _macro_direction(r['pub_val'], r['forecast_val'])
        ev_date = datetime.strptime(r['trade_date'], '%Y-%m-%d').date()
        et = None
        if r['time']:
            hh, mm = (r['time'].split(':') + ['0'])[:2]
            et = dtime(int(hh), int(mm))
        for code in MACRO_REGION_INDEX[r['region']]:
            ser = idx.get(code)
            if ser is None:
                continue
            t_day = macro_event_trading_day(ev_date, et, ser['market'],
                                            ser['dates'])
            if t_day is None:
                ev_rows.append((r['trade_date'], r['time'], r['region'], fam,
                                r['title'], code, None, 0, '事件超出指数数据末端', run_ts))
                continue
            i0 = ser['dmap'][t_day]
            ar, why = _macro_ar(ser['rets'], i0)
            ev_rows.append((r['trade_date'], r['time'], r['region'], fam,
                            r['title'], code, t_day.isoformat(),
                            1 if ar else 0, why or '', run_ts))
            if ar is None:
                continue
            ev = {'ar': ar, 'date': r['trade_date'], 'title': r['title'],
                  'direction': direction}
            for dirn in ([None, direction] if direction else [None]):
                key = (r['region'], fam, code, dirn)
                groups.setdefault(key, []).append(ev)
                key_all = (r['region'], '全部高重要性', code, dirn)
                groups.setdefault(key_all, []).append(ev)
    conn.executescript("DELETE FROM macro_study_results; "
                       "DELETE FROM macro_study_curves; "
                       "DELETE FROM macro_study_events;")
    n_groups = 0
    for key, evs_g in sorted(groups.items(),
                              key=lambda kv: tuple(str(x) for x in kv[0])):
        region, family, code, dirn = key
        if family != '全部高重要性' and family == '其他':
            continue                        # '其他'仅保留在事件明细, 不进统计
        g = _agg_group(key, evs_g, 'macro')
        gid = f"{family}|{region}|{code}|{dirn or '全部'}"
        for w in g['windows']:
            conn.execute(
                "INSERT INTO macro_study_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (gid, region, family, code, dirn, w['window'], w['n'],
                 w['car_mean'], w['t_stat'], w['p_value'], w['sig'], run_ts))
        for rel, aar, caar, n in zip(g['curve']['rel_days'], g['curve']['aar'],
                                     g['curve']['caar'], g['curve']['n_by_day']):
            conn.execute("INSERT INTO macro_study_curves VALUES (?,?,?,?,?,?)",
                         (gid, rel, aar, caar, n, run_ts))
        n_groups += 1
    conn.executemany("INSERT INTO macro_study_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                     ev_rows)
    meta = {
        'run_ts': run_ts,
        'study_end_date': end_date or 'full',   # 模块9: 事件窗口截断标记(诊断对照/恢复时可追溯)
        'star_high': MACRO_STAR_HIGH,
        'region_index': {k: v for k, v in MACRO_REGION_INDEX.items()},
        'est_win': list(EST_WIN), 'event_win': [EVENT_WIN[0], EVENT_WIN[-1]],
        'min_est_obs': MIN_EST_OBS, 'ar_model': '常数均值(指数自身估计窗均值)',
        'direction_rule': '公布vs预期可解析时拆分, 否则仅全部分组',
        'n_events': len(evs), 'n_groups': n_groups,
        'source': '百度股市通 sapi (审计 2026-09-02, 见 经济日历数据源审计.md)',
        'caveat': '观察性研究: 事件后窗口含后续事件影响, 小样本家族结论仅供参考',
    }
    for k, v in meta.items():
        conn.execute("INSERT OR REPLACE INTO macro_study_meta VALUES (?,?)",
                     (k, json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v))
    conn.commit()
    conn.close()
    log(f"[macro] 研究完成: 高重要性事件 {len(evs)} 条 → {n_groups} 组统计入库")
    return meta


def load_macro_study():
    """看板回读: results/curves/events DataFrame + meta dict"""
    conn = get_db()
    create_macro_tables(conn)
    res = pd.read_sql_query("SELECT * FROM macro_study_results", conn)
    cur = pd.read_sql_query("SELECT * FROM macro_study_curves", conn)
    evs = pd.read_sql_query("SELECT * FROM macro_study_events", conn)
    meta = {k: json.loads(v[0]) if v and v[0].startswith(('"', '[', '{')) else (v[0] if v else None)
            for k, v in ((r[0], [r[1]]) for r in conn.execute(
                "SELECT key, value FROM macro_study_meta"))}
    conn.close()
    return {'results': res, 'curves': cur, 'events': evs, 'meta': meta}


def _unit_test_backtest():
    """D1单元验证: 10日人工样本手算对照, 净值逐点误差须为0
    场景1: 单信号hold=3/K=1/fee=0 → 手算净值序列
    场景2: fee=0.001 → 手算含费收益
    场景3: K=1双信号冲突 → 第2笔被拒
    返回 (通过数, 失败明细)"""
    dates = [f'2026-01-{i:02d}' for i in range(1, 11)]
    opens = [100.0, 100, 100, 101, 101, 101, 105, 105, 105, 105]
    closes = [100.0, 100, 100, 102, 103, 104, 104, 105, 105, 105]
    data = {'stocks': {'TEST': {'market': '测试', 'dates': dates, 'opens': opens,
                                'closes': closes,
                                'date_idx': {d: i for i, d in enumerate(dates)}}},
            'calendar': dates, 'signals': set(), 'index_series': {}}
    results = []

    # 场景1: 信号日=d3(idx2), T+1=d4开盘买101, 持3日→d7开盘卖105
    res1 = _run_strategy_bt(data, [('TEST', dates[2])], 3, 1, 0.0)
    expect = [1, 1, 1, 102 / 101, 103 / 101, 104 / 101, 105 / 101,
              105 / 101, 105 / 101, 105 / 101]
    for i, (a, b) in enumerate(zip(res1['equity'], expect)):
        if abs(a - b) > 1e-12:
            results.append(f'场景1 净值点{i}: 引擎={a:.10f} 手算={b:.10f}')
    t = res1['trades'][0] if res1['trades'] else {}
    if t.get('entry_price') != 101 or t.get('exit_price') != 105:
        results.append(f"场景1 成交价: {t.get('entry_price')}→{t.get('exit_price')} 应为101→105")
    if abs(t.get('ret', 0) - (105 / 101 - 1)) > 1e-12:
        results.append(f"场景1 收益: {t.get('ret')} 应为{105 / 101 - 1}")
    if abs(t.get('stat_ret', 0) - (104 / 100 - 1)) > 1e-12:
        results.append(f"场景1 统计口径收益: {t.get('stat_ret')} 应为{104 / 100 - 1}")

    # 场景2: fee=0.001 → 收益 = 105*0.999/(101*1.001)-1
    res2 = _run_strategy_bt(data, [('TEST', dates[2])], 3, 1, 0.001)
    expect_ret = 105 * 0.999 / (101 * 1.001) - 1
    t2 = res2['trades'][0] if res2['trades'] else {}
    if abs(t2.get('ret', 0) - expect_ret) > 1e-12:
        results.append(f'场景2 含费收益: {t2.get("ret")} 应为{expect_ret}')

    # 场景3: K=1, 两信号(d3触发/d4触发), 第二笔d5开仓时槽满被拒
    res3 = _run_strategy_bt(data, [('TEST', dates[2]), ('TEST', dates[3])], 3, 1, 0.0)
    if len(res3['trades']) != 1 or res3['rejected'] != 1:
        results.append(f"场景3 冲突处理: trades={len(res3['trades'])} "
                       f"rejected={res3['rejected']} 应为1笔+1拒")

    # 场景4: 数据末端未完成持仓放弃(信号日=idx9, T+1已超界)
    res4 = _run_strategy_bt(data, [('TEST', dates[9])], 3, 1, 0.0)
    if len(res4['trades']) != 0 or res4['end_dropped'] != 1:
        results.append(f"场景4 末端放弃: trades={len(res4['trades'])} "
                       f"end_dropped={res4['end_dropped']}")

    return (4 - len(results), results)


def _smoke_test_backtest():
    """D1冒烟: 全量策略+双基准执行, 打印核心指标(不入库)"""
    out = run_all_backtests()
    r = out['range']
    print(f"回测区间: {r['start']} ~ {r['end']} ({r['n_days']}个并集交易日, {r['n_stocks']}只股票)")
    print(f"参数: 最大持仓{out['params']['max_positions']} 费率{out['params']['fee']}")
    print()
    print(f"{'对象':<24}{'总收益':>10}{'年化':>10}{'回撤':>9}{'夏普':>8}"
          f"{'笔数':>6}{'均收益':>9}{'跳空成本':>9}")
    for sid, res in out['strategies'].items():
        m = res['metrics']
        print(f"{sid} {res['strategy_name'][:14]:<16}"
              f"{m['total_return']*100:>9.2f}%{m['annual_return']*100:>9.2f}%"
              f"{m['max_drawdown']*100:>8.2f}%"
              f"{(m['sharpe'] if m['sharpe'] is not None else 0):>8.2f}"
              f"{m.get('n_trades', 0):>6}"
              f"{(m.get('avg_trade_ret', 0) or 0)*100:>8.2f}%"
              f"{(m.get('avg_gap_cost', 0) or 0)*100:>8.2f}%")
    for bid, res in out['baselines'].items():
        m = res['metrics']
        print(f"{bid} {res['name'][:14]:<16}"
              f"{m['total_return']*100:>9.2f}%{m['annual_return']*100:>9.2f}%"
              f"{m['max_drawdown']*100:>8.2f}%"
              f"{(m['sharpe'] if m['sharpe'] is not None else 0):>8.2f}"
              f"{'—':>6}{'—':>9}{'—':>9}")
    print()
    print("触发/放弃明细:")
    for sid, res in out['strategies'].items():
        print(f"  {sid}: 触发{res['n_triggers']} 成交{len(res['trades'])} "
              f"满仓拒绝{res['rejected']} 末端放弃{res['end_dropped']}")
    return out


# ============================================================
# 模块5 D2: 批量回测入库 (知识截止日协议, 详见 模块5开发计划.md D2.0)
# full = 全窗口工程验证栏; oos = 知识截止日后证据参考栏(净值从1重起)
# 红线: 冻结日 2026-08-31 后禁止依回测结果回头调参; 禁止UPDATE已入库结果
# ============================================================

def _filter_bt_data(data, start_date):
    """按知识截止日截取回测数据视图: 并集日历仅保留 start_date 之后,
    股票/指数序列保留完整索引(成交与统计口径收益仍按各市场真实日历计算)"""
    return {'stocks': data['stocks'],
            'calendar': [d for d in data['calendar'] if d > start_date],
            'signals': data['signals'],
            'index_series': data['index_series']}


def create_backtest_tables(conn):
    """建4张回测表(2026-09-04 模块8起改为多批次追加模式: 已存在则跳过, 不再DROP整批重建)
    旧行为(整批DROP重建)与 run_d2_backtests 的同日幂等/跨日保留逻辑矛盾——
    每次重跑都会清掉全部历史批次, 冻结批次 2026-09-03 因此丢失过一次(从备份恢复)
    forward run 属追加式入库, 由独立入口写入, 不触碰本组表"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_runs(
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id TEXT NOT NULL,
            strategy_name TEXT,
            kind TEXT,
            segment TEXT NOT NULL,
            fee REAL,
            start_date TEXT, end_date TEXT, n_days INTEGER,
            total_return REAL, annual_return REAL, annual_vol REAL, sharpe REAL,
            max_drawdown REAL, mdd_start TEXT, mdd_end TEXT,
            daily_win_rate REAL, seg1_annual REAL, seg2_annual REAL,
            n_trades INTEGER, avg_trade_ret REAL, avg_stat_ret REAL,
            avg_gap_cost REAL, avg_fund_util REAL,
            n_triggers INTEGER, n_rejected INTEGER, n_end_dropped INTEGER,
            excess_vs_bh REAL, excess_vs_index REAL,
            params_json TEXT,
            run_date TEXT, generated_at TEXT
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_equity(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER, trade_date TEXT,
            strategy_value REAL, bh_value REAL, index_value REAL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_trades(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER, strategy_id TEXT, segment TEXT, fee REAL,
            code TEXT, market TEXT,
            trigger_date TEXT, entry_date TEXT, entry_price REAL,
            exit_date TEXT, exit_price REAL,
            return_pct REAL, stat_ret REAL, gap_cost REAL,
            holding_days INTEGER, generated_at TEXT
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_meta(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meta_key TEXT,
            result_json TEXT,
            run_date TEXT, generated_at TEXT
        )""")


def _d2_protocol_json():
    """D2.0 协议参数(随结果入库, 看板与后续会话可直接回读)"""
    return {
        'knowledge_cutoff': BT_KNOWLEDGE_CUTOFF,
        'freeze_date': BT_FREEZE_DATE,
        'cutoff_rule': ('266个信号交易日的70%分位; 样本内 2025-07-29~2026-05-01(186天), '
                        '样本外 2026-05-04~2026-08-26(80天)'),
        'honesty_note': ('参数实际在全窗口选出, oos段属回溯性伪样本外, 仅能检验regime稳健性; '
                         '严格意义样本外=2026-09起的forward test(看板每日自动累积)'),
        'segment_semantics': {
            'full': '工程验证栏: 验证T+1成交/K槽位/费率/跳空成本等实现机制, 收益仅作机制对照, 不作业绩证据',
            'oos': '证据参考栏: 净值从1重起, 对照同窗口重算的B1/B2, 供第二阶段特征池取舍',
        },
        'freeze_rule': ('冻结日后禁止依回测结果回头调整参数/池标准/策略定义; '
                        '问题记观察名单, 由forward样本裁决'),
        'append_rule': 'forward run 以 run_date 追加式入库, 禁止UPDATE已入库的full/oos结果',
        'fees': BT_FEE_SENSITIVITY,
        'strategies': {sid: s['name'] for sid, s in BT_STRATEGIES.items()},
        'overlay_note': ('S2M/S1aM(模块9) = 原策略+宏观封锁日不开新仓(只做减法); '
                         'overlay家族经FDR(q=0.05分层BH)选出, 研究窗口含知识截止后数据, '
                         '故 full/oos 两段均为机制对照(选择泄漏), 唯一裁决=forward test'),
        'baselines': {'B1': '买入持有等权(各股自身首个回测日开盘建仓, oos段在截止日后首个交易日再建仓)',
                      'B2': '三市场指数等权日收益合成(休市日贡献0)'},
    }


def run_d2_backtests():
    """D2批量回测: 全部策略×2段×3费率 + 双基准×2段, 全量入库
    每段基准 B1/B2 同窗口重算(不计费); 策略按费率档位逐一执行
    入库: backtest_runs(指标) / backtest_equity(日净值, 含同段B1/B2对照列) /
          backtest_trades(交易明细) / backtest_meta(协议参数 + 双段完整结果JSON)"""
    data = _load_backtest_data()
    # 封锁日集必须在首个写事务开始前预载: 批量入库是数万行未提交大事务,
    # 缓存溢出后SQLite持EXCLUSIVE锁, 事务中途新连接读台账必被拒(问题日志#3)
    overlay_blocked = _load_overlay_blocked(data)
    conn = get_db()
    create_backtest_tables(conn)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    run_date = datetime.now().strftime('%Y-%m-%d')
    # 同日批次幂等: 批次由 run_date 标识, 同日重跑先清旧批再入库;
    # 跨日批次保留作对照(模块8扩窗批次 vs 2026-09-03 冻结批次)
    old_ids = [r[0] for r in conn.execute(
        "SELECT run_id FROM backtest_runs WHERE run_date=?", (run_date,)).fetchall()]
    if old_ids:
        marks = ",".join("?" * len(old_ids))
        conn.execute(f"DELETE FROM backtest_equity WHERE run_id IN ({marks})", old_ids)
        conn.execute(f"DELETE FROM backtest_trades WHERE run_id IN ({marks})", old_ids)
        conn.execute("DELETE FROM backtest_runs WHERE run_date=?", (run_date,))
        conn.execute("DELETE FROM backtest_meta WHERE run_date=?", (run_date,))
    summary = {'runs': 0, 'equity_rows': 0, 'trade_rows': 0, 'segments': {}}

    def insert_run(kind, sid, name, seg, fee, res, cal, bh_eq, idx_eq,
                   bh_total, idx_total, params):
        m = res['metrics']
        total = m.get('total_return')
        cur = conn.execute(
            "INSERT INTO backtest_runs "
            "(strategy_id, strategy_name, kind, segment, fee, "
            "start_date, end_date, n_days, "
            "total_return, annual_return, annual_vol, sharpe, "
            "max_drawdown, mdd_start, mdd_end, "
            "daily_win_rate, seg1_annual, seg2_annual, "
            "n_trades, avg_trade_ret, avg_stat_ret, avg_gap_cost, avg_fund_util, "
            "n_triggers, n_rejected, n_end_dropped, "
            "excess_vs_bh, excess_vs_index, params_json, run_date, generated_at, "
            "n_macro_blocked) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, name, kind, seg, fee, cal[0], cal[-1], len(cal), total,
             m.get('annual_return'), m.get('annual_vol'), m.get('sharpe'),
             m.get('max_drawdown'), m.get('mdd_start'), m.get('mdd_end'),
             m.get('daily_win_rate'), m.get('seg1_annual'), m.get('seg2_annual'),
             m.get('n_trades', 0), m.get('avg_trade_ret'), m.get('avg_stat_ret'),
             m.get('avg_gap_cost'), m.get('avg_fund_util'),
             res.get('n_triggers', 0), res.get('rejected', 0),
             res.get('end_dropped', 0),
             (total - bh_total) if total is not None else None,
             (total - idx_total) if total is not None else None,
             json.dumps(params, ensure_ascii=False, default=str),
             run_date, now, res.get('macro_blocked', 0)))
        run_id = cur.lastrowid
        for i, d in enumerate(cal):
            conn.execute(
                "INSERT INTO backtest_equity "
                "(run_id, trade_date, strategy_value, bh_value, index_value) "
                "VALUES(?,?,?,?,?)",
                (run_id, d, res['equity'][i], bh_eq[i], idx_eq[i]))
        summary['equity_rows'] += len(cal)
        for t in res.get('trades', []):
            conn.execute(
                "INSERT INTO backtest_trades "
                "(run_id, strategy_id, segment, fee, code, market, trigger_date, "
                "entry_date, entry_price, exit_date, exit_price, return_pct, "
                "stat_ret, gap_cost, holding_days, generated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, sid, seg, fee, t['code'], t['market'],
                 t['trigger_date'], t['entry_date'], t['entry_price'],
                 t['exit_date'], t['exit_price'], t['ret'], t['stat_ret'],
                 t['gap_cost'], t['hold_days'], now))
        summary['trade_rows'] += len(res.get('trades', []))
        summary['runs'] += 1
        return run_id

    for seg, start_date in BT_SEGMENTS:
        seg_data = _filter_bt_data(data, start_date) if start_date else data
        cal = seg_data['calendar']
        bh_eq = _buy_hold_baseline(seg_data, start_date)
        idx_eq = _index_baseline(seg_data)
        baselines = {
            'B1': {'name': '基准·买入持有等权', 'equity': bh_eq,
                   'metrics': _perf_metrics(bh_eq, cal)},
            'B2': {'name': '基准·大盘指数等权', 'equity': idx_eq,
                   'metrics': _perf_metrics(idx_eq, cal)},
        }
        bh_total = baselines['B1']['metrics']['total_return']
        idx_total = baselines['B2']['metrics']['total_return']
        base_params = {'max_positions': BT_MAX_POSITIONS,
                       'knowledge_cutoff': BT_KNOWLEDGE_CUTOFF,
                       'segment_start': cal[0]}
        for bid, bres in baselines.items():
            insert_run('baseline', bid, bres['name'], seg, 0.0,
                       {'equity': bres['equity'], 'metrics': bres['metrics']},
                       cal, bh_eq, idx_eq, bh_total, idx_total,
                       dict(base_params, baseline=bid))
        fee0 = {}
        for sid, spec in BT_STRATEGIES.items():
            for fee in BT_FEE_SENSITIVITY:
                res = run_backtest(sid, BT_MAX_POSITIONS, fee, data, start_date,
                                   blocked_days=overlay_blocked)
                if fee == 0.0:
                    fee0[sid] = res
                insert_run('strategy', sid, spec['name'], seg, fee, res, cal,
                           bh_eq, idx_eq, bh_total, idx_total,
                           dict(base_params, fee=fee,
                                spec={k: v for k, v in spec.items() if k != 'name'}))
        seg_json = {
            'range': {'start': cal[0], 'end': cal[-1], 'n_days': len(cal),
                      'n_stocks': len(seg_data['stocks'])},
            'params': dict(base_params, fee=0.0),
            'strategies': {sid: {'name': BT_STRATEGIES[sid]['name'],
                                 'metrics': r['metrics'], 'equity': r['equity'],
                                 'trades': r['trades'],
                                 'n_triggers': r['n_triggers'],
                                 'rejected': r['rejected'],
                                 'end_dropped': r['end_dropped'],
                                 'macro_blocked': r.get('macro_blocked', 0)}
                           for sid, r in fee0.items()},
            'baselines': {bid: {'name': b['name'], 'metrics': b['metrics'],
                                'equity': b['equity']}
                          for bid, b in baselines.items()},
        }
        conn.execute(
            "INSERT INTO backtest_meta(meta_key, result_json, run_date, generated_at) "
            "VALUES(?,?,?,?)",
            (f'segment_{seg}',
             json.dumps(seg_json, ensure_ascii=False, default=str),
             run_date, now))
        summary['segments'][seg] = {
            'range': seg_json['range'],
            'bh_total_return': bh_total, 'idx_total_return': idx_total,
            'strategies': {sid: r['metrics'] for sid, r in fee0.items()},
        }
        print(f"[D2] {seg} 段完成: {cal[0]}~{cal[-1]} {len(cal)}日, "
              f"B1 {bh_total*100:+.2f}% / B2 {idx_total*100:+.2f}%")

    conn.execute(
        "INSERT INTO backtest_meta(meta_key, result_json, run_date, generated_at) "
        "VALUES(?,?,?,?)",
        ('d2_protocol',
         json.dumps(_d2_protocol_json(), ensure_ascii=False, default=str),
         run_date, now))
    conn.commit()
    conn.close()
    return summary


def load_backtest_meta(meta_key=None):
    """回读 backtest_meta(协议参数 / 双段完整结果JSON), 供看板与测试回读"""
    conn = get_db()
    if meta_key:
        rows = conn.execute(
            "SELECT meta_key, result_json, run_date, generated_at "
            "FROM backtest_meta WHERE meta_key=?", (meta_key,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT meta_key, result_json, run_date, generated_at "
            "FROM backtest_meta").fetchall()
    conn.close()
    return {r['meta_key']: {'json': json.loads(r['result_json']),
                            'run_date': r['run_date'],
                            'generated_at': r['generated_at']}
            for r in rows}


# ============================================================
# 模块 7: Forward Test 前向测试基础设施 (2026-09-03)
# 设计/裁决规则预注册/表结构见 模块7开发计划.md
# 红线: 追加守卫(已入库日期只校验不改写) / 复用D1引擎口径 / 规则不得依结果修改
# ============================================================
FORWARD_START = '2026-08-28'         # 冻结回测批数据终点(2026-08-27)后首个交易日
FORWARD_B1_ENTRY_REF = '2026-08-27'  # B1 前向基准建仓参考日(其后各股首个交易日开盘建仓)
FORWARD_FEE = 0.001                  # 单边费率, 与 D2 呈现口径一致
FORWARD_ADJ_MIN_TRADES = 20          # 裁决门槛: forward 完成交易数
FORWARD_FRESHNESS_ALERT = 2          # 新鲜度告警阈值(落后交易日数 > 此值)


def create_forward_tables(conn):
    """模块7三表(IF NOT EXISTS, 幂等); B1/B2 以 strategy_id 行存于 forward_equity"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forward_trades(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id TEXT NOT NULL, code TEXT, market TEXT,
            trigger_date TEXT, entry_date TEXT, entry_price REAL,
            exit_date TEXT, exit_price REAL,
            return_pct REAL, stat_ret REAL, gap_cost REAL,
            holding_days INTEGER, run_ts TEXT,
            UNIQUE(strategy_id, code, trigger_date)
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forward_equity(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id TEXT NOT NULL, trade_date TEXT,
            nav REAL, run_ts TEXT,
            UNIQUE(strategy_id, trade_date)
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forward_meta(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meta_key TEXT, result_json TEXT,
            run_date TEXT, generated_at TEXT
        )""")


def _forward_protocol_json():
    """前向协议参数(首次运行时写入 forward_meta.protocol, 之后冻结不改)"""
    return {
        'forward_start': FORWARD_START,
        'b1_entry_ref': FORWARD_B1_ENTRY_REF,
        'fee': FORWARD_FEE,
        'max_positions': BT_MAX_POSITIONS,
        'strategies': {sid: s['name'] for sid, s in BT_STRATEGIES.items()},
        'adjudication': {
            'min_trades': FORWARD_ADJ_MIN_TRADES,
            'rules': ('forward完成交易数>=20后: excess_vs_B1<=0 且 交易胜率<50% → 淘汰建议; '
                      'excess_vs_B1>0 且 胜率>=50% → 保留(S1e为转正候选, 正式转正还需模块8时间分割验证); '
                      '否则继续观察'),
            'freeze_note': '裁决规则预注册于 2026-09-03, 不得依前向结果修改(与回测冻结协议同源)',
        },
        'append_rule': ('forward_equity 已入库日期只校验不改写; 数据修订以守卫告警可见化; '
                        '净值/交易只追加到全部数据源已终局的日期(完整性截断: 个股≤池内行情终局日最小值, '
                        'B2≤三市场指数终局日最小值; 卖出日未终局的交易不入库)'),
        'amendment': ('2026-09-03 完整性截断规则追加(问题日志#1): 修复美股未收盘时运行步进'
                      '导致 ffill 值入库、次日必然触发守卫误报的缺陷; 裁决规则与门槛未变'),
        'backtest_reference': {'freeze_date': BT_FREEZE_DATE,
                               'frozen_batch_end': '2026-08-27',
                               'seam_note': '跨界信号(trade_date<=2026-08-27)归冻结回测oos段, 前向净值从1重起'},
    }


def _forward_status():
    """回读 forward_meta.status(最近一次前向步进的状态快照, 派生数据可更新)"""
    conn = get_db()
    r = conn.execute("SELECT result_json FROM forward_meta WHERE meta_key='status' "
                     "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return json.loads(r[0]) if r else {}


def run_forward_step(log=print):
    """前向测试步进: 确定性重放前向窗口 + 完整性截断 + 追加守卫
    1) 全窗口重算(冻结参数+历史数据不变⇒结果确定) 2) 净值只追加到"全部数据源已终局"的日期
    (美股未收盘时其行情缺失由ffill补, 若照常入库则次日真实收盘到来必触发守卫告警)
    3) 已入库日期逐日校验NAV, 不一致记告警不改写 4) 新日期/新交易按唯一键追加
    5) 状态快照存 forward_meta.status
    运行前提: 管道已更新行情/信号/宽表(直接调用请先跑 run_daily_pipeline)
    2026-09-07 模块10: 重放数据=活跃池实时∪快照注入; 触发按资格区间过滤;
    B1 只由首批成员(首次join_eff<=FORWARD_START)构成 — 池扩容/离池零失配"""
    data = _forward_replay_data()
    membership = _forward_membership()
    fwd = {'stocks': data['stocks'],
           'calendar': [d for d in data['calendar'] if d >= FORWARD_START],
           'signals': data['signals'], 'index_series': data['index_series']}
    if not fwd['calendar']:
        return {'ok': False, 'error': '前向窗口无交易日数据(先运行采集管道)'}

    conn = get_db()
    create_forward_tables(conn)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cal = fwd['calendar']

    # 完整性截断(2026-09-03 问题日志#1): 个股净值截断日=池内全部股票行情终局日的最小值,
    # B2 截断日=三市场指数终局日的最小值; 读个股表必须走池过滤(单一事实源)
    pool_f, pool_args = _pool_sql_filter(_pool_code_set())
    stock_latest = [r[0] for r in conn.execute(
        f"SELECT MAX(trade_date) FROM daily_quotes WHERE {pool_f} GROUP BY code",
        pool_args)]
    index_latest = {r[0]: r[1] for r in conn.execute(
        "SELECT market, MAX(trade_date) FROM benchmark_index GROUP BY market")}
    stock_cap = min([d for d in stock_latest if d], default=None)
    index_cap = min([index_latest[m] for m in ('A股', '港股', '美股') if index_latest.get(m)],
                    default=None)
    if stock_cap is None or index_cap is None:
        conn.close()
        return {'ok': False, 'error': '行情或基准指数数据缺失, 无法确定完整性截断日'}
    cal_stock = [d for d in cal if d <= stock_cap]
    cal_index = [d for d in cal if d <= index_cap]
    if not cal_stock:
        conn.close()
        return {'ok': False, 'error': f'截断日({stock_cap})前无前向交易日, 检查行情采集'}

    # 协议首次写入后冻结
    if not conn.execute("SELECT 1 FROM forward_meta WHERE meta_key='protocol'").fetchone():
        conn.execute("INSERT INTO forward_meta(meta_key, result_json, run_date, generated_at) "
                     "VALUES('protocol',?,?,?)",
                     (json.dumps(_forward_protocol_json(), ensure_ascii=False, default=str),
                      datetime.now().strftime('%Y-%m-%d'), now))

    # 模块10: 池构成协议(独立meta键, DELETE+INSERT 幂等 — 模块9问题日志#4同源)
    conn.execute("DELETE FROM forward_meta WHERE meta_key='pool_protocol'")
    conn.execute("INSERT INTO forward_meta(meta_key, result_json, run_date, generated_at) "
                 "VALUES('pool_protocol',?,?,?)",
                 (json.dumps({
                     'membership_rule': ('信号(code,trade_date)计入前向 ⟺ 落在资格区间'
                                         '[join_eff,leave_eff)内(forward_pool_events 追加式); '
                                         '新股回填的历史信号不追溯, 前向样本自 join_eff 起积累'),
                     'b1_rule': ('B1 仅由首批成员(首次join_eff<=FORWARD_START)构成, 后加入者'
                                 '永不进入; 首批成员离池后其 B1 sleeve 由快照冻结在最后值'),
                     'leave_rule': ('离池/停用生效前快照冻结(forward_stock_px/sig), '
                                    '已关闭区间重放只用快照(防qfq重基准), 已入库 NAV 永不失配'),
                     'adjudication_unchanged': ('裁决规则/门槛/策略参数/B2定义均不随池构成变化; '
                                                '新增成员只增加触发样本来源'),
                     'valuation_rule': ('前向NAV为盯市口径: 出场日未到的持仓按最新收盘计市值'
                                        '(drop_incomplete=False), 净值含在持仓位; 完成交易在'
                                        '出场日行情终局(≤完整性截断日)后补录 forward_trades'),
                 }, ensure_ascii=False, default=str),
                  datetime.now().strftime('%Y-%m-%d'), now))

    # B1/B2 前向基准(同窗口重算); 总收益取截断日口径(未终局日期不入统计)
    # 模块10: B1 只由首批成员构成(excess_vs_B1 全窗口可比; 后加入者永不进入)
    first_batch = {c for c, ivs in membership.items()
                   if ivs and ivs[0][0] <= FORWARD_START and c in fwd['stocks']}
    b1_data = {'stocks': {c: fwd['stocks'][c] for c in first_batch},
               'calendar': fwd['calendar']}
    b1_eq = _buy_hold_baseline(b1_data, FORWARD_B1_ENTRY_REF)
    b2_eq = _index_baseline(fwd)
    b1_total = (b1_eq[len(cal_stock) - 1] / b1_eq[0] - 1
                if (len(cal_stock) >= 2 and b1_eq) else None)
    b2_total = (b2_eq[len(cal_index) - 1] / b2_eq[0] - 1) if len(cal_index) >= 2 else None

    # 守卫基准: 已入库 (strategy_id, trade_date) -> nav
    existing = {(r['strategy_id'], r['trade_date']): r['nav']
                for r in conn.execute("SELECT strategy_id, trade_date, nav FROM forward_equity")}

    # 模块9: 台账预载于首个写事务之前(问题日志#3同源: 前向窗口增长后
    # 循环内开新连接读台账会与大事务锁冲突)
    overlay_blocked = _load_overlay_blocked(fwd)

    report = {'ok': True, 'forward_start': FORWARD_START, 'n_days': len(cal),
              'window': [cal[0], cal[-1]],
              'append_caps': {'stocks': stock_cap, 'index': index_cap},
              'new_equity_rows': 0, 'new_trade_rows': 0,
              'mismatches': [], 'strategies': {}, 'generated_at': now}

    def cap_of(sid):
        return index_cap if sid == 'B2' else stock_cap

    stored_beyond = [(sid, d) for (sid, d) in existing if d > cap_of(sid)]
    if stored_beyond:
        report['stored_beyond_cap'] = stored_beyond

    def append_equity(sid, series, dates):
        mism = 0
        for d, nav in zip(dates, series):
            key = (sid, d)
            if key in existing:
                if abs(existing[key] - nav) > 1e-9:
                    report['mismatches'].append(
                        {'strategy': sid, 'date': d,
                         'stored': existing[key], 'new': nav})
                    mism += 1
            else:
                conn.execute("INSERT INTO forward_equity(strategy_id, trade_date, nav, run_ts) "
                             "VALUES(?,?,?,?)", (sid, d, float(nav), now))
                report['new_equity_rows'] += 1
        return mism

    status_strategies = {}
    for sid, spec in BT_STRATEGIES.items():
        # 模块10: 触发按资格区间过滤(离池区间/入池前历史信号不计入前向)
        triggers = [(c, d) for (c, d) in _collect_triggers(fwd, spec)
                    if d >= FORWARD_START and _member_on(membership, c, d)]
        # 模块9: overlay 变体用预载封锁日集(台账只追加不改写→重放确定性)
        blocked = overlay_blocked if spec.get('overlay') else None
        # 前向口径(2026-09-07 修复): 出场日未到的持仓盯市不丢弃 — 空仓1.0是引擎缺陷产物
        res = _run_strategy_bt(fwd, triggers, spec['hold'],
                               BT_MAX_POSITIONS, FORWARD_FEE, blocked_days=blocked,
                               drop_incomplete=False)
        metrics = _perf_metrics(res['equity'][:len(cal_stock)], cal_stock,
                                res['trades'], res['fund_util'])
        for t in res['trades']:
            if t['exit_date'] > stock_cap:
                continue  # 卖出日行情未终局(如美股未收盘), 待后续运行入库
            try:
                conn.execute(
                    "INSERT INTO forward_trades(strategy_id, code, market, trigger_date, "
                    "entry_date, entry_price, exit_date, exit_price, return_pct, stat_ret, "
                    "gap_cost, holding_days, run_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, t['code'], t['market'], t['trigger_date'], t['entry_date'],
                     t['entry_price'], t['exit_date'], t['exit_price'],
                     float(t['ret']), float(t['stat_ret']), float(t['gap_cost']),
                     t['hold_days'], now))
                report['new_trade_rows'] += 1
            except sqlite3.IntegrityError:
                pass  # 幂等业务键: 已入库交易跳过
        n_mism = append_equity(sid, res['equity'], cal_stock)
        capped_trades = [t for t in res['trades'] if t['exit_date'] <= stock_cap]
        win_rate = (sum(1 for t in capped_trades if t['ret'] > 0) / len(capped_trades)
                    if capped_trades else None)
        total = metrics.get('total_return')
        status_strategies[sid] = {
            'name': spec['name'], 'observation': bool(spec.get('observation')),
            'overlay': bool(spec.get('overlay')),
            'n_triggers': res['n_triggers'], 'n_trades': len(capped_trades),
            'rejected': res['rejected'], 'end_dropped': res['end_dropped'],
            'macro_blocked': res.get('macro_blocked', 0),
            'open_positions': res.get('open_positions', 0),
            'total_return': total, 'sharpe': metrics.get('sharpe'),
            'max_drawdown': metrics.get('max_drawdown'), 'win_rate': win_rate,
            'excess_vs_b1': (total - b1_total) if (total is not None and b1_total is not None) else None,
            'n_mismatch': n_mism,
        }
        report['strategies'][sid] = status_strategies[sid]
        log(f"  [forward] {sid} {spec['name']}: 触发{res['n_triggers']} 笔{len(capped_trades)} "
            f"总收益{total:+.2%}" if total is not None else f"  [forward] {sid}: 无数据")

    append_equity('B1', b1_eq, cal_stock)
    append_equity('B2', b2_eq, cal_index)

    status = {'window': report['window'], 'n_days': len(cal),
              'append_caps': report['append_caps'],
              'stored_beyond_cap': len(stored_beyond),
              'b1_total': b1_total, 'b2_total': b2_total,
              'strategies': status_strategies,
              'mismatches': report['mismatches'], 'generated_at': now}
    conn.execute("DELETE FROM forward_meta WHERE meta_key='status'")
    conn.execute("INSERT INTO forward_meta(meta_key, result_json, run_date, generated_at) "
                 "VALUES('status',?,?,?)",
                 (json.dumps(status, ensure_ascii=False, default=str),
                  datetime.now().strftime('%Y-%m-%d'), now))
    conn.commit()
    conn.close()
    if report['mismatches']:
        log(f"  [forward] 守卫告警: {len(report['mismatches'])} 条已入库NAV与新算不一致(不改写, 见status)")
    if stored_beyond:
        log(f"  [forward] 完整性告警: {len(stored_beyond)} 条已入库净值超出截断日"
            f"(个股≤{stock_cap}/指数≤{index_cap}), 为截断规则生效前误存, 需人工清理(模块7问题日志#1)")
    return report


def evaluate_watchlist():
    """观察名单裁决(S1d/S1e) — 规则预注册于 forward_meta.protocol, 不由人临时判断
    门槛: forward完成交易数 >= FORWARD_ADJ_MIN_TRADES"""
    status = _forward_status()
    conn = get_db()
    create_forward_tables(conn)
    out = []
    targets = [
        ('S1d', '回测冻结协议观察名单(oos -2.4%)'),
        ('S1e', '模块3状态=观察(验证中); 正式转正还需模块8时间分割验证'),
    ]
    for sid, note in targets:
        rows = conn.execute("SELECT return_pct FROM forward_trades WHERE strategy_id=?",
                            (sid,)).fetchall()
        n = len(rows)
        s = status.get('strategies', {}).get(sid, {})
        entry = {'strategy_id': sid, 'name': s.get('name', BT_STRATEGIES[sid]['name']),
                 'note': note, 'n_trades': n,
                 'min_trades': FORWARD_ADJ_MIN_TRADES,
                 'progress': f"{n}/{FORWARD_ADJ_MIN_TRADES}",
                 'win_rate': s.get('win_rate'), 'excess_vs_b1': s.get('excess_vs_b1')}
        if n >= FORWARD_ADJ_MIN_TRADES and s:
            win, excess = s.get('win_rate'), s.get('excess_vs_b1')
            if excess is not None and excess <= 0 and win is not None and win < 0.5:
                entry['verdict'] = '🔴 淘汰建议(前向证据)'
            elif excess is not None and excess > 0 and win is not None and win >= 0.5:
                entry['verdict'] = ('🟢 转正候选(前向证据)' if sid == 'S1e'
                                    else '🟢 保留(前向证据)')
            else:
                entry['verdict'] = '🟡 继续观察'
        else:
            entry['verdict'] = '⏳ 样本积累中'
        out.append(entry)
    conn.close()
    return out


def get_data_freshness():
    """数据新鲜度: 每股行情落后其市场指数的交易日数(指数日历=市场交易日历)
    lag > FORWARD_FRESHNESS_ALERT → alert; 指数/宏观滞后仅展示不告警"""
    conn = get_db()
    idx_dates = {}
    for m in ['A股', '港股', '美股']:
        idx_dates[m] = [r[0] for r in conn.execute(
            "SELECT trade_date FROM benchmark_index WHERE market=? ORDER BY trade_date", (m,))]
    stocks = []
    for s in get_stock_pool(active_only=True):
        ql = conn.execute("SELECT MAX(trade_date) FROM daily_quotes WHERE code=?",
                          (s['code'],)).fetchone()[0]
        lag = (sum(1 for d in idx_dates.get(s['market'], []) if d > ql)
               if ql else len(idx_dates.get(s['market'], [])))
        sl = conn.execute("SELECT MAX(trade_date) FROM passive_signals WHERE code=?",
                          (s['code'],)).fetchone()[0]
        stocks.append({'code': s['code'], 'name': s.get('name', ''),
                       'market': s['market'], 'quote_latest': ql,
                       'lag_trading_days': lag, 'signal_latest': sl,
                       'alert': lag > FORWARD_FRESHNESS_ALERT})
    benchmark = {m: ds[-1] for m, ds in idx_dates.items() if ds}
    macro_latest = conn.execute("SELECT MAX(trade_date) FROM macro_events").fetchone()[0]
    conn.close()
    return {'stocks': stocks, 'benchmark': benchmark, 'macro_latest': macro_latest,
            'alerts': [s for s in stocks if s['alert']],
            'threshold': FORWARD_FRESHNESS_ALERT}


def run_daily_pipeline(log=print):
    """模块7每日管道(收盘后运行): 孤儿股补课→行情→指数→指标→信号扫描→宽表→
    宏观→封锁日台账→前向记录→新鲜度(模块10起 9 步)
    各步独立 try/except(打印异常不静默), 单步失败不阻断后续; 报告存 forward_meta"""
    report = {'steps': {},
              'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

    def step(name, fn):
        try:
            report['steps'][name] = fn()
            log(f"  [pipeline] {name}: OK")
        except Exception as e:
            print(f"[pipeline:{name}] {type(e).__name__}: {e}")
            report['steps'][name] = {'error': f"{type(e).__name__}: {e}"}

    log("[pipeline] 开始每日管道...")
    # 模块10: 首步孤儿股补课(入池未引导/数据残缺 → 自动执行引导链, 同管道内补齐)
    step('onboard_check', lambda: _pipeline_onboard_check(log=log))
    step('quotes', lambda: batch_collect_daily())
    step('benchmark', lambda: batch_collect_benchmark())
    step('indicators', lambda: batch_generate_indicators())
    step('signals', lambda: run_signal_detection())
    step('wide_table', lambda: batch_generate_wide_tables())
    # 模块9: 宏观采集(最近N天强制重采, 公布值后填) + 封锁日台账追加(守卫=完整性截断日)
    _today = datetime.now().strftime('%Y-%m-%d')
    _recent = [(datetime.strptime(_today, '%Y-%m-%d') - timedelta(days=i)
                ).strftime('%Y-%m-%d') for i in range(MACRO_RECENT_REFRESH)]
    step('macro', lambda: collect_macro_events(_recent, log=log))
    _cap = _stock_cap_date()
    step('overlay_days', lambda: (refresh_overlay_days(min_date=_cap, log=log)
                                  if _cap else {'skipped': '无行情截断日, 跳过台账刷新'}))
    step('forward', lambda: run_forward_step(log=log))
    report['freshness'] = get_data_freshness()
    for s in report['freshness']['alerts']:
        log(f"  [pipeline] 新鲜度告警: {s['code']} 行情落后 {s['lag_trading_days']} 个交易日")

    conn = get_db()
    create_forward_tables(conn)
    conn.execute("INSERT INTO forward_meta(meta_key, result_json, run_date, generated_at) "
                 "VALUES('pipeline_last_run',?,?,?)",
                 (json.dumps({'steps': {k: ('OK' if not isinstance(v, dict) or 'error' not in v
                                            else v['error']) for k, v in report['steps'].items()},
                              'freshness': {'stocks': report['freshness']['stocks'],
                                            'alerts': len(report['freshness']['alerts'])},
                              'generated_at': report['generated_at']},
                             ensure_ascii=False, default=str),
                  datetime.now().strftime('%Y-%m-%d'), report['generated_at']))
    conn.commit()
    conn.close()
    log("[pipeline] 完成")
    return report


def get_pipeline_last_run():
    """回读 forward_meta.pipeline_last_run(最近一次每日管道报告), 无记录返回 None"""
    conn = get_db()
    create_forward_tables(conn)
    r = conn.execute("SELECT result_json FROM forward_meta WHERE meta_key='pipeline_last_run' "
                     "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if not r:
        return None
    try:
        return json.loads(r[0])
    except Exception as e:
        print(f"[get_pipeline_last_run] {e}")
        return None


def get_forward_view():
    """看板前向视图: 净值序列(策略+B1/B2) / 交易明细 / 状态快照 / 协议"""
    conn = get_db()
    create_forward_tables(conn)
    equity = {}
    for r in conn.execute("SELECT strategy_id, trade_date, nav FROM forward_equity "
                          "ORDER BY trade_date"):
        e = equity.setdefault(r['strategy_id'], {'dates': [], 'nav': []})
        e['dates'].append(r['trade_date'])
        e['nav'].append(r['nav'])
    trades = [dict(r) for r in conn.execute(
        "SELECT strategy_id, code, market, trigger_date, entry_date, entry_price, "
        "exit_date, exit_price, return_pct, holding_days FROM forward_trades "
        "ORDER BY trigger_date DESC, strategy_id LIMIT 300")]
    protocol_row = conn.execute("SELECT result_json FROM forward_meta "
                                "WHERE meta_key='protocol' LIMIT 1").fetchone()
    conn.close()
    return {'equity': equity, 'trades': trades, 'status': _forward_status(),
            'protocol': json.loads(protocol_row[0]) if protocol_row else None}


# ============================================================
# 模块 8: 样本扩展与信号回放 (2026-09-04)
# 行情回填至 2020(指标预热) → 信号回放自 2021(source='replay') →
# 模块3三口径统计 + RSI24时间分割验证 → 模块5冻结参数扩窗重跑(仅记录)
# 详见《模块8开发计划.md》; 冻结协议: 数据扩展允许, 参数/池/策略变更禁止
# ============================================================

QUOTE_BACKFILL_START = '2020-01-01'   # 行情回填起点(多回1年做指标预热)
REPLAY_START = '2021-01-01'          # 回放信号记录起点
MACRO_BACKFILL_TARGET = '2023-01-01' # 宏观回填目标(源深度限制, 2022-12前无数据)
M8_SIGNAL_SPLIT = 0.7                # RSI24时间分割: 前70%开发段/后30%验证段


def backfill_quotes_full(log=print):
    """模块8 行情回填: 池内股票回填至 QUOTE_BACKFILL_START
    复用现有采集函数(akshare 全历史 + qfq), INSERT OR REPLACE 全序列统一复权基准
    注意: 历史价格按最新前复权基准重写(分红导致的基准移动, 全序列口径统一)"""
    days = ((datetime.now() - datetime.strptime(QUOTE_BACKFILL_START, '%Y-%m-%d')).days + 5)
    stocks = get_stock_pool(active_only=True)
    out = []
    for s in stocks:
        code, market = s['code'], s.get('market', 'A股')
        try:
            if market == 'A股':
                n = collect_a_share_daily(code, days)
            elif market == '港股':
                n = collect_hk_daily(code, days)
            else:
                n = collect_us_daily(code, days)
        except Exception as e:
            print(f"[backfill_quotes_full] {code}: {type(e).__name__}: {e}")
            n = 0
        log(f"  [backfill] {code} {market}: {n} 行")
        out.append({'code': code, 'market': market, 'rows': n})
    return out


def replay_passive_signals(log=print):
    """模块8 信号回放: 在回填行情上全历史确定性重放 14 条被动规则
    - 指标在完整序列上重算(2020起, 保证2021信号的滚动窗口预热)
    - 只记录 trade_date >= REPLAY_START 的信号, source='replay'
    - 幂等: 复用既有业务键(code+date+type+subtype+direction), 已存在(live或replay)则跳过
      → 实采段(>=2025-07-29)既有 live 行不重不覆, 仅补缺口并如实标 replay
    - 重复运行零新增"""
    conn = get_db()
    before = conn.execute(
        "SELECT COUNT(*) FROM passive_signals WHERE source='replay'").fetchone()[0]
    conn.close()
    stocks = get_stock_pool(active_only=True)
    per_stock = []
    for s in stocks:
        code, market, name = s['code'], s.get('market', 'A股'), s.get('name', '')
        df = get_daily_quotes(code, days=4000)
        if df is None or len(df) < 30:
            per_stock.append({'code': code, 'detected': 0})
            continue
        df = calc_all_indicators(df)          # 完整序列重算指标(预热)
        df = df[df['date'] >= pd.Timestamp(REPLAY_START)].reset_index(drop=True)
        sigs = detect_signals(df, code, market, name, source='replay')
        per_stock.append({'code': code, 'detected': len(sigs)})
        log(f"  [replay] {code}: 检出 {len(sigs)} 条(REPLAY_START={REPLAY_START} 起)")
    conn = get_db()
    after = conn.execute(
        "SELECT COUNT(*) FROM passive_signals WHERE source='replay'").fetchone()[0]
    conn.close()
    log(f"  [replay] 新增入库 {after - before} 条 (replay 总数 {after})")
    return {'per_stock': per_stock, 'new_inserted': after - before, 'total_replay': after}


def validate_rsi24_timesplit(split=M8_SIGNAL_SPLIT, hold=10):
    """RSI24 时间分割验证(模块8 预注册规则, 2026-09-04 看结果前冻结):
    - 对象: rsi24_oversold(bullish/normal) 与 rsi24_overbought(bearish/reverse), 合并口径
    - 分割: 信号按 trade_date 升序, 前70%段A(开发) / 后30%段B(验证)
    - 转正: 两段各自触发数>=20 且 两段超额胜率>0 且 两段平均超额收益>0
    - 淘汰: 两段触发均>=20的前提下, 任一段超额胜率<=-5pp 或 平均超额收益<=-3pp
    - 任一段触发<20 → 继续观察(样本不足); 其余 → 继续观察
    规则一经写入不得依结果修改(与回测冻结协议同源)"""
    rule_text = (f"预注册(2026-09-04): 按{int(split*100)}%/{int((1-split)*100)}%分割, "
                 f"{hold}日持有期; 转正=两段触发>=20且超额胜率/超额收益均>0; "
                 f"淘汰=任一段超额胜率<=-5pp或超额收益<=-3pp(两段触发均>=20); 其余继续观察")
    price_map, date_map = _load_price_map()
    baselines = compute_random_baselines()
    rev_baselines = compute_random_baselines(reverse=True)

    pool_f, pool_args = _pool_sql_filter(_pool_code_set())
    conn = get_db()
    targets = {
        'rsi24_oversold': ('bullish', False),
        'rsi24_overbought': ('bearish', True),
    }
    result = {'rule': rule_text, 'split_ratio': split, 'hold_days': hold,
              'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
              'signals': {}}
    for sig_type, (direction, reverse) in targets.items():
        rows = conn.execute(
            f"SELECT code, trade_date FROM passive_signals "
            f"WHERE signal_type=? AND direction='bullish' AND {pool_f}"
            if sig_type == 'rsi24_oversold' else
            f"SELECT code, trade_date FROM passive_signals "
            f"WHERE signal_type=? AND direction='bearish' AND {pool_f}",
            (sig_type,) + tuple(pool_args)
        ).fetchall()
        rows = sorted(rows, key=lambda r: r['trade_date'])
        n_total = len(rows)
        entry = {'direction': direction,
                 'eval_mode': 'reverse' if reverse else 'normal',
                 'n_total': n_total, 'split_date': None,
                 'seg_a': None, 'seg_b': None,
                 'verdict': '继续观察', 'verdict_reason': ''}
        if n_total >= 4:
            cut = int(n_total * split)
            seg_a, seg_b = rows[:cut], rows[cut:]
            entry['split_date'] = seg_b[0]['trade_date'] if seg_b else None

            def _seg_stats(segs):
                rets = [_future_return(price_map, date_map, r['code'], r['trade_date'], hold)
                        for r in segs]
                if reverse:
                    rets = [-r if r is not None else None for r in rets]
                st = _win_stats(rets) or {}
                base = (rev_baselines if reverse else baselines).get(hold) or {}
                out = {'n': len(segs), 'triggers': st.get('triggers', 0),
                       'win_rate': st.get('win_rate'),
                       'avg_return': st.get('avg_return')}
                if base and st:
                    out['baseline_win_rate'] = base.get('win_rate')
                    out['baseline_return'] = base.get('avg_return')
                    out['excess_win_rate'] = (round(st['win_rate'] - base['win_rate'], 2)
                                              if st.get('win_rate') is not None else None)
                    out['excess_return'] = (round(st['avg_return'] - base['avg_return'], 4)
                                            if st.get('avg_return') is not None else None)
                return out

            entry['seg_a'] = _seg_stats(seg_a)
            entry['seg_b'] = _seg_stats(seg_b)
            a, b = entry['seg_a'], entry['seg_b']
            enough = (a['triggers'] >= 20 and b['triggers'] >= 20)
            if not enough:
                entry['verdict'] = '继续观察'
                entry['verdict_reason'] = (f"样本不足: 段A {a['triggers']} / 段B {b['triggers']} 触发"
                                           " (转正与淘汰均要求两段各>=20)")
            else:
                pos = all(x['excess_win_rate'] is not None and x['excess_win_rate'] > 0
                          and x['excess_return'] is not None and x['excess_return'] > 0
                          for x in (a, b))
                kill = any(x['excess_win_rate'] is not None and x['excess_win_rate'] <= -5
                           or x['excess_return'] is not None and x['excess_return'] <= -3
                           for x in (a, b))
                if pos:
                    entry['verdict'] = '转正'
                    entry['verdict_reason'] = "两段超额胜率与超额收益均为正"
                elif kill:
                    entry['verdict'] = '淘汰'
                    entry['verdict_reason'] = "任一段超额胜率<=-5pp或超额收益<=-3pp"
                else:
                    entry['verdict'] = '继续观察'
                    entry['verdict_reason'] = "未达转正也未触发淘汰阈值"
        else:
            entry['verdict_reason'] = f"总样本不足({n_total}<4), 无法分割"
        result['signals'][sig_type] = entry
    conn.close()
    return result


def get_backfill_status():
    """模块8 回填状态: 行情/信号(按source)/宽表/指标/宏观覆盖"""
    conn = get_db()
    pool_f, pool_args = _pool_sql_filter(_pool_code_set())
    stocks = [dict(r) for r in conn.execute(
        f"SELECT code, market, COUNT(*) n, MIN(trade_date) s, MAX(trade_date) e "
        f"FROM daily_quotes WHERE {pool_f} GROUP BY code, market", pool_args)]
    src = [dict(r) for r in conn.execute(
        f"SELECT COALESCE(source,'live') source, COUNT(*) n, MIN(trade_date) s, "
        f"MAX(trade_date) e FROM passive_signals WHERE {pool_f} GROUP BY 1", pool_args)]
    wide = conn.execute(
        f"SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM daily_feature_base "
        f"WHERE {pool_f}", pool_args).fetchone()
    ind = conn.execute(
        f"SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM daily_indicators "
        f"WHERE {pool_f}", pool_args).fetchone()
    macro = conn.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date), "
                         "COUNT(DISTINCT trade_date) FROM macro_events").fetchone()
    conn.close()
    return {
        'quote_backfill_start': QUOTE_BACKFILL_START,
        'replay_start': REPLAY_START,
        'macro_target': MACRO_BACKFILL_TARGET,
        'stocks': stocks, 'signal_sources': src,
        'wide': {'rows': wide[0], 'start': wide[1], 'end': wide[2]},
        'indicators': {'rows': ind[0], 'start': ind[1], 'end': ind[2]},
        'macro': {'rows': macro[0], 'start': macro[1], 'end': macro[2],
                  'days': macro[3]},
    }


def run_m8_backfill_chain(log=print):
    """模块8 一键回填重算链: 行情回填→指标全量重算→信号回放→宽表重建
    (模块3/模块5重算单独触发; 宏观回填为独立后台任务, 见 MACRO_BACKFILL_TARGET)"""
    log("[m8] ① 行情回填...")
    quotes = backfill_quotes_full(log=log)
    log("[m8] ② 指标全量重算...")
    inds = batch_generate_indicators()
    for r in inds:
        log(f"  [m8] 指标 {r['code']}: {r['rows']} 行")
    log("[m8] ③ 信号回放...")
    replay = replay_passive_signals(log=log)
    log("[m8] ④ 宽表全窗口重建...")
    wide = batch_generate_wide_tables(days=3000)
    for r in wide:
        log(f"  [m8] 宽表 {r['code']}: {r['rows']} 行")
    log("[m8] 链完成 (模块3三口径重算请单独运行 run_module3_analysis)")
    return {'quotes': quotes, 'indicators': inds, 'replay': replay, 'wide': wide}


# ============================================================
# 模块 9: 宏观×策略融合 (2026-09-06)
# FDR 预注册裁决(BH按窗口分层 q=0.05) → overlay 配置冻结 →
# 封锁日台账(追加式不改写) → S2M/S1aM 变体(只拒新仓) → 前向裁决
# 规则与设计见 模块9开发计划.md 二(2026-09-06 看结果前冻结)
# 红线: 家族选择含知识截止后数据(选择泄漏) → 回测 full/oos 仅机制对照,
#       唯一裁决 = forward test; 配置冻结后不得依回测/前向结果修改
# ============================================================
M9_STUDY_END = '2026-09-03'     # 主裁决研究事件窗口终点(=2026-09-05 00:18 冻结基线)
FDR_Q = 0.05                    # BH 显著性水平(按窗口分层, 6 族)
FDR_MIN_N = 30                  # overlay 资格: 组样本量下限
OVERLAY_MAX_COV = 0.5           # overlay 资格: 单组封锁日覆盖率上限(防退化)
INDEX_MARKET_CN = {'.INX': '美股', 'HSI': '港股', 'sh000300': 'A股'}   # 指数→市场
OVERLAY_BLOCKABLE_WINDOWS = ('[0,+1]', '[0,+3]', '[0,+5]', '[0,+10]')  # 可封锁窗口形状


def create_m9_tables(conn):
    """模块9四表(CREATE IF NOT EXISTS) + backtest_runs 追加列(幂等迁移, 旧批次 NULL)"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS macro_fdr_results(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_ts TEXT NOT NULL, window TEXT NOT NULL,
            region TEXT, family TEXT, index_code TEXT, direction TEXT,
            n INTEGER, car_mean REAL, t_stat REAL, p_value REAL,
            p_rank INTEGER, m_family INTEGER, bh_critical REAL, q_value REAL,
            survive INTEGER, overlay_eligible INTEGER, eligible_reason TEXT,
            run_date TEXT
        );
        CREATE TABLE IF NOT EXISTS macro_fdr_meta(
            key TEXT PRIMARY KEY, value TEXT
        );
        CREATE TABLE IF NOT EXISTS macro_overlay_days(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL, trade_date TEXT NOT NULL, group_id TEXT NOT NULL,
            window_days INTEGER, run_ts TEXT, run_date TEXT,
            UNIQUE(code, trade_date, group_id)
        );
        CREATE TABLE IF NOT EXISTS macro_overlay_meta(
            key TEXT PRIMARY KEY, value TEXT
        );
    """)
    has_bt = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                          "AND name='backtest_runs'").fetchone()
    if has_bt:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(backtest_runs)")]
        if 'n_macro_blocked' not in cols:
            conn.execute("ALTER TABLE backtest_runs ADD COLUMN n_macro_blocked INTEGER")
    conn.commit()


def _bh_stratum(pvals, q):
    """Benjamini-Hochberg step-up(单层): pvals=[原始p值]
    返回 (survive标记列表, q_value调整p值列表), 与输入顺序对齐
    存活 = 满足 p(k) <= q·k/m 的最大 rank k 及其以上全部(升序排列的前 k 个)"""
    m = len(pvals)
    survive = [False] * m
    qvals = [None] * m
    if m == 0:
        return survive, qvals
    order = sorted(range(m), key=lambda i: pvals[i])
    prev = 1.0
    for rank in range(m, 0, -1):           # 调整p: 自大rank向小取 min
        i = order[rank - 1]
        adj = min(prev, pvals[i] * m / rank)
        qvals[i] = min(1.0, adj)
        prev = qvals[i]
    k_max = 0
    for rank in range(1, m + 1):
        i = order[rank - 1]
        if pvals[i] <= q * rank / m:
            k_max = rank
    for rank in range(1, k_max + 1):
        survive[order[rank - 1]] = True
    return survive, qvals


def _m9_load_events(conn, end_date=None):
    """高重要性事件一次加载 → [(trade_date, time, region, family, direction)]
    家族/方向判定与 run_macro_study 同口径; 排除'其他'家族与未映射地区"""
    sql = ("SELECT trade_date, time, region, title, pub_val, forecast_val "
           "FROM macro_events WHERE star=?")
    args = [MACRO_STAR_HIGH]
    if end_date:
        sql += " AND trade_date <= ?"
        args.append(end_date)
    out = []
    for r in conn.execute(sql + " ORDER BY trade_date, time", args):
        if r['region'] not in MACRO_REGION_INDEX:
            continue
        fam = _macro_family(r['title'])
        if fam == '其他':
            continue
        out.append((r['trade_date'], r['time'] or '', r['region'], fam,
                    _macro_direction(r['pub_val'], r['forecast_val'])))
    return out


def _m9_events_for_group(events, region, family, direction):
    """组事件过滤(与 run_macro_study 建组口径严格一致):
    family='全部高重要性' → 聚合该地区全部家族(研究侧的 key_all 聚合组)
    direction='全部' → 不按方向过滤(含方向不可解析事件)"""
    if family == '全部高重要性':
        if direction == '全部':
            return [e for e in events if e[2] == region]
        return [e for e in events if e[2] == region and e[4] == direction]
    if direction == '全部':
        return [e for e in events if e[2] == region and e[3] == family]
    return [e for e in events if e[2] == region and e[3] == family
            and e[4] == direction]


def _m9_stock_calendars(conn):
    """池内股票日历: code → {'market', 'dates'(ISO升序列表)}(池为唯一事实源)"""
    out = {}
    for s in get_stock_pool(active_only=True):
        ds = [r[0] for r in conn.execute(
            "SELECT trade_date FROM daily_quotes WHERE code=? ORDER BY trade_date",
            (s['code'],))]
        if ds:
            out[s['code']] = {'market': s.get('market', 'A股'), 'dates': ds}
    return out


def _m9_index_calendars(conn):
    """指数日历: index_code → (market, 升序date对象列表)"""
    out = {}
    for code, market in [('.INX', 'us'), ('HSI', 'hk'), ('sh000300', 'cn')]:
        ds = [datetime.strptime(r[0], '%Y-%m-%d').date()
              for r in conn.execute(
                  "SELECT trade_date FROM benchmark_index WHERE code=? "
                  "ORDER BY trade_date", (code,))]
        if ds:
            out[code] = (market, ds)
    return out


def _m9_block_dates(code_dates, index_code, index_cals, group_events, n_days):
    """单股封锁日集: 事件→指数日历 t_day → 该股日历上 t_day 之后前 n_days 个交易日
    (不含 t 当日, 与全框架 T+1 成交惯例一致; 指数数据末端外的事件跳过)"""
    ent = index_cals.get(index_code)
    if ent is None or n_days <= 0:
        return set()
    market, idx_dates = ent
    blocks = set()
    for ev in group_events:
        ev_date = datetime.strptime(ev[0], '%Y-%m-%d').date()
        et = None
        if ev[1]:
            hh, mm = (ev[1].split(':') + ['0'])[:2]
            et = dtime(int(hh), int(mm))
        t_day = macro_event_trading_day(ev_date, et, market, idx_dates)
        if t_day is None:
            continue
        i = bisect.bisect_right(code_dates, t_day.isoformat())
        blocks.update(code_dates[i:i + n_days])
    return blocks


def _m9_group_coverage(group_events, index_code, n_days, stock_cals, index_cals,
                       win_start, win_end):
    """该组在各映射股票上的封锁日覆盖率 {code: 比例}
    分母 = 该股研究窗口内交易日数; 无映射池内股票 → {}"""
    cov = {}
    for code, sc in stock_cals.items():
        if sc['market'] != INDEX_MARKET_CN.get(index_code):
            continue
        blocks = _m9_block_dates(sc['dates'], index_code, index_cals,
                                 group_events, n_days)
        lo = bisect.bisect_left(sc['dates'], win_start)
        hi = bisect.bisect_right(sc['dates'], win_end)
        denom = hi - lo
        cov[code] = (len(blocks) / denom) if denom > 0 else 0.0
    return cov


def _fdr_on_current_study(conn, q, min_n, max_cov, study_end, log=print):
    """对当前 macro_study_results 做分层BH + overlay资格判定(预注册规则)
    返回 (rows, eligible, strata):
      rows = 全部组×窗口裁决明细(供 macro_fdr_results 入库)
      eligible = 入选组配置列表(组级最短窗口)
      strata = {window: {m, n_survive, n_eligible}}"""
    res_rows = conn.execute(
        "SELECT group_id, region, family, index_code, direction, window, "
        "n, car_mean, t_stat, p_value FROM macro_study_results").fetchall()
    if not res_rows:
        log("  [fdr] macro_study_results 为空, 先运行 run_macro_study")
        return [], [], {}
    # --- BH 按窗口分层(6 窗口 = 6 独立检验族; p缺失的行不参与检验) ---
    by_window = {}
    for i, r in enumerate(res_rows):
        by_window.setdefault(r['window'], []).append(i)
    survive = [False] * len(res_rows)
    q_value = [None] * len(res_rows)
    p_rank = [None] * len(res_rows)
    m_family = [None] * len(res_rows)
    bh_crit = [None] * len(res_rows)
    strata = {}
    for win, idxs in sorted(by_window.items()):
        testable = [i for i in idxs if res_rows[i]['p_value'] is not None]
        m = len(testable)
        order = sorted(testable, key=lambda i: res_rows[i]['p_value'])
        for rank, i in enumerate(order, 1):
            p_rank[i] = rank
            m_family[i] = m
            bh_crit[i] = round(q * rank / m, 6) if m else None
        sv, qv = _bh_stratum([res_rows[i]['p_value'] for i in testable], q)
        for j, i in enumerate(testable):
            survive[i] = sv[j]
            q_value[i] = qv[j]
        strata[win] = {'m': m, 'n_survive': sum(sv), 'n_eligible': 0}
    # --- overlay 资格: 四条件 + 覆盖率护栏(预注册, 逐条可追溯) ---
    events = _m9_load_events(conn, end_date=study_end)
    stock_cals = _m9_stock_calendars(conn)
    index_cals = _m9_index_calendars(conn)
    win_start = min((e[0] for e in events), default=None) or '2000-01-01'
    rows = []
    cov_cache = {}
    for i, r in enumerate(res_rows):
        direction = r['direction'] or '全部'
        p = r['p_value']
        if p is None:
            eligible, reason = 0, 'p值缺失(不可检验)'
        elif not survive[i]:
            eligible, reason = 0, f'BH未存活(q={q}, q_value={q_value[i]:.4f})'
        elif (r['n'] or 0) < min_n:
            eligible, reason = 0, f"n={r['n']}<{min_n}(小样本伪影)"
        elif (r['car_mean'] or 0) >= 0:
            eligible, reason = 0, 'CAR≥0(只减不加, 正CAR组只记录不行动)'
        elif r['window'] not in OVERLAY_BLOCKABLE_WINDOWS:
            eligible, reason = 0, ('[0,+0]无可封锁日' if r['window'] == '[0,+0]'
                                   else '[-5,+10]含事件前窗口, 均只参与FDR')
        else:
            n_days = int(r['window'].split('+')[1].rstrip(']'))
            ck = (r['region'], r['family'], r['index_code'], direction, n_days)
            if ck not in cov_cache:
                grp_ev = _m9_events_for_group(events, r['region'], r['family'],
                                              direction)
                cov_cache[ck] = _m9_group_coverage(
                    grp_ev, r['index_code'], n_days, stock_cals, index_cals,
                    win_start, study_end)
            cov = cov_cache[ck]
            worst = max(cov.values()) if cov else 0.0
            if worst > max_cov:
                eligible = 0
                worst_code = max(cov, key=cov.get)
                reason = (f'覆盖率{worst:.0%}>{max_cov:.0%}(退化护栏, {worst_code})')
            elif not cov:
                eligible, reason = 1, '四条件+护栏通过(无映射池内股票, 不产生封锁日)'
            else:
                eligible, reason = 1, '四条件+护栏通过'
        if eligible:
            strata[r['window']]['n_eligible'] += 1
        rows.append({
            'window': r['window'], 'region': r['region'], 'family': r['family'],
            'index_code': r['index_code'], 'direction': direction,
            'n': r['n'], 'car_mean': r['car_mean'], 't_stat': r['t_stat'],
            'p_value': p, 'p_rank': p_rank[i], 'm_family': m_family[i],
            'bh_critical': bh_crit[i], 'q_value': q_value[i],
            'survive': int(survive[i]), 'overlay_eligible': eligible,
            'eligible_reason': reason,
            '_group_id': r['group_id'], '_n_days': (int(r['window'].split('+')[1].rstrip(']'))
                                                    if r['window'] in OVERLAY_BLOCKABLE_WINDOWS else None),
            '_coverage': cov_cache.get((r['region'], r['family'], r['index_code'],
                                        direction,
                                        int(r['window'].split('+')[1].rstrip(']'))
                                        if r['window'] in OVERLAY_BLOCKABLE_WINDOWS else -1), {}),
        })
    # --- 组级最短窗口选择(同组多窗口合格 → 取最小N, 最小干预) ---
    elig_by_group = {}
    for row in rows:
        if row['overlay_eligible']:
            elig_by_group.setdefault(row['_group_id'], []).append(row)
    selected = {gid: min(rlist, key=lambda x: x['_n_days'])
                for gid, rlist in elig_by_group.items()}
    for row in rows:
        if (row['overlay_eligible'] and row['_group_id'] in selected
                and row['_n_days'] != selected[row['_group_id']]['_n_days']):
            row['eligible_reason'] += (
                f"(同组取更短窗口N={selected[row['_group_id']]['_n_days']})")
    eligible_cfg = []
    for gid in sorted(selected):
        s = selected[gid]
        eligible_cfg.append({
            'group_id': gid, 'region': s['region'], 'family': s['family'],
            'index_code': s['index_code'], 'direction': s['direction'],
            'window_days': s['_n_days'], 'n': s['n'],
            'car_mean': s['car_mean'], 't_stat': s['t_stat'],
            'p_value': s['p_value'], 'q_value': s['q_value'],
            'coverage': {k: round(v, 4) for k, v in s['_coverage'].items()},
        })
    log(f"  [fdr] {len(res_rows)} 行(267组×6窗口口径) → BH存活 "
        f"{sum(strata[w]['n_survive'] for w in strata)} 行, 资格通过 "
        f"{sum(strata[w]['n_eligible'] for w in strata)} 行, 入选组 {len(eligible_cfg)}")
    return rows, eligible_cfg, strata


def run_macro_fdr(q=FDR_Q, min_n=FDR_MIN_N, max_cov=OVERLAY_MAX_COV,
                  force=False, log=print):
    """模块9 主裁决入口: BH分层FDR + 诊断对照 + overlay配置冻结
    执行序列(严格, 规则见 模块9开发计划.md 二):
      ① 主裁决研究重算(事件 ≤ M9_STUDY_END, 复现冻结基线) → FDR → rows/入选组
      ② 诊断对照: 知识截止前子样本重跑研究+同规则FDR(非门槛, 只报告重合度)
      ③ 恢复主裁决研究窗口
      ④ macro_fdr_results 全量重建 + meta 入库
      ⑤ overlay 配置冻结(macro_overlay_meta.config/adjudication +
         forward_meta.overlay_protocol 独立键)
    force: 配置已冻结时默认拒绝重裁(冻结纪律); 显式 force=True 才允许新预注册决策"""
    conn = get_db()
    create_m9_tables(conn)
    frozen = conn.execute("SELECT value FROM macro_overlay_meta "
                          "WHERE key='config'").fetchone()
    conn.close()
    if frozen and not force:
        return {'error': ('overlay 配置已冻结(纪律: 不得依结果修改); '
                          '如需新的预注册裁决请 force=True 显式执行')}

    log(f"[fdr] ① 主裁决研究重算(事件窗口 ≤ {M9_STUDY_END})...")
    run_macro_study(log=log, end_date=M9_STUDY_END)
    conn = get_db()
    create_m9_tables(conn)
    run_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    run_date = datetime.now().strftime('%Y-%m-%d')
    rows, eligible, strata = _fdr_on_current_study(conn, q, min_n, max_cov,
                                                   M9_STUDY_END, log=log)
    study_meta = {r['key']: r['value'] for r in conn.execute(
        "SELECT key, value FROM macro_study_meta")}
    # ② 诊断对照(知识截止前子样本, 同规则) + ③ 恢复(异常也必须恢复)
    diag = None
    try:
        log(f"[fdr] ② 诊断对照: 知识截止前子样本(事件 ≤ {BT_KNOWLEDGE_CUTOFF})...")
        run_macro_study(log=log, end_date=BT_KNOWLEDGE_CUTOFF)
        _, diag_eligible, _ = _fdr_on_current_study(conn, q, min_n, max_cov,
                                                    BT_KNOWLEDGE_CUTOFF, log=log)
        a = {g['group_id'] for g in eligible}
        b = {g['group_id'] for g in diag_eligible}
        diag = {
            'cutoff': BT_KNOWLEDGE_CUTOFF,
            'n_main': len(a), 'n_diag': len(b),
            'overlap': sorted(a & b), 'main_only': sorted(a - b),
            'diag_only': sorted(b - a),
            'zero_survival_warning': bool(a and not b),
            'note': ('诊断对照非门槛, 主裁决不因对照结果改变; '
                     '主清单在子样本零存活=选择泄漏脆弱性, 必须显著标注'),
        }
    finally:
        log(f"[fdr] ③ 恢复主裁决研究窗口(事件 ≤ {M9_STUDY_END})...")
        run_macro_study(log=log, end_date=M9_STUDY_END)

    # ④ FDR 结果入库(每次裁决全量重建)
    conn.execute("DELETE FROM macro_fdr_results")
    conn.executemany(
        "INSERT INTO macro_fdr_results(run_ts, window, region, family, index_code, "
        "direction, n, car_mean, t_stat, p_value, p_rank, m_family, bh_critical, "
        "q_value, survive, overlay_eligible, eligible_reason, run_date) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(run_ts, r['window'], r['region'], r['family'], r['index_code'],
          r['direction'], r['n'], r['car_mean'], r['t_stat'], r['p_value'],
          r['p_rank'], r['m_family'], r['bh_critical'], r['q_value'],
          r['survive'], r['overlay_eligible'], r['eligible_reason'], run_date)
         for r in rows])
    protocol = {
        'frozen_at': '2026-09-06', 'method': 'Benjamini-Hochberg step-up',
        'q': q, 'stratified_by': 'window(6族: [0,+0]/[0,+1]/[0,+3]/[0,+5]/[0,+10]/[-5,+10])',
        'min_n': min_n, 'max_cov': max_cov,
        'eligibility': ('①BH存活 ②n>=30 ③car_mean<0(只减不加) '
                        '④窗口[0,+N]且N>=1; 护栏: 单组封锁日覆盖率>50%不入选; '
                        '同组多窗口取最短N'),
        'main_adjudication_window': f'2023-01-02 ~ {M9_STUDY_END}(=2026-09-05冻结基线)',
        'selection_leakage_disclosure': ('研究窗口含知识截止(2026-05-01)后数据, '
                                         'overlay回测full/oos均为机制对照, 唯一裁决=forward'),
    }
    last_run = {
        'run_ts': run_ts, 'q': q, 'min_n': min_n, 'max_cov': max_cov,
        'n_rows': len(rows), 'n_survive_rows': sum(r['survive'] for r in rows),
        'n_eligible_rows': sum(r['overlay_eligible'] for r in rows),
        'n_eligible_groups': len(eligible), 'strata': strata,
        'study_baseline': {'n_events': study_meta.get('n_events'),
                           'n_groups': study_meta.get('n_groups')},
    }
    for k, v in (('protocol', protocol), ('last_run', last_run)):
        conn.execute("INSERT OR REPLACE INTO macro_fdr_meta VALUES (?,?)",
                     (k, json.dumps(v, ensure_ascii=False)))

    # ⑤ overlay 配置冻结(交付后不再依回测/前向结果修改)
    config = {
        'frozen_at': run_ts, 'fdr_run_ts': run_ts, 'q': q, 'min_n': min_n,
        'max_cov': max_cov, 'study_window': ['2023-01-02', M9_STUDY_END],
        'groups': eligible,
        'mechanism': ('事件t日(指数日历映射)→该股t+1..t+N开仓日封锁, 只拒新仓不影响持仓; '
                      '多组取并集; 台账(macro_overlay_days)追加式永不改写, '
                      '管道刷新只追加 trade_date > 完整性截断日 的决策'),
        'note': '配置冻结; 宏观研究重跑不自动更新overlay(需新的预注册决策)',
    }
    adjudication = {
        'pre_registered': '2026-09-06', 'min_trades': FORWARD_ADJ_MIN_TRADES,
        'rules': ('S*M完成交易>=20后, 与原版S*同窗口前向对比: '
                  'excess_vs_B1(S*M)<excess_vs_B1(S*) → 淘汰; '
                  'excess_vs_B1(S*M)>=excess_vs_B1(S*) 且 max_drawdown收窄 → 保留(转正); '
                  '其余 → 继续观察'),
        'freeze_note': '裁决规则预注册, 不得依前向结果修改(与回测冻结协议同源)',
        'honesty': 'S*M回测full/oos为机制对照(选择泄漏), 唯一裁决=forward test',
    }
    conn.execute("INSERT OR REPLACE INTO macro_overlay_meta VALUES (?,?)",
                 ('config', json.dumps(config, ensure_ascii=False)))
    conn.execute("INSERT OR REPLACE INTO macro_overlay_meta VALUES (?,?)",
                 ('adjudication', json.dumps(adjudication, ensure_ascii=False)))
    if diag is not None:
        conn.execute("INSERT OR REPLACE INTO macro_fdr_meta VALUES (?,?)",
                     ('diagnostic_cutoff', json.dumps(diag, ensure_ascii=False)))
    create_forward_tables(conn)
    # forward_meta.meta_key 无 UNIQUE 约束 → 先删后插幂等(问题日志#2: 重裁产生重复行)
    conn.execute("DELETE FROM forward_meta WHERE meta_key='overlay_protocol'")
    conn.execute("INSERT INTO forward_meta(meta_key, result_json, run_date, generated_at) "
                 "VALUES('overlay_protocol',?,?,?)",
                 (json.dumps(adjudication, ensure_ascii=False), run_date, run_ts))
    conn.commit()
    conn.close()
    log(f"[fdr] 裁决完成: 入选组 {len(eligible)} / 267组, "
        f"诊断对照重合 {len(diag['overlap']) if diag else '-'}"
        f"{' [警告]主清单子样本零存活' if diag and diag['zero_survival_warning'] else ''}")
    log("[fdr] 下一步: refresh_overlay_days(min_date=None) 种子台账 → run_d2_backtests")
    return {'protocol': protocol, 'last_run': last_run, 'eligible': eligible,
            'diagnostic': diag, 'strata': strata}


def refresh_overlay_days(min_date=None, log=print, codes=None):
    """封锁日台账追加(幂等: UNIQUE(code,trade_date,group_id) + INSERT OR IGNORE)
    按冻结配置把入选组事件映射为各股封锁日; min_date 守卫: 只插 trade_date > min_date
    (已入库净值覆盖的日期永不回补封锁——迟到信息只作用于未来, 见 模块9开发计划.md 2.4)
    min_date=None → 全历史种子(交付时一次性执行)
    codes=None → 全池; codes=[code] → 单股种子(模块10 onboard: 新股无已入库净值且
    join_eff=当日, 全历史种子安全 — 入池前该股无资格触发, 不影响其它股已入库净值的重放;
    入池日之后的封锁日由管道 overlay_days 步的守卫继续追加)"""
    conn = get_db()
    create_m9_tables(conn)
    cfg_row = conn.execute("SELECT value FROM macro_overlay_meta "
                           "WHERE key='config'").fetchone()
    if not cfg_row:
        conn.close()
        return {'error': 'overlay 配置未冻结, 先运行 run_macro_fdr'}
    cfg = json.loads(cfg_row[0])
    events = _m9_load_events(conn)             # 全事件(不限窗口, 新事件持续产生封锁)
    stock_cals = _m9_stock_calendars(conn)
    index_cals = _m9_index_calendars(conn)
    run_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    run_date = datetime.now().strftime('%Y-%m-%d')
    inserted, per_group = 0, {}
    for g in cfg['groups']:
        grp_ev = _m9_events_for_group(events, g['region'], g['family'],
                                      g['direction'])
        n_days = g['window_days']
        n_ins = 0
        for code, sc in stock_cals.items():
            if codes is not None and code not in codes:
                continue
            if sc['market'] != INDEX_MARKET_CN.get(g['index_code']):
                continue
            blocks = _m9_block_dates(sc['dates'], g['index_code'], index_cals,
                                     grp_ev, n_days)
            for d in sorted(blocks):
                if min_date and d <= min_date:
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO macro_overlay_days"
                    "(code, trade_date, group_id, window_days, run_ts, run_date) "
                    "VALUES(?,?,?,?,?,?)",
                    (code, d, g['group_id'], n_days, run_ts, run_date))
                n_ins += cur.rowcount
        per_group[g['group_id']] = n_ins
        inserted += n_ins
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM macro_overlay_days").fetchone()[0]
    conn.close()
    log(f"[overlay] 台账追加 {inserted} 条(守卫 min_date={min_date}), 台账总计 {total} 条")
    return {'groups': len(cfg['groups']), 'inserted': inserted,
            'min_date': min_date, 'ledger_total': total, 'per_group': per_group}


def _load_overlay_blocked(data=None):
    """台账 → {code: set(封锁日)}; data 提供时按池内股票过滤(单一事实源)
    纯读路径不做DDL(建表在 run_macro_fdr/refresh_overlay_days 写入侧完成;
    并发写事务存在时DDL会触发 database is locked, 问题日志#1)"""
    conn = get_db()
    out = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='macro_overlay_days'").fetchone():
        for r in conn.execute("SELECT code, trade_date FROM macro_overlay_days"):
            out.setdefault(r['code'], set()).add(r['trade_date'])
    conn.close()
    if data is not None and data.get('stocks'):
        out = {c: ds for c, ds in out.items() if c in data['stocks']}
    return out


def _stock_cap_date():
    """池内股票行情终局日最小值(完整性截断日, 与 run_forward_step 同口径)"""
    conn = get_db()
    pool_f, pool_args = _pool_sql_filter(_pool_code_set())
    latest = [r[0] for r in conn.execute(
        f"SELECT MAX(trade_date) FROM daily_quotes WHERE {pool_f} GROUP BY code",
        pool_args)]
    conn.close()
    return min([d for d in latest if d], default=None)


def evaluate_overlay():
    """模块9 overlay 前向裁决读数(规则预注册于 macro_overlay_meta.adjudication)
    S2M vs S2 / S1aM vs S1a: 各自完成交易 >= FORWARD_ADJ_MIN_TRADES 后对比
    淘汰=overlay拖累收益; 保留(转正)=收益不降且回撤收窄; 其余继续观察"""
    status = _forward_status()
    conn = get_db()
    # 纯读路径不做DDL(空库时按0计数, 见 问题日志#1 同源约定)
    has_ft = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                          "AND name='forward_trades'").fetchone()
    out = []
    for m_id, p_id in (('S2M', 'S2'), ('S1aM', 'S1a')):
        n = (conn.execute("SELECT COUNT(*) FROM forward_trades WHERE strategy_id=?",
                          (m_id,)).fetchone()[0] if has_ft else 0)
        m = status.get('strategies', {}).get(m_id, {})
        p = status.get('strategies', {}).get(p_id, {})
        entry = {
            'strategy_id': m_id, 'parent_id': p_id,
            'name': m.get('name') or BT_STRATEGIES[m_id]['name'],
            'n_trades': n, 'min_trades': FORWARD_ADJ_MIN_TRADES,
            'progress': f"{n}/{FORWARD_ADJ_MIN_TRADES}",
            'macro_blocked': m.get('macro_blocked'),
            'excess_vs_b1': m.get('excess_vs_b1'),
            'parent_excess_vs_b1': p.get('excess_vs_b1'),
            'max_drawdown': m.get('max_drawdown'),
            'parent_max_drawdown': p.get('max_drawdown'),
        }
        if n >= FORWARD_ADJ_MIN_TRADES and m and p:
            me, pe = m.get('excess_vs_b1'), p.get('excess_vs_b1')
            md, pd_ = m.get('max_drawdown'), p.get('max_drawdown')
            if me is not None and pe is not None and me < pe:
                entry['verdict'] = '🔴 淘汰建议(overlay 拖累收益)'
            elif (me is not None and pe is not None and me >= pe
                  and md is not None and pd_ is not None and md > pd_):
                entry['verdict'] = '🟢 保留(转正): 收益不降且回撤收窄'
            else:
                entry['verdict'] = '🟡 继续观察'
        else:
            entry['verdict'] = '⏳ 样本积累中'
        out.append(entry)
    conn.close()
    return out


def load_fdr_view():
    """看板回读: FDR 裁决结果 + 双 meta + overlay 台账统计(纯读, 未裁决时返回空)"""
    conn = get_db()
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='macro_fdr_results'").fetchone():
        conn.close()
        return {'results': pd.DataFrame(), 'fdr_meta': {}, 'overlay_meta': {},
                'ledger': []}
    res = pd.read_sql_query(
        "SELECT window, region, family, index_code, direction, n, car_mean, "
        "t_stat, p_value, p_rank, m_family, bh_critical, q_value, survive, "
        "overlay_eligible, eligible_reason, run_date FROM macro_fdr_results "
        "ORDER BY overlay_eligible DESC, p_value", conn)

    def _meta(table):
        out = {}
        for r in conn.execute(f"SELECT key, value FROM {table}"):
            try:
                out[r['key']] = json.loads(r['value'])
            except (TypeError, ValueError):
                out[r['key']] = r['value']
        return out

    fdr_meta = _meta('macro_fdr_meta')
    overlay_meta = _meta('macro_overlay_meta')
    ledger = [dict(r) for r in conn.execute(
        "SELECT code, COUNT(*) n_days, MIN(trade_date) s, MAX(trade_date) e "
        "FROM macro_overlay_days GROUP BY code ORDER BY code")]
    conn.close()
    return {'results': res, 'fdr_meta': fdr_meta, 'overlay_meta': overlay_meta,
            'ledger': ledger}


# ============================================================
# 模块 10: 股票池动态扩容与数据引导 (2026-09-07)
# 入池即引导(onboard) + 前向资格区间(membership timeline) +
# 离池/停用快照冻结(已入库前向净值永不失配)
# 预注册规则见 模块10开发计划.md 二(2026-09-07 看结果前冻结)
# ============================================================

def _m10_event(code, event, source):
    """追加一条池构成事件(join/leave), eff_date=今天"""
    conn = get_db()
    row = conn.execute("SELECT market, name FROM stock_pool WHERE code=?",
                       (code,)).fetchone()
    conn.execute(
        "INSERT INTO forward_pool_events(code, market, name, event, "
        "eff_date, source, run_ts) VALUES(?,?,?,?,?,?,?)",
        (code, row['market'] if row else None, row['name'] if row else None,
         event, datetime.now().strftime('%Y-%m-%d'), source,
         datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()


def _forward_membership():
    """事件表 → {code: [(join_eff, leave_eff|None), ...]} 资格区间升序
    活跃池中无事件的股票 → 隐式 (FORWARD_START, None) 兜底(兼容未迁移库)"""
    conn = get_db()
    events = [dict(r) for r in conn.execute(
        "SELECT code, event, eff_date FROM forward_pool_events "
        "ORDER BY code, eff_date, id")]
    conn.close()
    out = {}
    for e in events:
        ivs = out.setdefault(e['code'], [])
        if e['event'] == 'join':
            ivs.append([e['eff_date'], None])
        elif ivs and ivs[-1][1] is None:
            ivs[-1][1] = e['eff_date']
        else:
            # leave 无对应 join(未迁移库的极端序列): 隐式 join 自 FORWARD_START
            ivs.append([FORWARD_START, e['eff_date']])
    for s in get_stock_pool(active_only=True):
        if s['code'] not in out:
            out[s['code']] = [[FORWARD_START, None]]
    return {c: [tuple(iv) for iv in ivs] for c, ivs in out.items()}


def _member_on(membership, code, d):
    """股票 code 在交易日 d 是否处于资格区间 [join, leave) 内"""
    for j, l in membership.get(code, ()):
        if j <= d and (l is None or d < l):
            return True
    return False


def _stock_open_membership(code):
    """该股当前是否存在开放资格区间(活跃池无事件股票的隐式区间也算开放)"""
    return any(l is None for (_, l) in _forward_membership().get(code, ()))


def _forward_replay_data():
    """模块10 前向重放数据集: 活跃池实时 ∪ 快照注入, 与 _load_backtest_data 同构
    已关闭区间(有leave事件)只用快照(防qfq重基准回写历史导致已入库NAV失配);
    开放区间(当前成员)只用实时表; 再入池股按区间逐日裁决(关闭区间日覆盖为快照值)"""
    data = _load_backtest_data()
    membership = _forward_membership()
    conn = get_db()
    snap_codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT code FROM forward_stock_px ORDER BY code")]
    for code in snap_codes:
        closed = [(j, l) for (j, l) in membership.get(code, ()) if l is not None]
        if not closed:
            continue  # 无关闭区间: 实时表即真相

        def in_closed(d, closed=closed):
            return any(j <= d < l for (j, l) in closed)

        px = conn.execute(
            "SELECT trade_date, open, close FROM forward_stock_px "
            "WHERE code=? ORDER BY trade_date", (code,)).fetchall()
        if code not in data['stocks']:
            # 离池/停用股: 整个前向窗口为关闭区间, 快照即全部数据
            mkt = conn.execute(
                "SELECT market FROM forward_pool_events WHERE code=? "
                "ORDER BY id LIMIT 1", (code,)).fetchone()
            st = {'market': mkt[0] if mkt else '', 'dates': [],
                  'opens': [], 'closes': [], 'date_idx': {}}
            for r in px:
                if r['open'] and r['close'] and in_closed(r['trade_date']):
                    st['date_idx'][r['trade_date']] = len(st['dates'])
                    st['dates'].append(r['trade_date'])
                    st['opens'].append(r['open'])
                    st['closes'].append(r['close'])
            if st['dates']:
                data['stocks'][code] = st
        else:
            # 再入池股: 关闭区间日以快照值覆盖实时值(其余日用实时)
            live = data['stocks'][code]
            px_map = {r['trade_date']: (r['open'], r['close']) for r in px}
            for i, d in enumerate(live['dates']):
                if d in px_map and in_closed(d):
                    live['opens'][i], live['closes'][i] = px_map[d]
        # 信号: 关闭区间日只认快照(移除实时, 防 qfq 重基准后检出差异)
        data['signals'] = {t for t in data['signals']
                           if not (t[0] == code and in_closed(t[1]))}
        for r in conn.execute(
                "SELECT trade_date, signal_type, signal_subtype FROM "
                "forward_stock_sig WHERE code=?", (code,)):
            if in_closed(r['trade_date']):
                data['signals'].add(
                    (code, r['trade_date'], r['signal_type'], r['signal_subtype']))
    conn.close()
    data['calendar'] = sorted(
        set(d for s in data['stocks'].values() for d in s['dates']))
    return data


def _snapshot_forward_stock(code):
    """离池/停用前冻结: 该股前向窗口价格与信号键快照(INSERT OR REPLACE 幂等)
    保证级联删除后前向重放仍能逐日复现已入库净值(追加守卫零失配)"""
    conn = get_db()
    px = conn.execute(
        "SELECT trade_date, open, close FROM daily_feature_base "
        "WHERE code=? AND trade_date>=?", (code, FORWARD_START)).fetchall()
    for r in px:
        conn.execute(
            "INSERT OR REPLACE INTO forward_stock_px(code, trade_date, open, close) "
            "VALUES(?,?,?,?)", (code, r['trade_date'], r['open'], r['close']))
    sig = conn.execute(
        "SELECT DISTINCT trade_date, signal_type, signal_subtype "
        "FROM passive_signals WHERE code=? AND trade_date>=?",
        (code, FORWARD_START)).fetchall()
    for r in sig:
        conn.execute(
            "INSERT OR REPLACE INTO forward_stock_sig"
            "(code, trade_date, signal_type, signal_subtype) VALUES(?,?,?,?)",
            (code, r['trade_date'], r['signal_type'], r['signal_subtype']))
    conn.commit()
    conn.close()
    return {'px_rows': len(px), 'sig_rows': len(sig)}


def _replay_signals_for(code, market, name, log=None):
    """单股信号回放(模块10 从 replay_passive_signals 重构抽出, 行为不变):
    完整行情重算指标 → 仅记录 ≥REPLAY_START 的信号(source='replay', 幂等键复用)"""
    df = get_daily_quotes(code, days=4000)
    if df is None or len(df) < 30:
        return 0
    df = calc_all_indicators(df)
    df = df[df['date'] >= pd.Timestamp(REPLAY_START)].reset_index(drop=True)
    sigs = detect_signals(df, code, market, name, source='replay')
    if log:
        log(f"  [replay] {code}: 检出 {len(sigs)} 条(REPLAY_START={REPLAY_START} 起)")
    return len(sigs)


def onboard_pool_stock(code, log=print, recalc_modules=False):
    """模块10 入池引导链: 行情回填→深度校验→基准指数→指标→信号回放→宽表→
    overlay封锁日种子→join事件兜底
    (深度校验在指标前: 源缺口补插的行情必须先落库再算指标/信号;
    overlay种子在宽表后: 新股历史封锁日一次性入台账, 管道守卫只补截断日之后)
    逐步独立 try/except(失败报告不中断); 全程幂等可重复
    recalc_modules=True 追加池结构变化收尾三件套(模块3三口径重算+回测新批次)"""
    code = code.strip().upper()
    s = get_stock_from_pool(code)
    if not s:
        return {'ok': False, 'error': f'{code} 不在股票池'}
    market = s.get('market', 'A股')
    rep = {'ok': True, 'code': code, 'market': market, 'steps': {}}

    def step(name, fn):
        try:
            rep['steps'][name] = fn()
            log(f"  [onboard] {code} {name}: OK")
        except Exception as e:
            print(f"[onboard:{code}:{name}] {type(e).__name__}: {e}")
            rep['steps'][name] = {'error': f"{type(e).__name__}: {e}"}
            rep['ok'] = False

    step('quotes_backfill', lambda: _onboard_backfill_quotes(code, market))
    step('depth_verify', lambda: _verify_source_depth(code, market, log=log))
    step('benchmark', lambda: _onboard_ensure_benchmark(market))
    step('indicators', lambda: generate_indicators(code))
    step('signal_replay', lambda: _replay_signals_for(code, market, s.get('name', '')))
    step('wide_table', lambda: generate_wide_table(code, days=3000))
    step('overlay_seed', lambda: _onboard_seed_overlay(code))
    step('join_event', lambda: _onboard_ensure_join(code, s))
    if recalc_modules:
        step('module3_recalc', lambda: run_module3_analysis())
        step('backtests_new_batch', lambda: run_d2_backtests())
    n_q = rep['steps'].get('quotes_backfill')
    if isinstance(n_q, int) and n_q == 0:
        rep['ok'] = False
        rep['error'] = (f'{code} 行情采集返回 0 行 — 检查代码/市场是否正确'
                        f'(港股为5位数字如 00700, 美股为交易所代码如 AAPL)')
    return rep


def _onboard_backfill_quotes(code, market):
    """行情全量回填(QUOTE_BACKFILL_START 起, 与模块8同口径复用采集函数)"""
    days = ((datetime.now() - datetime.strptime(QUOTE_BACKFILL_START, '%Y-%m-%d')).days + 5)
    if market == 'A股':
        n = collect_a_share_daily(code, days)
    elif market == '港股':
        n = collect_hk_daily(code, days)
    else:
        n = collect_us_daily(code, days)
    return n or 0


# --- 模块10 深度校验: 区分『上市新股·源端全量』与『回填截断/源缺口』 ---
_EM_KLINE_HDR = {
    "accept": "*/*",
    "referer": "https://quote.eastmoney.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
}


def _em_kline(code, market, fqt=0, beg='19900101', end='20501231'):
    """东财日线第二源探针/取数(curl_cffi 浏览器指纹 — akshare stock_hk_hist 的
    裸 requests 会被东财远端断连, 同宏观日历的 WAF 问题)
    fqt: 0=不复权(探真实上市起点) 1=前复权(补插数据)
    返回 (name, DataFrame[date,open,close,high,low,volume,amount,amplitude,change_pct])
    无数据/异常返回 (None, None)"""
    if market == '港股':
        secid = f"116.{code}"
    elif market == 'A股':
        secid = ('1.' if code.startswith('6') else '0.') + code
    else:
        return None, None
    try:
        r = cffi_requests.get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            params={"secid": secid,
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "klt": "101", "fqt": str(fqt), "beg": beg, "end": end},
            headers=_EM_KLINE_HDR, impersonate="chrome110", timeout=20)
        r.raise_for_status()
        d = r.json().get('data') or {}
        kl = d.get('klines') or []
        if not kl:
            return None, None
        rows = []
        for line in kl:
            p = line.split(',')
            rows.append({'date': p[0], 'open': float(p[1]), 'close': float(p[2]),
                         'high': float(p[3]), 'low': float(p[4]),
                         'volume': int(float(p[5])), 'amount': float(p[6]),
                         'amplitude': float(p[7]), 'change_pct': float(p[8])})
        return d.get('name'), pd.DataFrame(rows)
    except Exception as e:
        print(f"[_em_kline] {code}: {type(e).__name__}: {e}")
        return None, None


def _save_depth_meta(rec):
    """深度校验结果落 forward_meta(source_depth:<code>): DELETE+INSERT 按键幂等"""
    conn = get_db()
    create_forward_tables(conn)
    key = f"source_depth:{rec['code']}"
    conn.execute("DELETE FROM forward_meta WHERE meta_key=?", (key,))
    conn.execute(
        "INSERT INTO forward_meta(meta_key, result_json, run_date, generated_at) "
        "VALUES(?,?,?,?)",
        (key, json.dumps(rec, ensure_ascii=False, default=str),
         datetime.now().strftime('%Y-%m-%d'),
         datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()


def get_source_depth_notes():
    """UI 读路径(无DDL): {code: 深度校验记录} — 上市新股/回填对齐/源缺口标注"""
    conn = get_db()
    out = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='forward_meta'").fetchone():
        for k, j in conn.execute(
                "SELECT meta_key, result_json FROM forward_meta "
                "WHERE meta_key LIKE 'source_depth:%'"):
            try:
                d = json.loads(j)
                out[d.get('code')] = d
            except Exception:
                pass
    conn.close()
    return out


def _backfill_pool_name(code, name):
    """池内无名股票回填名称(东财 kline 响应自带 name, 深度校验顺带完成 —
    UI 股票池表/事件研究展示均依赖 name, 缺名会让新股显示为空白行)"""
    if not name:
        return 0
    conn = get_db()
    cur = conn.execute("UPDATE stock_pool SET name=? WHERE code=? "
                       "AND (name IS NULL OR name='')", (name, code))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n


def _verify_source_depth(code, market, log=print):
    """模块10 深度校验: 新股回填后区分『上市新股·源端全量』与『回填截断/源缺口』
    本地起点晚于 QUOTE_BACKFILL_START 时, 用东财(独立第二源)探测真实历史起点:
    - 两源起点一致(±7日) → listing_confirmed=True(上市新股, 数据已对齐, 无可补)
    - 东财明显更深 → 源缺口: 取东财 qfq 补插更早区间(重叠段价格校验,
      不一致则整段换源 — 新股无已入库净值, 全量重写安全)
    结果 DELETE+INSERT 落 forward_meta; 美股跳过(东财美股 secid 多交易所前缀不定)"""
    conn = get_db()
    n, local_start = conn.execute(
        "SELECT COUNT(*), MIN(trade_date) FROM daily_quotes WHERE code=?",
        (code,)).fetchone()
    conn.close()
    rec = {'code': code, 'market': market, 'local_rows': n,
           'local_start': local_start,
           'verified_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    if not local_start:
        rec.update({'note': '无行情', 'detail': '深度校验前必须有行情回填'})
        _save_depth_meta(rec)
        return rec
    if market == '美股':
        rec.update({'note': '未校验(美股)', 'detail': '东财美股 secid 多交易所前缀不定, 暂不探测'})
        _save_depth_meta(rec)
        return rec
    if local_start <= QUOTE_BACKFILL_START:
        rec.update({'note': '回填对齐·全量',
                    'detail': f'起点{local_start} ≤ 回填目标{QUOTE_BACKFILL_START}'})
        _save_depth_meta(rec)
        return rec
    name, em_raw = _em_kline(code, market, fqt=0)
    if em_raw is None:
        rec.update({'note': '深度未确认', 'detail': '第二源(东财)无该股数据或请求失败'})
        _save_depth_meta(rec)
        return rec
    em_start = em_raw.iloc[0]['date']
    rec.update({'em_name': name, 'em_start': em_start, 'em_rows': len(em_raw)})
    if name:
        rec['name_backfilled'] = _backfill_pool_name(code, name)
    gap_days = abs((datetime.strptime(em_start, '%Y-%m-%d')
                    - datetime.strptime(local_start, '%Y-%m-%d')).days)
    if gap_days <= 7:
        rec.update({'listing_confirmed': True, 'gap_filled': 0,
                    'note': '上市新股·源端全量',
                    'detail': f'新浪起点{local_start} = 东财起点{em_start}({name}), '
                              f'无可补数据'})
        _save_depth_meta(rec)
        log(f"  [depth] {code}: 上市新股·源端全量({em_start} 起, {name})")
        return rec
    _, em_qfq = _em_kline(code, market, fqt=1)
    if em_qfq is None:
        rec.update({'listing_confirmed': False, 'gap_filled': 0,
                    'note': '深度未确认',
                    'detail': f'东财更深(自{em_start})但 qfq 取数失败, 未补插'})
        _save_depth_meta(rec)
        return rec
    conn = get_db()
    local_map = {r[0]: r[1] for r in conn.execute(
        "SELECT trade_date, close FROM daily_quotes WHERE code=? AND trade_date>=?",
        (code, em_qfq.iloc[0]['date']))}
    conn.close()
    ov = em_qfq[em_qfq['date'].isin(local_map)]
    mism = sum(1 for _, r in ov.iterrows()
               if abs(r['close'] - local_map[r['date']]) / local_map[r['date']] > 0.002)
    if ov.empty or mism:
        df = em_qfq.copy()
        gap = _save_daily_quotes(df, code, market, data_source="eastmoney")
        rec.update({'listing_confirmed': False, 'gap_filled': gap,
                    'note': f'源缺口已补{gap}行(整段换源)',
                    'detail': f'东财自{em_start}更深, 重叠段不一致({mism}/{len(ov)}), '
                              f'整段以东财qfq重写'})
    else:
        df = em_qfq[em_qfq['date'] < local_start].copy()
        gap = _save_daily_quotes(df, code, market, data_source="eastmoney")
        rec.update({'listing_confirmed': False, 'gap_filled': gap,
                    'note': f'源缺口已补{gap}行',
                    'detail': f'东财自{em_start}更深, 重叠段价格一致, 补插更早区间'})
    _save_depth_meta(rec)
    log(f"  [depth] {code}: {rec['note']}")
    return rec


def _onboard_seed_overlay(code):
    """模块10 overlay 历史封锁日单股种子(管道 overlay_days 步的 min_date 守卫只补
    截断日之后, 新股的历史封锁日若不在引导链补齐将永久缺失 → S*M 回测把它当
    『历史上从未被封锁』)。安全性: 新股无已入库净值且入池前无资格触发,
    全历史种子不影响任何已入库净值; 幂等(INSERT OR IGNORE)"""
    rep = refresh_overlay_days(min_date=None, codes=[code], log=lambda m: None)
    if 'error' in rep:
        return {'skipped': rep['error']}
    return {'inserted': rep.get('inserted', 0),
            'ledger_total': rep.get('ledger_total')}


def _onboard_ensure_benchmark(market):
    """该市场基准指数缺失则采集(前向B2与新鲜度日历依赖)"""
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM benchmark_index WHERE market=?",
                     (market,)).fetchone()[0]
    conn.close()
    if n > 0:
        return {'rows': n, 'collected': False}
    collect_benchmark_index(market)
    conn = get_db()
    n2 = conn.execute("SELECT COUNT(*) FROM benchmark_index WHERE market=?",
                      (market,)).fetchone()[0]
    conn.close()
    return {'rows': n2, 'collected': True}


def _onboard_ensure_join(code, s):
    """join 事件兜底: 该股无任何 join 事件时补写(eff=added_at 或今天)"""
    conn = get_db()
    has = conn.execute("SELECT 1 FROM forward_pool_events "
                       "WHERE code=? AND event='join' LIMIT 1", (code,)).fetchone()
    conn.close()
    if has:
        return {'written': False}
    eff = max(FORWARD_START, (s.get('added_at') or '')[:10] or
              datetime.now().strftime('%Y-%m-%d'))
    _m10_event(code, 'join', 'onboard')
    conn = get_db()
    conn.execute("UPDATE forward_pool_events SET eff_date=? "
                 "WHERE code=? AND event='join' AND source='onboard'", (eff, code))
    conn.commit()
    conn.close()
    return {'written': True, 'eff_date': eff}


def get_pool_onboard_status():
    """池内每股数据引导状态(驱动 UI 与管道 onboard_check):
    needs_onboard = 无行情 或 无宽表 或 无指标 或 (有行情但零信号)"""
    conn = get_db()
    events = conn.execute(
        "SELECT code, MIN(eff_date) FROM forward_pool_events "
        "WHERE event='join' GROUP BY code").fetchall()
    first_join = {r[0]: r[1] for r in events}
    depth_note = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='forward_meta'").fetchone():
        for _k, _j in conn.execute(
                "SELECT meta_key, result_json FROM forward_meta "
                "WHERE meta_key LIKE 'source_depth:%'"):
            try:
                _d = json.loads(_j)
                depth_note[_d.get('code')] = _d.get('note', '')
            except Exception:
                pass
    idx_dates = {}
    for m in ['A股', '港股', '美股']:
        idx_dates[m] = [r[0] for r in conn.execute(
            "SELECT trade_date FROM benchmark_index WHERE market=? "
            "ORDER BY trade_date", (m,))]
    out = []
    for s in get_stock_pool(active_only=True):
        code = s['code']
        q = conn.execute(
            "SELECT COUNT(*), MAX(trade_date) FROM daily_quotes WHERE code=?",
            (code,)).fetchone()
        n_sig = conn.execute(
            "SELECT COUNT(*) FROM passive_signals WHERE code=?", (code,)).fetchone()[0]
        n_ind = conn.execute(
            "SELECT COUNT(*) FROM daily_indicators WHERE code=?", (code,)).fetchone()[0]
        n_wide = conn.execute(
            "SELECT COUNT(*) FROM daily_feature_base WHERE code=?",
            (code,)).fetchone()[0]
        ql = q[1]
        lag = (sum(1 for d in idx_dates.get(s['market'], []) if d > ql)
               if ql else len(idx_dates.get(s['market'], [])))
        needs = (q[0] == 0 or n_wide == 0 or n_ind == 0
                 or (q[0] > 0 and n_sig == 0))
        out.append({
            'code': code, 'name': s.get('name', ''), 'market': s['market'],
            'quotes': q[0], 'quote_latest': ql, 'lag_trading_days': lag,
            'signals': n_sig, 'indicators': n_ind, 'wide': n_wide,
            'forward_join': first_join.get(code, FORWARD_START),
            'depth_note': depth_note.get(code, ''),
            'needs_onboard': bool(needs),
            'ready': not needs,
        })
    conn.close()
    return out


def _pipeline_onboard_check(log=print):
    """模块10 管道首步: 活跃池孤儿股自动补课
    (无行情/无宽表/无指标/有行情但零信号 → 自动执行入池引导链)"""
    rep = {'checked': 0, 'onboarded': [], 'results': {}}
    for s in get_pool_onboard_status():
        if not s['needs_onboard']:
            continue
        rep['checked'] += 1
        log(f"  [pipeline] {s['code']} 数据未引导(行情{s['quotes']}/宽表{s['wide']}/"
            f"指标{s['indicators']}/信号{s['signals']}), 自动执行引导链")
        r = onboard_pool_stock(s['code'], log=log)
        rep['onboarded'].append(s['code'])
        rep['results'][s['code']] = {'ok': r.get('ok'),
                                     'error': r.get('error')}
    if rep['checked'] == 0:
        rep['note'] = '全部股票数据就绪, 无需补课'
    return rep


def get_pool_membership_view():
    """模块10 池构成视图(Tab9 ③c): 事件时间线/开放资格成员/B1首批成员/池协议
    读路径无DDL(模块9问题日志#1): forward_meta 用 sqlite_master 探测"""
    conn = get_db()
    events = [dict(r) for r in conn.execute(
        "SELECT code, market, name, event, eff_date, source, run_ts "
        "FROM forward_pool_events ORDER BY eff_date, id")]
    proto_row = None
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='forward_meta'").fetchone():
        proto_row = conn.execute(
            "SELECT result_json FROM forward_meta WHERE meta_key='pool_protocol' "
            "ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    membership = _forward_membership()
    active = {s['code'] for s in get_stock_pool(active_only=True)}
    members = [{'code': c, 'join_eff': j, 'in_pool': c in active}
               for c, ivs in sorted(membership.items())
               for (j, l) in ivs if l is None]
    first_batch = sorted(c for c, ivs in membership.items()
                         if ivs and ivs[0][0] <= FORWARD_START)
    return {
        'events': events,
        'members': members,
        'first_batch': first_batch,
        'forward_start': FORWARD_START,
        'pool_protocol': json.loads(proto_row[0]) if proto_row else None,
    }


_m10_migrate_events()
