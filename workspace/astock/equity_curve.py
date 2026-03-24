"""
资金曲线趋势跟踪 + 阶梯式提盈 + 精细化连亏处理 v1

核心逻辑:
1. 资金曲线20日均线趋势跟踪
   - 站上MA → 正常仓位
   - 跌破MA → 仓位上限20%，仅A级+
   - 跌破MA>5% → 停止开仓，诊断

2. 阶梯式提盈+利润垫
   - 月盈≥10% → 提50%盈利为安全垫
   - 月盈≥20% → 提70%盈利为安全垫
   - 安全垫绝对不动，优先亏损交易账户资金

3. 精细化连亏处理
   - 连亏2笔 → 当日停开仓
   - 连亏3笔+跑输基准5% → 停开仓+参数回滚+诊断
   - 连亏5笔 → 全面失效，关闭所有权限
"""

import os
import json
from datetime import datetime, date, timedelta
from pathlib import Path
from statistics import mean

WORKSPACE = Path("/home/gem/workspace/agent/workspace")
DB_PATH = WORKSPACE / "astock" / "position" / "portfolio.db"
STATE_FILE = WORKSPACE / "astock" / "position" / "equity_state.json"

INITIAL_CAPITAL = 1_000_000.0


# ── 20日均线趋势跟踪 ──────────────────────────────────────────────
MA_PERIOD = 20          # 均线周期
DRAWDOWN_STOP = 0.05   # 跌破MA>5%则停止开仓


def load_equity_curve():
    """加载资金曲线（每日权益记录）"""
    state = {}
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            state = json.load(f)
    
    records = state.get("equity_curve", [])
    # 结构: [{"date": "YYYYMMDD", "equity": float, "drawdown": float}, ...]
    return records


def save_equity_curve(records):
    """保存资金曲线"""
    state = {}
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            state = json.load(f)
    state["equity_curve"] = records[-MA_PERIOD*2:]  # 保留足够数据
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False)


def compute_ma(records, period=MA_PERIOD):
    """计算N日均线"""
    if len(records) < period:
        return None
    values = [r["equity"] for r in records[-period:]]
    return mean(values)


def get_equity_status():
    """
    获取资金曲线状态
    
    返回: {
        "trend": "above_ma" / "below_ma" / "below_ma_deep",
        "ma20": float,
        "current_equity": float,
        "position_limit": float,   # 总仓位上限
        "min_tier": "C" / "A",     # 最低可开仓评级
        "stop_trading": bool,       # 是否停止开仓
        "reason": str,
    }
    """
    records = load_equity_curve()
    
    # 如果没有数据，返回默认状态
    if len(records) < 2:
        return {
            "trend": "neutral",
            "ma20": INITIAL_CAPITAL,
            "current_equity": INITIAL_CAPITAL,
            "position_limit": 0.70,
            "min_tier": "C",
            "stop_trading": False,
            "reason": "新账户，无历史数据",
        }
    
    current_equity = records[-1]["equity"]
    ma20 = compute_ma(records, MA_PERIOD)
    
    if ma20 is None:
        return {
            "trend": "neutral",
            "ma20": current_equity,
            "current_equity": current_equity,
            "position_limit": 0.70,
            "min_tier": "C",
            "stop_trading": False,
            "reason": f"数据不足{MA_PERIOD}天",
        }
    
    drawdown_from_ma = (ma20 - current_equity) / ma20
    
    if drawdown_from_ma > DRAWDOWN_STOP:
        return {
            "trend": "below_ma_deep",
            "ma20": ma20,
            "current_equity": current_equity,
            "position_limit": 0.0,
            "min_tier": "S",
            "stop_trading": True,
            "reason": f"跌破MA{MA_PERIOD}达{drawdown_from_ma:.1%}，停止开仓",
        }
    elif current_equity < ma20:
        return {
            "trend": "below_ma",
            "ma20": ma20,
            "current_equity": current_equity,
            "position_limit": 0.20,
            "min_tier": "A",
            "stop_trading": False,
            "reason": f"跌破MA{MA_PERIOD}，仓位限20%，仅A级+",
        }
    else:
        return {
            "trend": "above_ma",
            "ma20": ma20,
            "current_equity": current_equity,
            "position_limit": 0.70,  # 正常阶段上限，实际由情绪决定
            "min_tier": "C",
            "stop_trading": False,
            "reason": f"站上MA{MA_PERIOD}，正常交易",
        }


def record_daily_equity(date_str, equity):
    """每日收盘后记录权益"""
    records = load_equity_curve()
    
    # 更新或追加
    found = False
    for r in records:
        if r["date"] == date_str:
            r["equity"] = equity
            found = True
            break
    if not found:
        records.append({"date": date_str, "equity": equity})
    
    # 计算当日回撤
    if len(records) >= 2:
        peak = max(r["equity"] for r in records)
        drawdown = (peak - records[-1]["equity"]) / peak
        records[-1]["drawdown"] = drawdown
    
    save_equity_curve(records)


