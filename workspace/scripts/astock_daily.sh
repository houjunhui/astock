#!/bin/bash
# A股每日复盘 - 数据收集脚本
# 每天 22:00 运行，收集当日涨停股及竞价数据

DATA_DIR="/home/gem/workspace/agent/workspace/data/astock"
STRATEGY_DIR="/home/gem/workspace/agent/workspace/astock_strategy"
LOG_FILE="$DATA_DIR/logs/collector_$(date +%Y%m%d).log"

mkdir -p "$DATA_DIR/logs" "$STRATEGY_DIR"

TODAY=$(date +%Y%m%d)
YESTERDAY=$(date -d '1 day ago' +%Y%m%d)
TRADING_DATE=${1:-$TODAY}

echo "[$(date)] 开始采集数据: $TRADING_DATE" >> "$LOG_FILE"

# 1. 获取当日指数
echo "=== 指数数据 ===" >> "$LOG_FILE"
curl -s "https://qt.gtimg.cn/q=s_sh000001,s_sz399001,s_sz399006,s_sh000016,s_sh000300" \
  -H "Referer: https://finance.qq.com" | iconv -f gbk -t utf8 >> "$LOG_FILE"

# 2. 获取当日涨停股（涨幅>=9.9%）
echo "=== 涨停股列表 ===" >> "$LOG_FILE"
curl -s "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page=1&num=200&sort=changepercent&asc=0&node=hs_a&symbol=&_s_r_a=page" \
  -H "Referer: https://finance.sina.com.cn" 2>/dev/null | python3 -c "
import sys,json,datetime

data=sys.stdin.read()
try:
    rows=json.loads(data)
    zt=[r for r in rows if float(r.get('changepercent',0))>=9.9]
    today=datetime.datetime.now().strftime('%Y%m%d')
    print(f'# Date: {today}', flush=True)
    print(f'# Count: {len(zt)}', flush=True)
    for r in zt:
        pct=float(r['changepercent'])
        amount=float(r.get('amount',0))
        vol=float(r.get('volume',0))
        code=r['code']
        name=r['name']
        open_price=float(r.get('open',0))
        yesterday_close=float(r.get('settlement',0))
        auction_pct = (open_price/yesterday_close-1)*100 if yesterday_close>0 else 0
        print(f'{code}|{name}|{pct:.2f}|{amount/1e8:.1f}|{vol/1e4:.0f}|{auction_pct:.2f}', flush=True)
except Exception as e:
    print(f'ERROR: {e}', flush=True)
" >> "$DATA_DIR/zt_$TRADING_DATE.txt" 2>&1

echo "[$(date)] 数据采集完成" >> "$LOG_FILE"
echo "---END---" >> "$LOG_FILE"
