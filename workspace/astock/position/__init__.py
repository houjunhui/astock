"""
astock.position - 持仓管理模块（SQLite版）
自动初始化SQLite，保留CSV迁移接口
"""
from astock.position.position_sqlite import (
    init_files,
    init_db,
    add_position,
    close_position,
    load_portfolio,
    get_today_trades,
    load_all_trades,
    get_daily_pnl,
    migrate_csv_to_sqlite,
)
from astock.position.position_sizer import (
    calc_position,
    calc_stop_loss,
    calc_target,
)
from astock.position.sector_tracker import (
    get_sector_momentum,
    compare_sector_momentum,
    check_position_sector风险,
    format_sector_report,
)
from astock.position.daily_report import generate_full_report
from astock.position.query import statistics, format_statistics, load_all_trades as _load_all

# 旧CSV追踪器（仅保留get_intraday_*辅助函数，不做持久化）
from astock.position.position_tracker import (
    get_current_price,
    get_intraday_low,
    get_intraday_high,
    get_minute,
    update_positions,
    get_portfolio_status,
    format_position_report,
)

__all__ = [
    "init_files", "init_db", "add_position", "close_position",
    "load_portfolio", "get_today_trades", "load_all_trades",
    "get_daily_pnl", "migrate_csv_to_sqlite",
    "calc_position", "calc_stop_loss", "calc_target",     "get_sector_momentum", "compare_sector_momentum",
    "check_position_sector风险", "format_sector_report",
    "generate_full_report", "statistics", "format_statistics",
    "get_current_price", "get_intraday_low", "get_intraday_high",
    "get_minute", "update_positions",
    "get_portfolio_status", "format_position_report",
]

# 启动时自动初始化数据库
init_db()
