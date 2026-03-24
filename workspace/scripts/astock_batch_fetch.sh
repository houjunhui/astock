#!/bin/bash
# A股竞价复盘 - 批量获取涨停股实时竞价数据
# 调用方式: bash astock_batch_fetch.sh [日期YYYYMMDD]

DATE=${1:-$(date +%Y%m%d)}
OUTFILE="/home/gem/workspace/agent/workspace/data/astock/zt_${DATE}.txt"

echo "抓取 $DATE 涨停股竞价数据..."

# 获取涨停股列表
ZT_CODES=$(curl -s "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page=1&num=200&sort=changepercent&asc=0&node=hs_a&symbol=&_s_r_a=page" \
  -H "Referer: https://finance.sina.com.cn" 2>/dev/null | python3 -c "
import sys,json
data=sys.stdin.read()
rows=json.loads(data)
codes=[]
for r in rows:
    if float(r.get('changepercent',0))>=9.9:
        codes.append(r['code'])
print(','.join(codes))
" 2>/dev/null)

if [ -z "$ZT_CODES" ]; then
    echo "获取涨停股列表失败"
    exit 1
fi

echo "涨停股数量: $(echo $ZT_CODES | tr ',' '\n' | wc -l)"

# 分批查询竞价数据（每批20只）
BATCH_URLS=""
COUNT=0
for CODE in $(echo $ZT_CARDS | tr ',' '\n'); do
    # 判断交易所前缀
    if [[ "$CODE" =~ ^(6|5|9|8|7) ]]; then
        if [[ "$CODE" =~ ^(9|8) ]]; then
            PREFIX="bj"
        else
            PREFIX="sh"
        fi
    else
        PREFIX="sz"
    fi
    BATCH_URLS="${BATCH_URLS},${PREFIX}${CODE}"
    COUNT=$((COUNT+1))
    
    if [ $COUNT -eq 20 ]; then
        # 处理这一批
        BATCH_URLS=${BATCH_URLS:1}
        curl -s "https://qt.gtimg.cn/q=${BATCH_URLS}" -H "Referer: https://finance.qq.com" 2>/dev/null | iconv -f gbk -t utf8 2>/dev/null | python3 -c "
import sys
codes_set = set()
lines = sys.stdin.readlines()
for line in lines:
    if 'v_p_' not in line:
        continue
    parts = line.split('~')
    if len(parts) > 40:
        raw = parts[2]
        code = raw.replace('sz','').replace('sh','').replace('bj','')
        name = parts[1]
        yesterday_close = float(parts[4]) if parts[4] else 0
        today_open = float(parts[5]) if parts[5] else 0
        today_high = float(parts[33]) if parts[33] else 0
        today_close = float(parts[3]) if parts[3] else 0
        auction_pct = (today_open/yesterday_close-1)*100 if yesterday_close>0 else 0
        high_pct = (today_high/yesterday_close-1)*100 if yesterday_close>0 else 0
        close_pct = (today_close/yesterday_close-1)*100 if yesterday_close>0 else 0
        print(f'{code}|{name}|{yesterday_close:.2f}|{today_open:.2f}|{today_high:.2f}|{today_close:.2f}|{auction_pct:+.2f}|{high_pct:+.2f}|{close_pct:+.2f}')
        codes_set.add(code)
" >> "${OUTFILE}.tmp"
        BATCH_URLS=""
        COUNT=0
    fi
done

# 处理最后一批
if [ $COUNT -gt 0 ]; then
    BATCH_URLS=${BATCH_URLS:1}
    curl -s "https://qt.gtimg.cn/q=${BATCH_URLS}" -H "Referer: https://finance.qq.com" 2>/dev/null | iconv -f gbk -t utf8 2>/dev/null | python3 -c "
import sys
for line in sys.stdin:
    if 'v_p_' not in line:
        continue
    parts = line.split('~')
    if len(parts) > 40:
        code = parts[2].replace('sz','').replace('sh','').replace('bj','')
        name = parts[1]
        yesterday_close = float(parts[4]) if parts[4] else 0
        today_open = float(parts[5]) if parts[5] else 0
        today_high = float(parts[33]) if parts[33] else 0
        today_close = float(parts[3]) if parts[3] else 0
        auction_pct = (today_open/yesterday_close-1)*100 if yesterday_close>0 else 0
        high_pct = (today_high/yesterday_close-1)*100 if yesterday_close>0 else 0
        close_pct = (today_close/yesterday_close-1)*100 if yesterday_close>0 else 0
        print(f'{code}|{name}|{yesterday_close:.2f}|{today_open:.2f}|{today_high:.2f}|{today_close:.2f}|{auction_pct:+.2f}|{high_pct:+.2f}|{close_pct:+.2f}')
" >> "${OUTFILE}.tmp"
fi

# 合并输出
{
    echo "# Date: $DATE"
    echo "# Generated: $(date '+%Y-%m-%d %H:%M:%S')"
    if [ -f "${OUTFILE}.tmp" ]; then
        echo "# Count: $(wc -l < '${OUTFILE}.tmp')"
        cat "${OUTFILE}.tmp"
        rm "${OUTFILE}.tmp"
    fi
} > "$OUTFILE"

echo "输出: $OUTFILE"
echo "完成: $(wc -l < "$OUTFILE") 行"
