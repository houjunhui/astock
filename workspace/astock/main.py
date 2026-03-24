#!/usr/bin/env python3
"""
astock.main
CLI入口 + 命令路由
"""
import sys, json, sqlite3, os
from datetime import datetime

# 加载 .env 环境变量（兼容 bash export 格式）
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_env_path):
    for line in open(_env_path):
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

# 导入各模块
from config import DB_PATH, EXIT_RULES
from market import date_from_str, today_str, next_trading_day, prev_trading_day
try:
    from quicktiny_kline import get_kline
except ImportError:
    from kline import get_kline
from kline import get_next_close
from predict import load_coef  # noqa: F401
from predict_calibrated import predict_stock_v2 as predict_stock
from db import get_db, init_db, save_predictions, save_daily_stats, save_historical_zt
from calibration import update_calibration, get_calibration_report, suggest_coef_adjustments
from auction import auction_ok
from exit_signals import eval_exit_signals
from market_data import get_zt_pool, get_zbgc_pool, get_dtgc_pool, get_market_sentiment
from wudao import batch_load_signals
import akshare as ak


# ===================== 核心逻辑 =====================

def detect_market_phase(conn):
    """检测市场情绪周期"""
    rows = conn.execute(
        "SELECT date, total_zt, zt_rate, dz_rate FROM daily_stats ORDER BY date DESC LIMIT 5"
    ).fetchall()
    if len(rows) < 2:
        return '未知', '样本不足'

    zt_trend = rows[0]['total_zt'] - rows[-1]['total_zt']
    dz_avg = sum(r['dz_rate'] or 0 for r in rows[:3]) / min(3, len(rows))

    if dz_avg > 30 or zt_trend < -10:
        return '退潮', f'炸板率{dz_avg:.0f}%/涨停递减'
    if rows[0]['total_zt'] < 30:
        return '冰点', f'涨停仅{rows[0]["total_zt"]}只'
    if zt_trend > 10:
        return '启动', f'涨停逐日增加'
    if dz_avg > 15:
        return '发酵', f'炸板率{dz_avg:.0f}%'
    return '稳定', '市场平稳'


def detect_auction_sentiment(date, auction_map=None):
    """
    从传入的 auction_map 计算竞价情绪。
    auction_map: {code: {changeRate, ...}} 或 None
    """
    if auction_map is None:
        auction_map = {}

    nd = next_trading_day(date)
    prev = prev_trading_day(date)
    if not prev:
        return None, None, {}, set()

    try:
        zt_prev_df, _ = get_zt_pool(prev)
        if zt_prev_df is None or zt_prev_df.empty:
            return None, None, {}, set()
        prev_codes = set(str(r['代码']).zfill(6) for _, r in zt_prev_df.iterrows())
    except Exception:
        return None, None, {}, set()

    low_open, high_open, normal_open = 0, 0, 0
    details = {}
    for code, ad in auction_map.items():
        if code not in prev_codes:
            continue
        change = ad.get('changeRate', 0) if isinstance(ad, dict) else 0
        if change < 0:
            low_open += 1
        elif change > 5:
            high_open += 1
        else:
            normal_open += 1
        details[code] = change
    total = low_open + high_open + normal_open
    if total == 0:
        return None, None, {}, prev_codes
    return low_open / total, high_open / total, details, prev_codes


