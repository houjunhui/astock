"""
狗仔 - 财经媒体爬虫
爬取: 财联社、淘股吧、同花顺、东方财富、雪球
"""
import sys, os, json, time, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)) or '.')

import requests
from datetime import datetime
from bs4 import BeautifulSoup
import sqlite3

# ===================== 配置 =====================
DATA_DIR = '/home/gem/workspace/agent/workspace/gouzai/data'
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'news.db')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/html, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}

TIMEOUT = 15
MAX_RETRIES = 3

# ===================== 数据库 =====================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hash TEXT UNIQUE,
            title TEXT,
            summary TEXT,
            content TEXT,
            source TEXT,
            url TEXT,
            category TEXT,
            tags TEXT,
            stocks TEXT,
            published_at TEXT,
            fetched_at TEXT,
            importance INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS crawl_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            count INTEGER,
            status TEXT,
            error TEXT,
            run_at TEXT
        )
    """)
    conn.commit()
    conn.close()

# ===================== 工具函数 =====================
def save(news_item):
    """去重保存"""
    conn = get_db()
    h = hashlib.md5((news_item['title'] + news_item['source']).encode()).hexdigest()
    news_item['hash'] = h
    try:
        conn.execute("""
            INSERT OR IGNORE INTO news (hash, title, summary, content, source, url, category, tags, stocks, published_at, fetched_at, importance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (h, news_item['title'], news_item.get('summary',''), news_item.get('content',''),
              news_item['source'], news_item.get('url',''), news_item.get('category',''),
              json.dumps(news_item.get('tags',[])), json.dumps(news_item.get('stocks',[])),
              news_item.get('published_at',''), datetime.now().isoformat(), news_item.get('importance',1)))
        conn.commit()
        return True
    except Exception as e:
        print(f"  保存失败: {e}")
        return False
    finally:
        conn.close()

