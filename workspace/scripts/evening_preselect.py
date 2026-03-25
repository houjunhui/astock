#!/usr/bin/env python3
"""
scripts/evening_preselect.py
每天 23:00 收盘后运行 - 晚盘预选报告

功能：
  - 获取今日涨停池（quicktiny ladder）
  - 获取今日市场情绪数据（温度/涨停/炸板/跌停）
  - 判断市场周期（退潮/冰点/启动/发酵/稳定）
  - 计算每只涨停股的连板数 + 晋级概率
  - 推送飞书消息（主升期 Top10，退潮/冰点 Top5）
"""
import sys, os, sqlite3

# ── 路径 + .env（必须在所有 astock 导入之前）──────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_env = os.path.join(_ROOT, ".env")
if os.path.exists(_env):
    for line in open(_env):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            if k.startswith("export "):
                k = k[7:].strip()
            if k:
                os.environ.setdefault(k, v.strip().strip('"').strip("'"))

from datetime import datetime
from astock.config import DB_PATH
from astock.market import today_str, prev_trading_day
from astock.quicktiny import get_ladder, get_zt_stocks, get_limit_stats, get_market_overview_fixed
from astock.kline import get_kline
from astock.predict_calibrated import predict_stock_v2


def detect_phase(ls, mo):
    """基于收盘数据检测市场周期"""
    mtemp = mo.get("market_temperature", 50) if mo else 50
    dz_rate = ls.get("limitUp", {}).get("yesterday", {}).get("rate", 0) if ls else 0
    dt_count = ls.get("limitDown", {}).get("today", {}).get("num", 0) if ls else 0
    zt_count = ls.get("limitUp", {}).get("today", {}).get("num", 0) if ls else 0
    rise = mo.get("rise_count", 0) if mo else 0
    fall = mo.get("fall_count", 1) if mo else 1

    signals = []
    if (mtemp or 50) < 18: signals.append(("冰点", f"温度{mtemp:.0f}<18"))
    elif (mtemp or 50) < 22: signals.append(("退潮", f"温度{mtemp:.0f}<22"))
    if (dz_rate or 0) > 0.50: signals.append(("恐慌", f"昨日炸板率{dz_rate:.0%}>50%"))
    elif (dz_rate or 0) > 0.38: signals.append(("退潮", f"昨日炸板率{dz_rate:.0%}>38%"))
    if (dt_count or 0) > 50: signals.append(("冰点", f"跌停{dt_count}只>50"))
    if rise and fall > rise * 8: signals.append(("弱市", "下跌家数>>上涨>8倍"))

    if signals:
        # 恐慌/冰点优先
        for s in signals:
            if s[0] in ("恐慌", "冰点"):
                return s[0], s[1]
        return signals[0]
    return "稳定", f"温度{mtemp:.0f}"