def run_predict(date):
    """执行预测逻辑"""
    print(f"[正在获取 {date} 涨停数据...]")

    # ===== 1. 获取今日涨停池 =====
    zt_df, reasons_ak = get_zt_pool(date)
    if zt_df is None or len(zt_df) == 0:
        print(f"获取涨停池失败")
        return None, None, None, None

    # ===== 2. 获取昨日涨停池（用于竞价情绪判断） =====
    prev = prev_trading_day(date)
    prev_lb_codes = set()
    try:
        zt_prev_df, _ = get_zt_pool(prev)
        if zt_prev_df is not None and not zt_prev_df.empty:
            prev_lb_codes = set(str(r['代码']).zfill(6) for _, r in zt_prev_df.iterrows())
    except Exception:
        pass

    # ===== 3. 合并所有需要查竞价数据的代码 =====
    today_codes = [str(r['代码']).zfill(6) for _, r in zt_df.iterrows()]
    all_codes = list(set(today_codes) | prev_lb_codes)
    zt_yesterday_codes = prev_lb_codes  # 兼容旧变量名

    # ===== 4. 一次性获取所有竞价数据（避免重复API调用） =====
    auction_by_code = {}
    try:
        from quicktiny import get_auction_for_codes
        auction_raw = get_auction_for_codes(all_codes, delay=2.2)
        for code, ad in auction_raw.items():
            auction_by_code[code] = {
                'chng': ad.get('changeRate', 0),
                'bid': 0,
            }
        print(f"[竞价数据] 获取 {len(auction_by_code)} 只（今日{len(today_codes)} + 昨日{len(prev_lb_codes)}）")
    except Exception as e:
        print(f"[竞价数据] 获取失败: {e}")

    # ===== 5. 竞价情绪判断（复用已获取的竞价数据） =====
    low_ratio, high_ratio, auction_lb_details = None, None, {}
    auction_sentiment = ''
    try:
        low_ratio, high_ratio, auction_lb_details, _ = detect_auction_sentiment(date, auction_by_code)
        if low_ratio is not None:
            if low_ratio > 0.5:
                auction_sentiment = f'退潮延续(昨日连板{low_ratio:.0%}低开)⚠️'
            elif high_ratio > 0.5:
                auction_sentiment = f'回暖确认(昨日连板{high_ratio:.0%}高开)✅'
    except Exception:
        pass

    # ===== 6. 行业和涨停原因 =====
    try:
        from quicktiny import get_zt_stocks
        qt_stocks = get_zt_stocks(date)
        qt_industry = {s["code"]: s.get("industry", "其他") for s in qt_stocks}
        qt_reason = {s["code"]: s.get("reason", "") for s in qt_stocks}
    except Exception:
        qt_industry = {}
        qt_reason = {}
    for code in qt_reason:
        if qt_reason.get(code):
            reasons_ak[code] = qt_reason[code]

    # ===== 7. 板块统计 =====
    sector_raw = {}
    for _, r in zt_df.iterrows():
        code = str(r['代码']).zfill(6)
        ind = qt_industry.get(code, str(r.get('行业', '其他')))
        if ind not in sector_raw:
            sector_raw[ind] = {'total': 0}
        sector_raw[ind]['total'] += 1

    # ===== 8. 市场情绪（quicktiny limit-stats 接口） =====
    try:
        from quicktiny import get_limit_stats, get_market_overview_fixed
        ls = get_limit_stats(date)
        mo = get_market_overview_fixed(date)
        if ls and mo:
            lu = ls.get("limitUp", {}).get("today", {})
            ld = ls.get("limitDown", {}).get("today", {})
            zbgc_count = ls.get("limitUp", {}).get("yesterday", {}).get("num", 0)  # 昨日涨停→今日炸板
            zbgc_rate_pct = ls.get("limitUp", {}).get("yesterday", {}).get("rate", 0)
            zt_count = lu.get("num", len(zt_df))
            dt_count = ld.get("num", 0)
            mtemp = mo.get("market_temperature", 0)
            sentiment = (
                f"涨停{zt_count}只 昨日炸板{zbgc_count}只({zbgc_rate_pct:.0%}) "
                f"跌停{dt_count}只 市场温度{mtemp:.0f}"
            )
        else:
            sentiment = "涨停28只 炸板0只 跌停0只"
    except Exception:
        sentiment = "涨停28只 炸板0只 跌停0只"
        zbgc_rate = 0

    # ===== 9. 市场周期（实时数据优先）=====
    # 短期情绪：基于quicktiny实时数据，覆盖长期周期判断
    short_term_phase = "未知"
    short_term_reason = ""
    if ls and mo:
        mtemp = mo.get("market_temperature", 50)
        dz_rate = ls.get("limitUp", {}).get("yesterday", {}).get("rate", 0.5)  # 昨日炸板率
        dt_count = ls.get("limitDown", {}).get("today", {}).get("num", 0)
        rise = mo.get("rise_count", 0)
        fall = mo.get("fall_count", 1)

        # 熔断信号：多个指标共振→强制定为冰点/恐慌
        signals = []
        if mtemp < 18: signals.append(("温度<18", "冰点"))
        elif mtemp < 22: signals.append(("温度<22", "退潮"))
        if dz_rate > 0.50: signals.append((f"炸板率{dz_rate:.0%}>50%", "恐慌"))
        elif dz_rate > 0.38: signals.append((f"炸板率{dz_rate:.0%}>38%", "退潮"))
        if dt_count > 50: signals.append((f"跌停{dt_count}只>50", "冰点"))
        if fall > rise * 8: signals.append(("下跌家数>>上涨>8倍", "弱市"))

        # 短期情绪override规则
        if any(s[0] for s in signals):
            # 冰点/恐慌信号 → 强制
            panic_signals = [s for s in signals if s[1] in ("冰点", "恐慌")]
            if panic_signals:
                short_term_phase = panic_signals[0][1]
                short_term_reason = panic_signals[0][0]
            elif signals:
                short_term_phase = signals[0][1]
                short_term_reason = signals[0][0]

    # 长期周期（DB历史数据）
    conn = get_db()
    base_phase, base_phase_name = detect_market_phase(conn)
    # 短期情绪强时覆盖长期
    if short_term_phase not in ("未知", ""):
        phase = short_term_phase
        phase_name = f'实时{short_term_phase} | {short_term_reason} | {base_phase_name}'
    elif auction_sentiment:
        phase = base_phase
        phase_name = f'{base_phase_name} | {auction_sentiment}'
    else:
        phase = base_phase
        phase_name = base_phase_name

    # ===== 悟道信号批量加载（5个优化方向）=====
    import time as _time
    _t0 = _time.time()
    _wudao_raw = batch_load_signals(date)
    zt_detail = _wudao_raw.get("zt_detail", {})
    sector_data = _wudao_raw.get("sector_data", {})
    hotlist_signal = _wudao_raw.get("hotlist", {}).get("signal", 0)

    # 构建：行业名 → 四象限信号
    sector_map = {}
    if sector_data:
        for name in (sector_data.get("high_strong") or []):
            sector_map[name] = 1
        for name in (sector_data.get("low_weak") or []):
            sector_map[name] = -1
        for name in (sector_data.get("high_weak") or []):
            sector_map[name] = 0

    # 悟道信号合并（含预留的研报/龙虎榜，未来填充）
    wsignals = {
        "zt_detail": zt_detail,
        "sector_map": sector_map,
        "hotlist_signal": hotlist_signal,
        "research": {},   # 研报（未来批量预加载）
        "dragon_tiger": {},  # 龙虎榜（未来批量预加载）
    }

    print(f"[悟道信号] 板块:{len(sector_map)}个强势|热榜信号:{hotlist_signal} | 加载耗时:{(_time.time()-_t0)*1000:.0f}ms")

    # ===== 预加载所有K线（顺序，防限流+防磁盘争用）=====
    _t_kl = _time.time()
    kl_cache = {}
    for _, r in zt_df.iterrows():
        code = str(r['代码']).zfill(6)
        kl_cache[code] = get_kline(code)
    print(f"[K线预加载] {len(kl_cache)}只 耗时:{(_time.time()-_t_kl)*1000:.0f}ms")

    # 并发填充预测结果（已预加载K线，无IO争用）
    from concurrent.futures import ThreadPoolExecutor

    def predict_one(idx):
        row = zt_df.iloc[idx]
        code = str(row['代码']).zfill(6)
        name = str(row.get('名称', code))
        lb = int(row.get('连板数', 1) or 1)
        industry = qt_industry.get(code, str(row.get('行业', '其他')))
        reason = reasons_ak.get(code, '')
        kl = kl_cache.get(code)
        if kl is None:
            return None
        ac = auction_by_code.get(code, {})
        is_zt_yesterday = code in (zt_yesterday_codes or set())
        pred = predict_stock(code, lb, kl, phase=phase,
                             auction_chng=ac.get('chng'),
                             auction_bid=ac.get('bid'),
                             zt_yesterday=is_zt_yesterday,
                             research_signal=wsignals.get('research', {}).get(code),
                             dragon_tiger_signal=wsignals.get('dragon_tiger', {}).get(code),
                             sector_signal=wsignals.get('sector_map', {}).get(industry, 0),
                             hotlist_signal=wsignals.get('hotlist_signal', 0),
                             zt_detail=wsignals.get('zt_detail', {}).get(code))
        return {
            'date': date, 'code': code, 'name': name,
            'lb': lb, 'industry': industry,
            'reason': reason,
            'trend': kl.get('trend'), 'rsi': kl.get('rsi'), 'vr': kl.get('vr'),
            'macd_state': kl.get('macd_state'), 'vol_status': kl.get('vol_status'),
            'price_vs_ma20': kl.get('price_vs_ma20'),
            'kline': kl,
            'prediction': pred,
        }

    rows_list = list(zt_df.iterrows())
    results = []
    from concurrent.futures import wait
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(predict_one, i) for i in range(len(rows_list))]
        done, pending = wait(futures, timeout=30.0)
        for future in done:
            try:
                result = future.result(timeout=1)
                if result is not None:
                    results.append(result)
            except Exception:
                pass
        for f in pending:
            f.cancel()

    # 按板位和晋级概率排序
    results.sort(key=lambda x: (x['lb'], x['prediction']['jb_prob']), reverse=True)

    save_predictions(date, results, phase, phase_name)

    # 保存当日涨停到历史表（自然积累，从今天开始）
    hist_stocks = []
    for r in results:
        p = r.get('prediction', {})
        kl_data = r.get('kline', {})
        prev_close = kl_data.get('close', 0) / (1 + p.get('pct_chg', 0) / 100) if p.get('pct_chg', 0) != 0 else kl_data.get('close', 0)
        is_cyb = r['code'].startswith(('300', '688', '301'))
        zt_pct = 20.0 if is_cyb else 10.0
        zt_price = round(prev_close * (1 + zt_pct / 100), 2) if prev_close else 0
        hist_stocks.append({
            'code': r['code'], 'name': r['name'],
            'close': kl_data.get('close', 0),
            'high': kl_data.get('high', 0),
            'zt_price': zt_price,
            'pct_chg': p.get('pct_chg', 0),
            'turn_rate': 0,
            'reason': r.get('reason', ''),
            'industry': r.get('industry', ''),
            'lb': r.get('lb', 1),
        })
    if hist_stocks:
        # 统一日期格式为 YYYY-MM-DD
        date_fmt = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
        save_historical_zt(date_fmt, hist_stocks)

    avg_jb = sum(r['prediction']['jb_prob'] for r in results) / len(results) if results else 0
    print(f"[预测完成: {len(results)}只 | 情绪:{phase_name} | 均晋级概率:{avg_jb:.1f}%]")
    conn.close()
    return results, phase, phase_name, sector_raw, sentiment, zt_yesterday_codes