# ── 阶梯式提盈+安全垫 ────────────────────────────────────────────
SAFETY_MARGIN_FILE = WORKSPACE / "astock" / "position" / "safety_margin.json"


def load_safety_margin():
    """加载安全垫状态"""
    if SAFETY_MARGIN_FILE.exists():
        with open(SAFETY_MARGIN_FILE) as f:
            return json.load(f)
    return {
        "total_extracted": 0.0,   # 历史累计提取总额
        "balance": 0.0,           # 当前安全垫余额
        "records": [],             # 提取记录
        "equity_floor": 1_000_000 * 0.70,  # 资金地板：总资产的30%永远保留
    }


def save_safety_margin(state):
    with open(SAFETY_MARGIN_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False)


def check_profit_taking(current_equity, month_str):
    """
    检查是否需要提盈
    
    返回: (should_take: bool, amount: float, reason: str)
    """
    state = load_safety_margin()
    
    # 初始资金 - 安全垫余额 = 交易账户资金
    trading_equity = INITIAL_CAPITAL - state["balance"]
    profit = current_equity - INITIAL_CAPITAL + state["total_extracted"]
    profit_rate = profit / INITIAL_CAPITAL
    
    if profit_rate >= 0.20:
        # 提取70%
        extract_rate = 0.70
        threshold = f"月盈{profit_rate:.0%}≥20%"
    elif profit_rate >= 0.10:
        # 提取50%
        extract_rate = 0.50
        threshold = f"月盈{profit_rate:.0%}≥10%"
    else:
        return False, 0.0, f"月盈{profit_rate:.0%}<10%，不提取"
    
    extractable = profit * extract_rate
    # 安全垫不能透支
    max_extract = current_equity * 0.5  # 最多提取账户50%
    extract_amount = min(extractable, max_extract)
    
    if extract_amount < 1000:
        return False, 0.0, f"可提取金额{extract_amount}<1000，不操作"
    
    return True, round(extract_amount, 0), threshold


def execute_profit_taking(amount, month_str):
    """执行提盈"""
    state = load_safety_margin()
    state["total_extracted"] += amount
    state["balance"] += amount
    state["records"].append({
        "date": date.today().strftime("%Y%m%d"),
        "month": month_str,
        "amount": amount,
        "balance": state["balance"],
    })
    save_safety_margin(state)
    return state["balance"]


# ── 精细化连亏处理 ───────────────────────────────────────────────
LOSS_STATE_FILE = WORKSPACE / "astock" / "position" / "loss_state.json"


def load_loss_state():
    if LOSS_STATE_FILE.exists():
        with open(LOSS_STATE_FILE) as f:
            return json.load(f)
    return {
        "consecutive_losses": 0,      # 当前连亏笔数
        "total_losses": 0,            # 累计亏损笔数
        "last_loss_date": None,
        "stopped_reason": None,       # 停止原因
        "recovery_mode": False,        # 是否在恢复模式
    }


def save_loss_state(state):
    with open(LOSS_STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False)


def record_trade_result(is_profit, pnl_pct, date_str):
    """
    记录交易结果，更新连亏状态
    
    is_profit: bool
    pnl_pct: 盈亏百分比（如 -4.5 表示亏损4.5%）
    
    返回: (action: str, reason: str)
        action: "normal" / "stop_today" / "stop_and_diagnose" / "full_shutdown"
    """
    state = load_loss_state()
    
    if is_profit:
        state["consecutive_losses"] = 0
        state["recovery_mode"] = False
        save_loss_state(state)
        return "normal", "盈利，连亏计数归零"
    
    # 亏损（按幅度差异化计数）
    loss_pct = abs(pnl_pct)  # pnl_pct is already a percentage, e.g. -4.5
    if loss_pct <= 0.01:
        inc = 1
    elif loss_pct <= 0.03:
        inc = 2
    else:
        inc = 3
    state["consecutive_losses"] += inc
    state["total_losses"] += 1
    state["last_loss_date"] = date_str
    
    cl = state["consecutive_losses"]
    
    if cl >= 5:
        state["stopped_reason"] = "连亏5笔，策略全面失效"
        state["recovery_mode"] = True
        save_loss_state(state)
        return "full_shutdown", f"连亏{cl}笔，全面失效，关闭所有权限"
    
    if cl == 3:
        # 需要判断是否跑输基准
        state["stopped_reason"] = "连亏3笔+疑似策略失效"
        state["recovery_mode"] = True
        save_loss_state(state)
        return "stop_and_diagnose", f"连亏{cl}笔，疑似失效，停仓+诊断"
    
    if cl == 2:
        state["stopped_reason"] = "连亏2笔"
        save_loss_state(state)
        return "stop_today", f"连亏{cl}笔，今日停开仓"
    
    save_loss_state(state)
    return "normal", f"连亏{cl}笔，继续观察"