def run_preselect(date):
    """执行晚盘预选"""
    date_str = str(date).replace("-", "")
    today_disp = f"{date[:4]}-{date[4:6]}-{date[6:8]}"

    print(f"[晚盘预选] {today_disp} 开始...")

    # ── 1. 市场情绪数据 ───────────────────────────────────
    ls = get_limit_stats(date_str)
    mo = get_market_overview_fixed(date_str)
    phase, phase_reason = detect_phase(ls, mo)

    zt_count = 0
    dt_count = 0
    dz_count = 0
    dz_rate = 0.0
    mtemp = 0.0
    if ls:
        lu = ls.get("limitUp", {}).get("today", {})
        ld = ls.get("limitDown", {}).get("today", {})
        zbgc = ls.get("limitUp", {}).get("yesterday", {})
        zt_count = lu.get("num", 0)
        dt_count = ld.get("num", 0)
        dz_count = zbgc.get("num", 0)
        dz_rate = zbgc.get("rate", 0)
    if mo:
        mtemp = mo.get("market_temperature", 0)

    print(f"[市场] 温度{mtemp:.0f} | 涨停{zt_count} | 炸板{dz_count}({dz_rate:.0%}) | 跌停{dt_count} | 周期:{phase} {phase_reason}")

    # ── 2. 涨停池 ─────────────────────────────────────────
    qt_stocks = get_zt_stocks(date_str)
    if not qt_stocks:
        print("[警告] 涨停池为空，尝试备用数据源...")
        try:
            import akshare as ak
            zt_df = ak.stock_zt_pool_em(date=date)
            if zt_df is not None and not zt_df.empty:
                qt_stocks = []
                for _, row in zt_df.iterrows():
                    code = str(row.get("代码", "")).zfill(6)
                    qt_stocks.append({
                        "code": code,
                        "name": str(row.get("名称", code)),
                        "level": int(row.get("连板数", 1) or 1),
                        "industry": str(row.get("行业", "其他")),
                        "reason": str(row.get("涨停主题", ""))[:50],
                        "limit_up_suc_rate": None,
                    })
        except Exception as e:
            print(f"[错误] 备用数据源也失败: {e}")

    if not qt_stocks:
        print("[错误] 无法获取涨停池")
        return None

    print(f"[涨停池] 共{len(qt_stocks)}只")

    # ── 3. K线预加载（顺序，防 baostock 并发乱码）──────
    print("[K线加载中...]")
    kl_cache = {}
    for i, s in enumerate(qt_stocks):
        kl = get_kline(s["code"])
        if kl:
            kl_cache[s["code"]] = kl
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(qt_stocks)}...")

    print(f"[K线] 成功{len(kl_cache)}/{len(qt_stocks)}只")

    # ── 4. 预测计算 ───────────────────────────────────────
    results = []
    for s in qt_stocks:
        code = s["code"]
        lb = s.get("level", 1) or 1
        kl = kl_cache.get(code)
        if not kl:
            continue

        # 晚盘无竞价数据，传 None
        pred = predict_stock_v2(
            code, lb, kl,
            phase=phase,
            auction_chng=None,
            auction_bid=None,
            zt_yesterday=False,
            sector_signal=0,
            hotlist_signal=0,
        )

        results.append({
            "code": code,
            "name": s.get("name", code),
            "lb": lb,
            "industry": s.get("industry", "其他"),
            "reason": s.get("reason", "")[:50],
            "limit_up_suc_rate": s.get("limit_up_suc_rate"),
            "jb_prob": pred.get("calibrated_jb_prob", pred.get("jb_prob", 0)),
            "xx_prob": pred.get("calibrated_xx_prob", 0),
            "dz_risks": pred.get("dz_risks", []),
            "signal": pred.get("signal", ""),
            "is_yao": pred.get("is_yao", False),
            "raw_jb": pred.get("raw_jb", 0),
            "adj_factor": pred.get("adj_factor", 1.0),
            "kl": kl,
        })

    # ── 5. 排序 ───────────────────────────────────────────
    # 主升/发酵期：晋级率排序；退潮/冰点：降低仓位，只看高概率
    phase_top = {"启动": 10, "发酵": 10, "稳定": 8, "退潮": 5, "冰点": 5, "恐慌": 3, "弱市": 5}
    top_n = phase_top.get(phase, 5)

    results.sort(key=lambda x: (
        # 退潮/冰点：优先看低板位
        0 if phase in ("退潮", "冰点", "恐慌", "弱市") else 1,
        # 妖股优先
        0 if x["is_yao"] else 1,
        # 按晋级概率降序
        -x["jb_prob"],
    ))

    top = results[:top_n]

    # ── 6. 存储到DB ───────────────────────────────────────
    _save_to_db(date, results, phase, phase_reason, zt_count, dt_count, dz_rate, mtemp)

    print(f"[预选完成] 周期:{phase} | 晋级率均值:{sum(r['jb_prob'] for r in results)/len(results):.1f}% | 送出Top{len(top)}")
    return top, results, phase, phase_reason, {
        "zt": zt_count, "dt": dt_count, "dz": dz_count,
        "dz_rate": dz_rate, "mtemp": mtemp,
        "date": today_disp,
    }


