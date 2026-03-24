#!/usr/bin/env python3
"""
auto_monitor.py - 盘中持仓智能监控（增强版）
风控优先级（只执行最高优先级一条）：
  1. 止损：买入价×96% → 全仓平
  2. 炸板回落：≥4%非龙头/≥6%龙头 → 全仓平
  3. 目标价：达到目标 → 全仓止盈
  4. 动态止盈：高点回落40%+浮盈6% → 全仓止盈
  5. 浮亏处理：-2%~-4%降仓50%，≥-5%全仓平
滑点模拟：平仓价±0.5%
"""
import sys, os, time
from datetime import datetime, date
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from astock.position import (
    init_files, load_portfolio, close_position, reduce_position,
    get_current_price, get_intraday_low, get_intraday_high,
    add_position, get_minute
)
from astock.strategy_params import get_params

# ── 超时保护 ────────────────────────────────────────────────────────
import signal
from functools import wraps

class TimeoutError(Exception):
    pass

def with_timeout(seconds, default=None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            def handler(sig, frame):
                raise TimeoutError()
            old_h = signal.signal(signal.SIGALRM, handler)
            signal.alarm(seconds)
            try:
                return func(*args, **kwargs)
            except TimeoutError:
                return default
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_h)
        return wrapper
    return decorator


@with_timeout(5, None)
def get_intraday_peak(code):
    """获取当日最高价"""
    try:
        mins = get_minute(code, ndays=1)
        if mins:
            return max(m[2] for m in mins)
    except:
        pass
    return None


@with_timeout(5, None)
def calc_broken_price(code, buy_price, notes):
    """
    推算涨停基准价
    从notes里的'竞价+X%'估算昨日收盘
    """
    try:
        chg_str = notes.split("竞价+")[1].split("%")[0]
        chg = float(chg_str)
        if chg > 0:
            prev_close = buy_price / (1 + chg / 100)
            return round(prev_close * 1.10, 2)  # 涨停价
    except:
        pass
    return None


