"""
IC验证框架 - 因子有效性量化验证
IC = Information Coefficient，衡量因子与收益的相关性
IC>0.02 认为因子有效，IC<0 认为因子应剔除
"""
import sys, os, json
sys.path.insert(0, '/home/gem/workspace/agent/workspace')
from pathlib import Path
import numpy as np

WORKSPACE = Path("/home/gem/workspace/agent/workspace")
IC_REPORT_FILE = WORKSPACE / "astock/pools/ic_report.json"

# ── 因子定义 ──────────────────────────────────────────────
FACTORS = {
    "vr": {"name": "量比", "weight": 0.20},
    "seal_rate": {"name": "封成率", "weight": 0.25},
    "auction_chg": {"name": "竞价涨幅", "weight": 0.15},
    "turnover": {"name": "换手率", "weight": 0.15},
    "continue_num": {"name": "连板数", "weight": 0.15},
    "industry_strength": {"name": "板块强度", "weight": 0.10},
}

def calc_ic(factor_values, returns):
    """计算IC值：因子值与收益的相关系数"""
    if len(factor_values) != len(returns) or len(factor_values) < 10:
        return 0.0
    try:
        return np.corrcoef(factor_values, returns)[0, 1]
    except:
        return 0.0

def verify_factors():
    """从 execution_trace.db 读取历史交易，验证各因子有效性"""
    # 读取 sqlite
    import sqlite3
    db_path = WORKSPACE / "astock/position/execution_trace.db"
    if not db_path.exists():
        return {}
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 读取历史交易记录
    cursor.execute("""
        SELECT code, name, action, pnl_pct, phase, tier, market_temp
        FROM execution_log
        WHERE action IN ('买入', 'BUY', 'buy')
        ORDER BY date DESC
        LIMIT 500
    """)
    
    records = cursor.fetchall()
    conn.close()
    
    if len(records) < 20:
        return {}
    
    # 解析字段（execution_log表字段：code,name,action,pnl_pct,phase,tier,market_temp）
    # phase tier 可做IC分析
    phase_map = {"主升": 5, "发酵": 4, "分歧": 3, "退潮": 2, "冰点": 1, "": 0}
    tier_map = {"S": 4, "A": 3, "B": 2, "C": 1, "": 0}
    
    phase_vals, tier_vals, returns = [], [], []
    
    for r in records:
        pnl_pct = float(r[3]) if r[3] else 0
        returns.append(pnl_pct)
        phase_vals.append(phase_map.get(str(r[4]), 0))
        tier_vals.append(tier_map.get(str(r[5]), 0))
    
    # 计算IC（只有phase和tier可用）
    ic_results = {
        "phase": {"name": "情绪相位", "ic": round(calc_ic(phase_vals, returns), 4)},
        "tier": {"name": "评级档次", "ic": round(calc_ic(tier_vals, returns), 4)},
    }
    
    # 计算各因子IC
    ic_results = {}
    for k, name in [(k, v["name"]) for k, v in FACTORS.items()]:
        ic = calc_ic(factor_data[k], returns)
        ic_results[k] = {"name": name, "ic": round(ic, 4)}
    
    return ic_results

def get_effective_factors(min_ic=0.02):
    """获取有效因子（IC >= min_ic）"""
    results = verify_factors()
    return {k: v for k, v in results.items() if v["ic"] >= min_ic}

def apply_ic_weights():
    """根据IC调整因子权重"""
    effective = get_effective_factors()
    if not effective:
        return {}
    
    # 归一化权重
    total_ic = sum(abs(v["ic"]) for v in effective.values())
    adjusted = {}
    for k, v in effective.items():
        adjusted[k] = {
            **v,
            "adjusted_weight": round(abs(v["ic"]) / total_ic, 3)
        }
    return adjusted

# CLI
if __name__ == "__main__":
    import pprint
    results = verify_factors()
    print("=== 因子IC验证 ===")
    for k, v in sorted(results.items(), key=lambda x: -abs(x[1]["ic"])):
        status = "✅" if v["ic"] >= 0.02 else "❌"
        print(f"  {status} {v['name']}: IC={v['ic']:+.4f}")
    
    effective = get_effective_factors()
    print(f"\n有效因子: {list(effective.keys())}")
