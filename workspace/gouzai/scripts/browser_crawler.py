"""
狗仔 - 浏览器爬虫（处理反爬网站）
使用 playwright 渲染页面后提取数据
"""
import sys, os, json, time, re
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)) or '.')

from datetime import datetime
from crawler import save, log_crawl, get_db

# ===================== Playwright 爬虫 =====================

def crawl_cailian():
    """财联社 - 用浏览器渲染"""
    items = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
            )
            page = browser.new_page()
            page.goto('https://www.cls.cn/telegraph', wait_until='networkidle', timeout=20000)
            page.wait_for_timeout(5000)
            
            # 用JavaScript提取文章数据
            articles = page.evaluate('''
                () => {
                    const results = [];
                    const bodyHTML = document.body.innerHTML;
                    // 按时间戳+内容提取
                    const regex = /(\\d{2}:\\d{2}:\\d{2})\\s*([\\s\\S]*?)(?=\\d{2}:\\d{2}:\\d{2}|$)/g;
                    let match;
                    while ((match = regex.exec(bodyHTML)) !== null && results.length < 20) {
                        const time = match[1];
                        let content = match[2].replace(/<[^>]+>/g, '').trim();
                        // 清理多余空白
                        content = content.replace(/\\s+/g, ' ');
                        if (content.length > 30 && (content.includes('财联社') || content.includes('【'))) {
                            // 提取标题（如果有【】）
                            let title = '';
                            let body = content;
                            const titleMatch = content.match(/【([^】]+)】/);
                            if (titleMatch) {
                                title = titleMatch[1];
                                body = content.replace(/【[^】]+】/, '').trim();
                            }
                            // 提取链接
                            const linkRegex = /href=\"([^\"]+)\"/g;
                            const links = [];
                            let linkMatch;
                            while ((linkMatch = linkRegex.exec(match[2])) !== null) {
                                if (!linkMatch[1].includes('beian') && !linkMatch[1].includes('gov.cn')) {
                                    links.push(linkMatch[1]);
                                }
                            }
                            results.push({
                                time: time,
                                title: title || body.substring(0, 60),
                                body: body.substring(0, 200),
                                link: links[0] || ''
                            });
                        }
                    }
                    return results.slice(0, 20);
                }
            ''')
            
            for a in articles:
                url = a['link']
                if url and not url.startswith('http'):
                    url = 'https://www.cls.cn' + url
                items.append({
                    'title': a['title'],
                    'summary': a['body'][:200],
                    'source': '财联社',
                    'url': url,
                    'category': '资讯',
                    'published_at': a['time'],
                    'importance': 2 if any(k in a['title'] + a['body'] for k in ['利好', '利空', '监管', '突发', 'A股', '涨停', '跌停', '特停']) else 1,
                })
            
            browser.close()
    except Exception as e:
        print(f"  财联社失败: {e}")
    return items


def crawl_taoguba():
    """淘股吧 - 用浏览器"""
    items = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
            )
            page = browser.new_page()
            page.goto('https://www.taoguba.com.cn/', wait_until='networkidle', timeout=20000)
            page.wait_for_timeout(3000)
            
            # 用inner_text分割提取
            text = page.inner_text('body')
            lines = text.split('\n')
            
            current = {}
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                # 检测淘股论坛 · X阅读 · X时间前
                if '·' in line and ('阅读' in line or '评论' in line):
                    parts = [pt.strip() for pt in line.split('·')]
                    if len(parts) >= 3:
                        reads = parts[1]
                        time_ago = parts[2]
                        if current and current.get('title'):
                            current['reads'] = reads
                            current['time'] = time_ago
                            items.append(current)
                            current = {}
                elif line and len(line) > 10 and len(line) < 120:
                    # 排除导航项
                    skip_words = ['首页', '淘股论坛', '投资策略', '直播', '登录', '注册', '研股', '网友精选', '今日推荐', '淘县院子', '极速快讯', '实盘比赛', '我的关注', '综合推荐', '下载App']
                    if not any(sw in line for sw in skip_words):
                        if not current:
                            current = {'title': line}
            
            browser.close()
            
            # 转换为标准格式
            formatted = []
            for a in items:
                formatted.append({
                    'title': a.get('title', '')[:100],
                    'summary': f"阅读:{a.get('reads', '')}",
                    'source': '淘股吧',
                    'url': '',
                    'category': '论坛',
                    'published_at': a.get('time', ''),
                    'importance': 1,
                })
            return formatted
    except Exception as e:
        print(f"  淘股吧失败: {e}")
    return []


def crawl_xueqiu_fallback():
    """雪球 - 尝试API方式（备用）"""
    items = []
    try:
        import requests
        session = requests.Session()
        # 先获取首页拿cookie
        resp = session.get('https://xueqiu.com/', timeout=10)
        cookies = resp.cookies.get_dict()
        
        # 用cookie访问API
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        resp = session.get(
            'https://xueqiu.com/v4/statuses/public_timeline_by_category.json?since_id=-1&max_id=-1&count=10&category=104',
            headers=headers, cookies=cookies, timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get('list', [])[:10]:
                user = item.get('user', {})
                text = item.get('text', '')
                items.append({
                    'title': text[:60] if len(text) > 60 else text,
                    'summary': f"@{user.get('screen_name', '未知')}",
                    'source': '雪球',
                    'url': f"https://xueqiu.com/{user.get('id','')}/{item.get('id','')}",
                    'category': '社区',
                    'published_at': datetime.fromtimestamp(item.get('created_at', 0)/1000).strftime('%H:%M') if item.get('created_at') else '',
                    'importance': 1,
                })
    except Exception as e:
        print(f"  雪球(API备用)失败: {e}")
    return items


def run_browser_crawl():
    """主函数"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 狗仔浏览器巡逻...")
    
    all_new = 0
    
    # 财联社
    try:
        items = crawl_cailian()
        saved = sum(save(item) for item in items)
        print(f"  财联社: {len(items)}条, 新增{saved}条")
        log_crawl('财联社(浏览器)', saved, 'ok')
        all_new += saved
    except Exception as e:
        print(f"  财联社: 失败 {e}")
        log_crawl('财联社(浏览器)', 0, 'error', str(e))
    time.sleep(2)
    
    # 淘股吧
    try:
        items = crawl_taoguba()
        saved = sum(save(item) for item in items)
        print(f"  淘股吧: {len(items)}条, 新增{saved}条")
        log_crawl('淘股吧(浏览器)', saved, 'ok')
        all_new += saved
    except Exception as e:
        print(f"  淘股吧: 失败 {e}")
        log_crawl('淘股吧(浏览器)', 0, 'error', str(e))
    time.sleep(2)
    
    # 雪球 - 备用API方式
    try:
        items = crawl_xueqiu_fallback()
        saved = sum(save(item) for item in items)
        print(f"  雪球: {len(items)}条, 新增{saved}条")
        log_crawl('雪球(API)', saved, 'ok')
        all_new += saved
    except Exception as e:
        print(f"  雪球: 失败 {e}")
        log_crawl('雪球(API)', 0, 'error', str(e))
    
    print(f"  共新增 {all_new} 条资讯")
    return all_new


if __name__ == '__main__':
    run_browser_crawl()