def check_position(pos):
    """
    检查单只持仓，返回动作（按优先级，只返回最高优先级一条）
    返回: {action, reason, close_price}
    """
    params = get_params()
    code = pos["code"]
    name = pos["name"]
    buy_price = float(pos["buy_price"])
    qty = int(pos["qty"])
    stop_loss = float(pos["stop_loss"])
    target = float(pos["target_price"])
    level = int(pos.get("level") or 0)
    notes = pos.get("notes", "")

    cur = with_timeout(5, None)(get_current_price)(code)
    low = with_timeout(5, None)(get_intraday_low)(code)
    high = with_timeout(5, None)(get_intraday_high)(code)
    peak = get_intraday_peak(code)

    if cur is None or cur <= 0:
        return None

    pnl_pct = (cur - buy_price) / buy_price * 100 if buy_price > 0 else 0

    # ── 0. 保本止损（浮盈≥5%上移止损至买入价，≥10%上移至×1.05）──
    effective_stop = stop_loss
    if buy_price > 0:
        if pnl_pct >= params.get('lock_profit_threshold', 0.10) * 100:
            effective_stop = round(buy_price * (1 + params.get('lock_profit_threshold', 0.10)), 2)
        elif pnl_pct >= params.get('breakeven_threshold', 0.05) * 100:
            effective_stop = buy_price  # 保本

    # ── 1. 止损（-4%必走）──
    if effective_stop > 0 and low is not None and low <= effective_stop:
        # 实际用low触及价含滑点平仓
        close = round(low * 0.995, 2)
        return {
            "action": "stop_loss",
            "reason": f"触及止损({low}<={effective_stop}){'【锁定5%利润】' if pnl_pct>=10 else ('【保本】' if pnl_pct>=5 else '')}",
            "close_price": close,
        }

    # ── 2. 炸板回落（涨停板专属）──
    if level >= 1:
        limit_price = calc_broken_price(code, buy_price, notes)
        if limit_price and limit_price > 0:
            broken_pct = (limit_price - cur) / limit_price * 100 if limit_price > 0 else 0
            threshold = params.get('broken_limit_dragon', 0.06) * 100 if level >= 3 else params.get('broken_limit_normal', 0.04) * 100  # 龙头6%，非龙头4%
            if broken_pct >= threshold:
                close = round(cur * 0.995, 2)  # 滑点
                return {
                    "action": "broken_limit",
                    "reason": f"炸板回落{broken_pct:.1f}%({limit_price}→{cur})",
                    "close_price": close,
                }

    # ── 3. 目标价止盈 ──
    if target > 0 and high is not None and high >= target:
        close = round(target * 0.995, 2)
        return {
            "action": "target_hit",
            "reason": f"触及目标价({high}>={target})",
            "close_price": close,
        }

    # ── 4. 动态止盈（回落40%+浮盈6%）──
    if peak is not None and peak > 0 and peak > buy_price:
        drawdown = (peak - cur) / peak * 100
        profit = (peak - buy_price) / buy_price * 100
        if drawdown >= params.get('trailing_profit_pct', 0.40) * 100 and profit >= params.get('trailing_profit_min', 6):
            close = round(cur * 0.995, 2)
            return {
                "action": "trailing_profit",
                "reason": f"从高点{peak}回落{drawdown:.0f}%止盈(浮盈{profit:.1f}%)",
                "close_price": close,
            }

    # ── 5. 浮亏处理（仅未触发以上条件时执行）──
    if pnl_pct <= -params.get('close_threshold', 0.05) * 100:
        close = round(cur * 0.995, 2)
        return {
            "action": "full_close",
            "reason": f"浮亏{pnl_pct:.1f}%>=-5%清仓",
            "close_price": close,
        }
    elif pnl_pct <= -params.get('reduce_threshold', 0.02) * 100:
        # 降仓50%（剩余不足1手时直接清仓）
        half_qty = qty // 2
        close = round(cur * 0.995, 2)
        if half_qty >= 100:
            return {
                "action": "reduce",
                "reason": f"浮亏{pnl_pct:.1f}%降仓50%",
                "close_price": close,
                "reduce_qty": half_qty,
            }
        else:
            # 剩余不足1手，直接清仓
            return {
                "action": "full_close",
                "reason": f"浮亏{pnl_pct:.1f}%清仓（不足1手）",
                "close_price": close,
            }

    return None