def run_outcome(date):
    """追踪T日预测的T+1实际结局"""
    conn = get_db()
    nd = next_trading_day(date)
    print(f"[T日 {date} → T+1日 {nd}]")

    try:
        next_zt = ak.stock_zt_pool_em(date=nd)
        zt_df = ak.stock_zt_pool_em(date=date)
    except Exception as e:
        print(f"获取涨停池失败: {e}")
        return

    codes21 = {str(r['代码']).zfill(6): int(r.get('连板数', 1) or 1) for _, r in next_zt.iterrows()}
    codes20 = {str(r['代码']).zfill(6): int(r.get('连板数', 1) or 1) for _, r in zt_df.iterrows()}
    in_next = set(codes21.keys())

    rows = conn.execute("SELECT * FROM predictions WHERE date=?", (date,)).fetchall()
    jb_c = xux_c = duan_c = 0

    for row in rows:
        code = row['code']
        lb21 = codes21.get(code, 0)
        lb20 = row['lb'] or 1

        if lb21 > lb20:
            outcome = '晋级'
            detail = f'{lb20}板→{lb21}板'
            jb_c += 1
        elif code not in in_next:
            outcome = '断板'
            detail = '未续涨'
            duan_c += 1
        else:
            outcome = '续涨'
            detail = f'维持{lb21}板'
            xux_c += 1

        conn.execute(
            "UPDATE predictions SET outcome=?,detail=?,actual_boards=? WHERE date=? AND code=?",
            (outcome, detail, lb21 if lb21 > 0 else None, date, code)
        )

    conn.commit()
    update_calibration(conn)
    conn.close()

    total = len(rows)
    print(f"实际晋级: {jb_c}/{total}={jb_c/total*100:.1f}%  续涨:{xux_c}只({xux_c/total*100:.1f}%)  断板:{duan_c}只({duan_c/total*100:.1f}%)")

    return {
        'jb': jb_c, '续涨': xux_c, '断板': duan_c, 'total': total,
        'nd': nd, 'date': date
    }


