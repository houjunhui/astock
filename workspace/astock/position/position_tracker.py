"""
持仓跟踪系统 v1.0
- 持仓记录（CSV）
- 实时价格监控
- 止损/目标监控
- 每日盈亏统计
"""

import csv
import os
from datetime import datetime, date
from pathlib import Path
from astock.quicktiny import get_minute

TRACKER_DIR = Path("/home/gem/workspace/agent/workspace/astock/position")
PORTFOLIO_FILE = TRACKER_DIR / "portfolio.csv"
DAILY_PNL_FILE = TRACKER_DIR / "daily_pnl.csv"
CAPITAL_FILE = TRACKER_DIR / "capital.csv"

FIELDS = ["date", "code", "name", "buy_date", "buy_price", "qty", "capital_pct",
          "stop_loss", "target_price", "buy_method", "current_price", "pnl_pct", "pnl_amt", "status", "notes"]


def init_files():
    """初始化文件"""
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    if not PORTFOLIO_FILE.exists():
        with open(PORTFOLIO_FILE, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=FIELDS).writeheader()
    if not CAPITAL_FILE.exists():
        with open(CAPITAL_FILE, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=["date", "total_capital", "cash", "position_value", "updated"]).writeheader()


def load_portfolio():
    """加载当前持仓"""
    init_files()
    positions = []
    if PORTFOLIO_FILE.exists():
        with open(PORTFOLIO_FILE, "r") as f:
            for row in csv.DictReader(f):
                if row["status"] in ("持仓", "持仓中"):
                    positions.append(row)
    return positions


def get_current_price(code):
    """获取最新收盘价（当日分时最后一笔的收盘价）"""
    try:
        minutes = get_minute(code, ndays=1)
        if minutes:
            return round(minutes[-1][4], 2)  # close price (index 4)
    except:
        pass
    return None


def get_intraday_low(code):
    """获取当日最低价"""
    try:
        minutes = get_minute(code, ndays=1)
        if minutes:
            return round(min(minute[3] for minute in minutes), 2)  # low (index 3)
    except:
        pass
    return None


def get_intraday_high(code):
    """获取当日最高价"""
    try:
        minutes = get_minute(code, ndays=1)
        if minutes:
            return round(max(minute[2] for minute in minutes), 2)  # high (index 2)
    except:
        pass
    return None


def update_positions():
    """更新所有持仓最新价格"""
    positions = load_portfolio()
    results = []
    for pos in positions:
        code = pos["code"]
        buy_price = float(pos["buy_price"])
        current = get_current_price(code)
        stop_loss = float(pos["stop_loss"])
        target = float(pos["target_price"])
        
        if current:
            pnl_pct = (current - buy_price) / buy_price * 100
        else:
            pnl_pct = 0.0
        
        # 状态判断
        if current and current <= stop_loss:
            status = "⚠️触及止损"
        elif current and current >= target:
            status = "🎯触及目标"
        elif current and pnl_pct <= -3:
            status = "⚠️亏损超3%"
        elif current and pnl_pct >= 5:
            status = "✅浮盈5%+"
        else:
            status = pos["status"]
        
        results.append({
            **pos,
            "current_price": current,
            "pnl_pct": round(pnl_pct, 2),
            "status": status,
        })
    return results


def add_position(code, name, buy_price, qty, capital_pct, stop_loss, target_price, buy_method="", notes=""):
    """记录新开仓"""
    init_files()
    today = date.today().strftime("%Y-%m-%d")
    row = {
        "date": today,
        "code": code,
        "name": name,
        "buy_date": today,
        "buy_price": buy_price,
        "qty": qty,
        "capital_pct": capital_pct,
        "stop_loss": stop_loss,
        "target_price": target_price,
        "buy_method": buy_method,
        "current_price": buy_price,
        "pnl_pct": 0.0,
        "status": "持仓",
        "notes": notes,
    }
    with open(PORTFOLIO_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writerow(row)
    return row


def close_position(code, close_price, reason=""):
    """平仓记录"""
    if not PORTFOLIO_FILE.exists():
        return False
    
    all_rows = []
    with open(PORTFOLIO_FILE, "r") as f:
        all_rows = list(csv.DictReader(f))
    
    updated = False
    for row in all_rows:
        if row["code"] == code and row["status"] in ("持仓", "持仓中"):
            buy_price = float(row["buy_price"])
            pnl_pct = (float(close_price) - buy_price) / buy_price * 100
            row["current_price"] = close_price
            row["pnl_pct"] = round(pnl_pct, 2)
            row["status"] = f"已平仓:{reason}"
            pnl_amt = (float(close_price) - float(row["buy_price"])) * int(row["qty"])
            row["pnl_amt"] = str(round(pnl_amt, 2))
            row["notes"] = f"{row['notes']} | 平仓:{reason}" if row["notes"] else f"平仓:{reason}"
            updated = True
    
    if updated:
        with open(PORTFOLIO_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerows(all_rows)
    
    return updated


def get_portfolio_status(positions=None):
    """组合整体状态（可用update_positions()的返回值避免重复读取）"""
    positions = positions or load_portfolio()
    if not positions:
        return {"total": 0, "winning": 0, "losing": 0, "warning": 0, "total_pnl": 0}
    
    winning = sum(1 for p in positions if float(p.get("pnl_pct", 0)) > 0)
    losing = sum(1 for p in positions if float(p.get("pnl_pct", 0)) < 0)
    warning = sum(1 for p in positions if "⚠️" in p.get("status", ""))
    total_pnl = sum(float(p.get("pnl_pct", 0)) for p in positions)
    
    return {"total": len(positions), "winning": winning, "losing": losing,
            "warning": warning, "total_pnl": round(total_pnl, 2)}


def format_position_report(positions):
    """格式化持仓报告"""
    if not positions:
        return "📊 持仓：无\n空空如也，观望为主。"
    
    lines = ["📊 持仓监控\n" + "─" * 30]
    for p in positions:
        code = p["code"]
        name = p["name"]
        cur = p.get("current_price", "-")
        buy = p["buy_price"]
        pnl = p.get("pnl_pct", 0)
        stop = p["stop_loss"]
        target = p["target_price"]
        status = p["status"]
        method = p.get("buy_method", "")
        
        cur_str = f"{cur}元" if cur != "-" else "获取中..."
        pnl_str = f"{pnl:+.2f}%"
        
        lines.append(f"\n{name}({code})")
        lines.append(f"  买入: {buy}元 | 现价: {cur_str} | {pnl_str}")
        lines.append(f"  止损: {stop}元 | 目标: {target}元")
        if method:
            lines.append(f"  方式: {method}")
        lines.append(f"  状态: {status}")
    
    return "\n".join(lines)