def _save_to_db(date, results, phase, phase_reason, zt_count, dt_count, dz_rate, mtemp):
    """保存预选结果到数据库"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    date_fmt = f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(str(date)) == 8 else str(date)

    # daily_stats
    avg_jb = sum(r["jb_prob"] for r in results) / len(results) if results else 0
    conn.execute("""
        INSERT OR REPLACE INTO daily_stats
        (date, total_zt, avg_jb_prob, phase, phase_name, market_trend)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (date_fmt, zt_count, avg_jb, phase, phase_reason, f"温度{mtemp:.0f}"))

    # predictions
    for r in results:
        p = r
        conn.execute("""
            INSERT OR REPLACE INTO predictions
            (date, code, name, lb, industry, jb_prob, signal, outcome, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
        """, (date_fmt, r["code"], r["name"], r["lb"], r.get("industry", ""),
              r["jb_prob"], r["signal"]))

    conn.commit()
    conn.close()


def fmt_feishu(top, all_results, phase, phase_reason, stats):
    """格式化飞书消息"""
    date_disp = stats["date"]
    mtemp = stats["mtemp"]
    zt = stats["zt"]
    dt = stats["dt"]
    dz = stats["dz"]
    dz_rate = stats["dz_rate"]

    # 市场概览
    phase_icon = {"启动": "🚀", "发酵": "🔥", "稳定": "📊", "退潮": "🌊", "冰点": "❄️", "恐慌": "⚠️", "弱市": "📉"}
    icon = phase_icon.get(phase, "📊")

    lines = [
        f"🦞 *晚盘预选报告*  `{date_disp}`",
        f"",
        f"*市场概览*  {icon} {phase} {phase_reason}",
        f"温度 {mtemp:.0f}｜涨停 {zt}｜炸板 {dz}({dz_rate:.0%})｜跌停 {dt}",
        f"",
        f"*预选股票*（Top {len(top)}）",
        f"",
    ]

    # 表头
    lines.append(f"{'代码':<8}{'名称':<8}{'板位':<5}{'晋级率':<8}{'续涨率':<8}{'信号'}")
    lines.append(f"{'--'*18}")

    for r in top:
        code = r["code"]
        name = r["name"][:6]
        lb = r["lb"]
        jb = r["jb_prob"]
        xx = r["xx_prob"]
        sig = r["signal"][:20] if r["signal"] else ""

        # 晋级率颜色标记
        jb_tag = f"✅{jb:.0f}%" if jb >= 30 else (f"🟡{jb:.0f}%" if jb >= 15 else f"⚪{jb:.0f}%")

        risks = r.get("dz_risks", [])
        risk_tag = " 🚨" if risks else (" ⚠️" if any("下降通道" in str(r) or "MACD空头" in str(r) for r in risks) else "")

        lines.append(f"{code:<8}{name:<8}{lb}板{jb_tag}  {xx:.0f}%  {sig}{risk_tag}")

    # 全市场统计
    all_sorted = sorted(all_results, key=lambda x: -x["jb_prob"])
    gt30 = sum(1 for r in all_sorted if r["jb_prob"] >= 30)
    gt15 = sum(1 for r in all_sorted if 15 <= r["jb_prob"] < 30)
    lt15 = len(all_sorted) - gt30 - gt15
    avg_jb = sum(r["jb_prob"] for r in all_sorted) / len(all_sorted) if all_sorted else 0

    lines.append(f"")
    lines.append(f"*全市场晋级分布*")
    lines.append(f"晋级率>30%: {gt30}只 / 15-30%: {gt15}只 / <15%: {lt15}只")
    lines.append(f"均晋级率: {avg_jb:.1f}%")

    # 操作建议
    if phase in ("退潮", "冰点", "恐慌"):
        lines.append(f"")
        lines.append(f"⚠️ 当前周期：{phase}，建议降低仓位，最多持仓{len(top)}只")
    elif phase in ("启动", "发酵"):
        lines.append(f"")
        lines.append(f"🚀 当前周期：{phase}，积极操作，重点关注Top5")

    return "\n".join(lines)


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else today_str()
    result = run_preselect(date)
    if result is None:
        print("[晚盘预选] 无数据，退出")
        return

    top, all_results, phase, phase_reason, stats = result
    msg = fmt_feishu(top, all_results, phase, phase_reason, stats)
    print("\n" + "=" * 50)
    print(msg)
    print("=" * 50)

    # 写入文件供 cron 推送
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "astock", "cache", "evening_preselect.txt")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(msg)
    print(f"\n[输出] 已写入 {out_path}")


if __name__ == "__main__":
    main()