# ===================== CLI命令 =====================

def cmd_predict(date):
    ret = run_predict(date)
    if ret[0] is None:
        return
    results, phase, phase_name, sector_raw, sentiment, zt_yesterday_codes = ret

    from formatter import fmt_predict_terminal, fmt_auction_mobile
    print(fmt_predict_terminal(date, results, sector_raw, phase, phase_name, sentiment))
    print()
    print(fmt_auction_mobile(results, sector_raw, phase, zt_yesterday_codes))


def cmd_outcome(date):
    result = run_outcome(date)
    if not result:
        return

    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM predictions WHERE date=? ORDER BY jb_prob DESC", (date,)
    ).fetchall()

    print(f"\n{'=' * 60}\n  追踪结局 {date}\n{'=' * 60}")
    print(f"{'代码':<8}{'名称':<10}{'板':<4}{'晋级%':<7}{'判断':<6}{'实际'}")
    print('-' * 50)
    for row in rows:
        jb_p = row['jb_prob']
        correct = (row['outcome'] == '晋级' and jb_p > 50) or (row['outcome'] != '晋级' and jb_p <= 50)
        icon = '✅' if correct else '⚠️'
        detail = row['detail'] or row['outcome'] or ''
        print(f"{row['code']:<8}{row['name'][:8]:<10}{row['lb']:<4}板 {jb_p:<6.1f}% {icon}  {detail}")
    conn.close()