def log_crawl(source, count, status, error=''):
    conn = get_db()
    conn.execute("INSERT INTO crawl_log (source, count, status, error, run_at) VALUES (?,?,?,?,?)",
                 (source, count, status, error, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def fetch(url, headers=None, timeout=TIMEOUT):
    """带重试的HTTP请求"""
    h = {**HEADERS, **(headers or {})}
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers=h, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
            else:
                raise e

# ===================== 各平台爬虫 =====================

def crawl_cailian():
    """财联社 - 权威A股资讯"""
    items = []
    try:
        # 财联社RSS
        r = fetch('https://www.cls.cn/api/sw?app=CLS&os=web&sv=7.8.3&width=750&row=20&start=0&type=1,2,3&app_type=day')
        data = r.json()
        for art in data.get('data', {}).get('roll_data', []):
            items.append({
                'title': art.get('title',''),
                'summary': art.get('summary',''),
                'source': '财联社',
                'url': f"https://www.cls.cn/telegraph/{art.get('id','')}",
                'category': art.get('type_name',''),
                'published_at': datetime.fromtimestamp(art.get('ctime',0)).strftime('%Y-%m-%d %H:%M:%S') if art.get('ctime') else '',
                'importance': 2 if '利好' in str(art.get('title','')) or '利空' in str(art.get('title','')) else 1,
            })
    except Exception as e:
        log_crawl('财联社', 0, 'error', str(e))
        print(f"  财联社失败: {e}")
    return items

def crawl_eastmoney():
    """东方财富 - A股资讯"""
    items = []
    try:
        # 东财实时资讯
        r = fetch('https://np-anotice-stock.eastmoney.com/api/security/ann?cb=&sr=-1&page_size=20&page_index=1&ann_type=SHA,CYB,SZA,SHA_GP,SZA_GP&client_source=web&stock=&_=1')
        data = r.json()
        for art in data.get('data', {}).get('list', []):
            items.append({
                'title': art.get('title',''),
                'summary': art.get('summary',''),
                'source': '东方财富',
                'url': f"https://data.eastmoney.com/notices/noticesdetail/{art.get('id','')}.html",
                'category': '公告',
                'published_at': art.get('publish_time',''),
                'importance': 2,
            })
    except Exception as e:
        print(f"  东方财富失败: {e}")
    return items

def crawl_ths():
    """同花顺 - 资讯"""
    items = []
    try:
        r = fetch('https://news.10jqka.com.cn/tapp/news/push/stock/?page=1&tag=&track=website&pagesize=20')
        data = r.json()
        for art in data.get('data', {}).get('list', []):
            items.append({
                'title': art.get('title',''),
                'summary': art.get('summary','') or art.get('desc',''),
                'source': '同花顺',
                'url': art.get('url','') or f"https://news.10jqka.com.cn/tapp/news/push/stock/?id={art.get('id','')}",
                'category': art.get('tag',''),
                'published_at': art.get('ctime',''),
                'importance': 1,
            })
    except Exception as e:
        print(f"  同花顺失败: {e}")
    return items

def crawl_xueqiu():
    """雪球 - 社区讨论"""
    items = []
    try:
        r = fetch('https://xueqiu.com/v4/statuses/public_timeline_by_category.json?since_id=-1&max_id=-1&count=20&category=104')
        data = r.json()
        for art in data.get('list', []):
            user = art.get('user', {})
            items.append({
                'title': art.get('title','') or art.get('text','')[:50],
                'summary': BeautifulSoup(art.get('text',''), 'html.parser').get_text()[:200],
                'source': '雪球',
                'url': f"https://xueqiu.com/{art.get('user_id','')}/{art.get('id','')}",
                'category': '社区',
                'published_at': datetime.fromtimestamp(art.get('created_at',0)/1000).strftime('%Y-%m-%d %H:%M:%S') if art.get('created_at') else '',
                'importance': 1,
            })
    except Exception as e:
        print(f"  雪球失败: {e}")
    return items

def crawl_taoguba():
    """淘股吧 - 论坛"""
    items = []
    try:
        r = fetch('https://www.taoguba.com.cn/api/article/list?pageIndex=1&pageSize=20&blockId=0&order=1')
        data = r.json()
        for art in data.get('result', {}).get('dataList', []):
            items.append({
                'title': art.get('title',''),
                'summary': art.get('summary','') or art.get('content','')[:200],
                'source': '淘股吧',
                'url': f"https://www.taoguba.com.cn/p/{art.get('articleId','')}",
                'category': '论坛',
                'published_at': art.get('createTime',''),
                'importance': 1,
            })
    except Exception as e:
        print(f"  淘股吧失败: {e}")
    return items

# ===================== 主流程 =====================
def run_all():
    init_db()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 狗仔开始巡逻...")
    
    sources = [
        ('财联社', crawl_cailian),
        ('东方财富', crawl_eastmoney),
        ('同花顺', crawl_ths),
        ('雪球', crawl_xueqiu),
        ('淘股吧', crawl_taoguba),
    ]
    
    total = 0
    for name, crawler in sources:
        try:
            items = crawler()
            saved = sum(save(item) for item in items)
            total += saved
            log_crawl(name, saved, 'ok')
            print(f"  {name}: 抓取{len(items)}条, 新增{saved}条")
            time.sleep(1)
        except Exception as e:
            log_crawl(name, 0, 'error', str(e))
            print(f"  {name}: 失败 {e}")
    
    print(f"狗仔巡逻完毕: 共{total}条新资讯")
    return total

def get_recent(hours=2, limit=30):
    """获取最近N小时的资讯"""
    conn = get_db()
    from datetime import timedelta
    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT * FROM news WHERE fetched_at > ? ORDER BY published_at DESC LIMIT ?",
        (since, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_hot_stocks(hours=2):
    """从资讯中提取热门股票"""
    conn = get_db()
    from datetime import timedelta
    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT stocks FROM news WHERE fetched_at > ? AND stocks IS NOT NULL AND stocks != '[]'",
        (since,)
    ).fetchall()
    conn.close()
    
    stock_count = {}
    for r in rows:
        try:
            stocks = json.loads(r['stocks'])
            for s in stocks:
                stock_count[s] = stock_count.get(s, 0) + 1
        except:
            pass
    return sorted(stock_count.items(), key=lambda x: -x[1])[:10]


if __name__ == '__main__':
    import fire
    fire.Fire({
        'run': run_all,
        'recent': lambda hours=2, limit=30: get_recent(hours, limit),
        'hot': lambda hours=2: get_hot_stocks(hours),
    })
