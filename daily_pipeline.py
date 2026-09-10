"""模块7 每日管道入口脚本（收盘后运行一次）

用法: python daily_pipeline.py
步骤: 孤儿股补课 -> 行情 -> 分钟数据(模块11) -> 指数 -> 指标 -> 信号扫描 ->
      宽表 -> 宏观 -> 封锁日台账 -> 前向记录 -> 新鲜度检查 (10步)
退出码: 0=全部成功, 1=存在失败步骤
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)
sys.path.insert(0, _HERE)

import quant_data  # noqa: E402


def main():
    print("=" * 60)
    print("[daily_pipeline] start, cwd =", _HERE)
    report = quant_data.run_daily_pipeline(log=print)
    print("-" * 60)

    failed = [k for k, v in report['steps'].items()
              if isinstance(v, dict) and 'error' in v]
    for k in failed:
        print(f"[daily_pipeline] STEP FAILED {k}: {report['steps'][k]['error']}")
    if not failed:
        print("[daily_pipeline] all steps OK")

    fr = report.get('freshness', {})
    for s in fr.get('alerts', []):
        print(f"[daily_pipeline] FRESHNESS ALERT: {s['code']}({s['name']}) "
              f"lag {s['lag_trading_days']} trading days, quotes to {s['quote_latest']}")

    print("[daily_pipeline] done")
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
