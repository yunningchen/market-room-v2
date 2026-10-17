"""Market Room public news + official Taiwan IR events; no paid API keys."""
import json, re, urllib.request, urllib.parse, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Taipei')
NOW = datetime.now(TZ)
OUT = Path('docs/data/news.json')
HEADERS = {'User-Agent':'MarketRoomNews/1.0 (personal research dashboard; public RSS reader)', 'Accept-Language':'zh-TW,zh;q=0.9,en;q=0.8'}
QUERIES = [
 ('台股','台股 產業 半導體 金融 重大消息 when:7d','zh-TW','TW'),
 ('美股','美股 華爾街 聯準會 科技股 AI 財報 when:7d','zh-TW','TW'),
 ('財報新聞','台股 美股 財報 法說會 earnings results when:7d','zh-TW','TW'),
]

def fetch(url, timeout=15):
    req=urllib.request.Request(url,headers=HEADERS)
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return r.read(1200000)

def rss_items():
    result=[]; errors=[]
    for market,q,lang,region in QUERIES:
        url='https://news.google.com/rss/search?'+urllib.parse.urlencode({'q':q,'hl':lang,'gl':region,'ceid':f'{region}:{lang}'})
        try:
            root=ET.fromstring(fetch(url))
            for item in root.findall('./channel/item')[:35]:
                title=(item.findtext('title') or '').strip()
                link=(item.findtext('link') or '').strip()
                pub=(item.findtext('pubDate') or '').strip()
                if not title or not link: continue
                # Chinese display only; do not falsely label an English title as translated.
                if market == '美股' and not re.search(r'[\u4e00-\u9fff]', title): continue
                try: dt=parsedate_to_datetime(pub).astimezone(TZ)
                except Exception: continue
                if not NOW-timedelta(days=7) <= dt <= NOW+timedelta(hours=1): continue
                source=item.find('source'); publisher=source.text.strip() if source is not None and source.text else ''
                result.append({'market':market,'title':title[:200], 'url':link,'published':dt.isoformat(timespec='minutes'),'publisher':publisher})
        except Exception as e: errors.append(f'{market}: {type(e).__name__}')
    seen=set(); unique=[]
    for it in sorted(result,key=lambda x:x['published'],reverse=True):
        key=re.sub(r'\W+','',it['title'].split(' - ')[0].lower())[:90]
        if key in seen:continue
        seen.add(key);unique.append(it)
    return unique[:65],errors

def ir_events():
    """Parse public TDCC event listing, only accept explicit stock + datetime pairs."""
    events=[]; errors=[]
    try:
        from bs4 import BeautifulSoup
        for offset in (0,20,40,60):
            url=f'https://irplatform.tdcc.com.tw/ir/event/list?lang=zh&max=20&offset={offset}'
            try: soup=BeautifulSoup(fetch(url),'html.parser')
            except Exception as e:
                errors.append(f'TDCC page {offset}: {type(e).__name__}');continue
            # Each listing item is expected to contain a code/name and date in the same compact container.
            for tag in soup.find_all(['article','li','tr','div']):
                txt=' '.join(tag.stripped_strings)
                if len(txt)>250 or len(txt)<14 or '法說' not in txt:continue
                match=re.search(r'(?<!\d)(\d{4})\s+([^\s]{1,18})',txt)
                date=re.search(r'(20\d{2})[/-](\d{1,2})[/-](\d{1,2})\s+(\d{1,2}):(\d{2})',txt)
                if not match or not date:continue
                try: dt=datetime(*map(int,date.groups()),tzinfo=TZ)
                except ValueError:continue
                if NOW-timedelta(days=1) <= dt <= NOW+timedelta(days=14):
                    events.append({'symbol':match.group(1),'company':match.group(2),'type':'台股法說會','datetime':dt.isoformat(timespec='minutes'),'url':url,'source':'TDCC 公司投資人關係整合平台'})
    except ImportError: errors.append('beautifulsoup4 missing')
    unique={ (e['symbol'],e['datetime']):e for e in events }
    return sorted(unique.values(),key=lambda x:x['datetime'])[:70],errors

def main():
    news,err1=rss_items();events,err2=ir_events()
    # Fail closed: do not replace previously valid data with an empty payload after network errors.
    prior={}
    if OUT.exists():
        try: prior=json.loads(OUT.read_text(encoding='utf-8'))
        except Exception: pass
    if not news and err1: news=[x for x in prior.get('news',[]) if x.get('published','') >= (NOW-timedelta(days=7)).isoformat()]
    if not events and err2: events=[x for x in prior.get('events',[]) if x.get('datetime','') >= NOW.isoformat()]
    payload={'updated_at':NOW.isoformat(timespec='seconds'),'news':news,'events':events,'errors':err1+err2,'notes':'新聞為公開 RSS 摘要連結；法說會時間以主辦單位最新公告為準。美股未確認的財報日期不會推測填入。'}
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'news={len(news)} events={len(events)} errors={len(err1)+len(err2)}')
if __name__=='__main__':main()