def monitor():
    """监控所有持仓，返回操作记录（含T+1+超期+自动重试）"""
    init_files()
    # 自动重试加载持仓
    positions = None
    for _ in range(3):
        try:
            positions = load_portfolio()
            break
        except Exception:
            continue
    if positions is None:
        return [], [], ["⚠️ 持仓加载失败"]

    closed = []
    reduced = []
    alerts = []

    # ── T+1 规则：今日新仓不允许在盘中平仓 ──
    today = date.today().strftime("%Y%m%d")

    closed = []
    reduced = []
    alerts = []

    # ── T+1 规则：今日新仓不允许在盘中平仓 ──
    today = date.today().strftime("%Y%m%d")

    for pos in positions:
        buy_date = pos.get("buy_date", "")
        if buy_date and buy_date == today:
            # 今日新开仓，T+1限制，不得触发平仓
            continue

        # ── 持仓超期检查：超过max_days必须强制平仓 ──
        max_days = int(pos.get("max_days", 1))
        buy_dt = None
        try:
            buy_dt = datetime.strptime(buy_date, "%Y%m%d")
        except Exception:
            pass
        if buy_dt:
            days_held = (datetime.now() - buy_dt).days
            if days_held >= max_days:
                # 超期强制平仓
                cur = with_timeout(5, None)(get_current_price)(pos["code"])
                close_price = round(cur * (1 - 0.0005), 2) if cur else float(pos["buy_price"])
                reason = f"持仓超期({days_held}天≥{max_days}天最大期限)"
                close_position(pos["code"], close_price, reason)
                closed.append({
                    "action": "force_close",
                    "reason": reason,
                    "close_price": close_price,
                    "code": pos["code"], "name": pos["name"],
                    "buy_price": float(pos["buy_price"]), "close_price": close_price,
                    "qty": int(pos["qty"]),
                    "pnl_pct": (close_price - float(pos["buy_price"])) / float(pos["buy_price"]) * 100 if float(pos["buy_price"]) else 0
                })
                alerts.append(f"🚨 {pos['name']}({pos['code']}) 持仓{days_held}天超期，强制平仓")
                continue

        result = check_position(pos)
        if result is None:
            continue

        code = pos["code"]
        name = pos["name"]
        buy_price = float(pos["buy_price"])
        qty = int(pos["qty"])
        cur = result["close_price"]
        action = result["action"]

        if action in ("stop_loss", "target_hit", "trailing_profit", "broken_limit", "full_close"):
            close_position(code, cur, result["reason"])
            pnl_pct = (cur - buy_price) / buy_price * 100 if buy_price > 0 else 0
            closed.append({**result, "code": code, "name": name,
                           "buy_price": buy_price, "close_price": cur,
                           "qty": qty, "pnl_pct": pnl_pct})

        elif action == "reduce":
            # 降仓：使用reduce_position保留剩余持仓
            rqty = result["reduce_qty"]
            reduce_position(code, rqty, cur, result["reason"])
            reduced.append({**result, "code": code, "name": name,
                            "buy_price": buy_price, "close_price": cur,
                            "reduce_qty": rqty,
                            "remaining_qty": qty - rqty,
                            "pnl_pct": (cur - buy_price) / buy_price * 100 if buy_price > 0 else 0})

    # 预警：浮亏持仓（3%≤浮亏<止损线）
    positions_now = load_portfolio()
    for pos in positions_now:
        if pos.get("status") in ("持仓", "持仓中"):
            cur = with_timeout(5, None)(get_current_price)(pos["code"])
            if cur:
                pnl = (cur - float(pos["buy_price"])) / float(pos["buy_price"]) * 100
                if -3 <= pnl < 0:
                    alerts.append(f"⚠️ {pos['name']}({pos['code']}) 浮亏{pnl:.1f}%，在警戒线内")
                elif pnl >= 8:
                    peak = get_intraday_peak(pos["code"])
                    if peak:
                        dd = (peak - cur) / peak * 100
                        alerts.append(f"📌 {pos['name']}({pos['code']}) 浮盈{pnl:.1f}%，高点{peak}回落{dd:.0f}%")

    return closed, reduced, alerts


def format_report(closed, reduced, alerts, date_str=None):
    now = datetime.now().strftime("%H:%M")
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    lines = [f"【📊 持仓监控】{date_str} {now}"]

    if closed:
        for c in closed:
            emoji = "✅" if c["pnl_pct"] >= 0 else "❌"
            action_map = {
                "stop_loss": "止损",
                "target_hit": "目标止盈",
                "trailing_profit": "动态止盈",
                "broken_limit": "炸板出局",
                "full_close": "浮亏清仓",
            }
            action = action_map.get(c["action"], c["action"])
            lines.append(f"\n🔔 {emoji} {c['name']}({c['code']}) {action}")
            lines.append(f"   买入{c['buy_price']} → 平仓{c['close_price']} {c['pnl_pct']:+.2f}%")
            lines.append(f"   原因: {c['reason']}")

    if reduced:
        for r in reduced:
            lines.append(f"\n⚠️ {r['name']}({r['code']}) 降仓50%")
            lines.append(f"   卖出{r['reduce_qty']}股@{r['close_price']}，剩余{r['remaining_qty']}股")

    if alerts:
        lines.append("\n🚨 预警：")
        for a in alerts:
            lines.append(f"  {a}")

    if not closed and not reduced and not alerts:
        return None  # 无事静默

    return "\n".join(lines)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    date_str = datetime.now().strftime("%Y-%m-%d")
    closed, reduced, alerts = monitor()
    report = format_report(closed, reduced, alerts, date_str)
    if report:
        print(report)