def get_loss_status():
    """获取当前连亏状态"""
    state = load_loss_state()
    cl = state["consecutive_losses"]
    
    if cl == 0:
        return "normal", "无连亏，正常交易"
    if cl == 1:
        return "watch", f"连亏1笔，警惕"
    if cl == 2:
        return "stop_today", f"连亏2笔，今日停开仓"
    if cl == 3:
        return "stop_and_diagnose", f"连亏3笔，停仓+诊断"
    return "full_shutdown", f"连亏{cl}笔，全面失效"


# ── 综合风控入口 ─────────────────────────────────────────────────
def pre_trading_check():
    """
    交易前综合检查（每日开盘前调用）
    
    返回: {
        "can_trade": bool,
        "position_limit": float,   # 仓位上限
        "min_tier": str,            # 最低评级
        "stop_reasons": list,        # 停止原因列表
        "warnings": list,            # 警告列表
    }
    """
    equity_status = get_equity_status()
    loss_status, loss_reason = get_loss_status()
    
    stop_reasons = []
    warnings = []
    
    # 资金曲线风控
    if equity_status["stop_trading"]:
        stop_reasons.append(equity_status["reason"])
    
    # 连亏风控
    if loss_status in ("stop_today", "stop_and_diagnose", "full_shutdown"):
        stop_reasons.append(loss_reason)
    
    # 计算最终仓位
    # 取熔断仓位、 equity仓位、连亏仓位的最小值
    base_limit = 0.70
    equity_limit = equity_status["position_limit"]
    loss_limit = 0.0 if loss_status == "full_shutdown" else (
        0.20 if loss_status in ("stop_today", "stop_and_diagnose") else base_limit
    )
    
    final_limit = min(base_limit, equity_limit, loss_limit)
    
    # 最低评级
    if equity_status["trend"] == "below_ma_deep":
        min_tier = "S"
    elif equity_status["trend"] == "below_ma" or loss_status != "normal":
        min_tier = "A"
    else:
        min_tier = "C"
    
    return {
        "can_trade": len(stop_reasons) == 0,
        "position_limit": final_limit,
        "min_tier": min_tier,
        "stop_reasons": stop_reasons,
        "warnings": warnings,
        "equity_status": equity_status,
        "loss_status": loss_status,
    }


def format_equity_report():
    """生成资金曲线+风控综合报告"""
    status = pre_trading_check()
    equity = status["equity_status"]
    loss_s, loss_r = status["loss_status"], ""
    
    state = load_loss_state()
    loss_r = state.get("stopped_reason", "")
    
    records = load_equity_curve()
    current = equity["current_equity"]
    profit = current - INITIAL_CAPITAL
    profit_rate = profit / INITIAL_CAPITAL * 100
    
    lines = [
        f"【📊 资金曲线+风控综合】",
        f"{'='*36}",
        f"当前权益: {current:,.0f}元 ({profit_rate:+.2f}%)",
        f"总盈亏: {profit:+,.0f}元",
        f"",
    ]
    
    if records:
        ma = equity.get("ma20", 0)
        if ma:
            lines.append(f"20日均线: {ma:,.0f}元")
            lines.append(f"趋势: {'🟢站上' if equity['trend']=='above_ma' else '🔴跌破'}MA20")
        if len(records) >= 2:
            dd = records[-1].get("drawdown", 0)
            lines.append(f"当前回撤: {dd:.2%}")
    
    lines.append(f"{'─'*36}")
    lines.append(f"【风控状态】")
    if status["can_trade"]:
        lines.append(f"  ✅ 可交易 | 仓位上限: {status['position_limit']:.0%}")
        lines.append(f"  最低评级: {status['min_tier']}级")
    else:
        lines.append(f"  🔴 停止交易")
        for r in status["stop_reasons"]:
            lines.append(f"    ❌ {r}")
    
    # 连亏状态
    lines.append(f"{'─'*36}")
    lines.append(f"【连亏状态】{loss_s.upper()}")
    lines.append(f"  {loss_r or '正常'}")
    if state["consecutive_losses"] > 0:
        lines.append(f"  连亏笔数: {state['consecutive_losses']}笔")
    
    # 安全垫
    sm = load_safety_margin()
    if sm["balance"] > 0:
        lines.append(f"{'─'*36}")
        lines.append(f"【安全垫】")
        lines.append(f"  余额: {sm['balance']:,.0f}元")
        lines.append(f"  累计提取: {sm['total_extracted']:,.0f}元")
    
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_equity_report())