def cmd_accuracy():
    conn = get_db()
    report = get_calibration_report(conn)
    if report:
        print(f"\n{'=' * 50}\n  概率标定报告\n{'=' * 50}\n{report}")
    else:
        print("样本不足，等待更多数据积累后查看标定报告。")
    conn.close()


def cmd_positions(all_flag=False):
    conn = get_db()
    if all_flag:
        rows = conn.execute("SELECT * FROM positions ORDER BY created_at DESC").fetchall()
        label = "全部持仓"
    else:
        rows = conn.execute("SELECT * FROM positions WHERE status='持仓' ORDER BY created_at DESC").fetchall()
        label = "当前持仓"

    if not rows:
        print(f"暂无{'活跃' if not all_flag else ''}持仓记录。")
        conn.close()
        return

    exits = eval_exit_signals(conn)

    print(f"\n{'=' * 60}\n  {label}\n{'=' * 60}")
    print(f"{'代码':<8}{'名称':<10}{'买入日':<10}{'买入价':<10}{'现价':<10}{'浮盈%':<10}{'持仓天':<8}{'状态'}")
    print('-' * 70)
    for r in rows:
        print(f"{r['code']:<8}{r['name']:<10}{r['buy_date']:<10}{r['buy_price']:<10.2f}"
              f"{r['cur_price']:<10.2f}{r['profit_pct']:<10.1f}{r['hold_days']:<8}{r['status']}")

    if exits:
        print(f"\n{'=' * 60}\n  卖出信号\n{'=' * 60}")
        for e in exits:
            if e['signal']:
                print(f"  {e['name']}({e['code']}): {e['signal']}")
    conn.close()


def cmd_buy(date, code, buy_price):
    conn = get_db()
    name = code  # 简化，可通过接口查询名称
    cur_p = get_next_close(code) or float(buy_price)
    profit_pct = 0.0

    conn.execute("""
        INSERT INTO positions (code, name, buy_date, buy_price, cur_price, profit_pct, hold_days, status)
        VALUES (?,?,?,?,?,?,0,'持仓')
    """, (code, name, date, float(buy_price), cur_p, profit_pct))
    conn.commit()
    conn.close()
    print(f"已记录买入: {code} {name} @ {buy_price} ({date})")


def cmd_sell(code):
    conn = get_db()
    conn.execute("UPDATE positions SET status='已卖出' WHERE code=? AND status='持仓'", (code,))
    conn.commit()
    row = conn.execute("SELECT * FROM positions WHERE code=? ORDER BY id DESC LIMIT 1", (code,)).fetchone()
    profit = row['profit_pct'] if row else 0.0
    conn.close()
    print(f"已卖出: {code}  浮盈: {profit:.1f}%")


def cmd_apply_coef():
    print("系数调整需人工确认后执行，暂不支持自动应用。")


# ===================== CLI路由 =====================

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'help'

    if cmd == 'predict':
        date = sys.argv[2] if len(sys.argv) > 2 else today_str()
        cmd_predict(date)

    elif cmd == 'outcome':
        if len(sys.argv) < 3:
            print("用法: outcome <日期>"); return
        cmd_outcome(sys.argv[2])

    elif cmd == 'accuracy':
        cmd_accuracy()

    elif cmd == 'positions':
        all_flag = (len(sys.argv) > 2 and sys.argv[2] == 'all')
        cmd_positions(all_flag)

    elif cmd == 'buy':
        if len(sys.argv) < 5:
            print("用法: buy <日期> <代码> <买入价>"); return
        cmd_buy(sys.argv[2], sys.argv[3], sys.argv[4])

    elif cmd == 'sell':
        if len(sys.argv) < 3:
            print("用法: sell <代码>"); return
        cmd_sell(sys.argv[2])

    elif cmd == 'init':
        from db import init_db as _init
        print(f"数据库初始化完成: {_init()}")

    else:
        print(f"""用法:
  predict [日期]           - 预测+竞价自检
  outcome <日期>           - 追踪结局
  accuracy                 - 概率标定报告
  positions [all]          - 查看持仓（加all看历史）
  buy <日期> <代码> <买入价> - 记录买入
  sell <代码>              - 卖出
  init                     - 初始化数据库""")


if __name__ == '__main__':
    main()
