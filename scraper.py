import os
import re
import json
import asyncio
from datetime import datetime, timezone, timedelta
import httpx
from curl_cffi import requests as cf_requests
from bs4 import BeautifulSoup
import feedparser

KST = timezone(timedelta(hours=9))
DATA_PATH = "public/data.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Cookie": "SOCS=CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg"
}

YOUTUBE_CHANNELS = {
    "lions_tv": "UCKP8I92724p5v571U6i25wA",
    "kbo_highlights": "UCoVz66yWHzVsXAFG8WhJK9g",
    "foreign_scouting": "UCQlxOv_3-nIpA352Afgdglw"
}

def is_official_kbo_highlight(title: str) -> bool:
    normalized = (title or "").replace("|", "｜")
    return (
        "하이라이트｜2026 KBO 리그｜" in normalized
        or "하이라이트｜2026 신한 SOL KBO 포스트시즌｜" in normalized
    )

def load_existing_data():
    if os.path.exists(DATA_PATH):
        try:
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_updated": "",
        "samsung_news": [],
        "kbo_news": [],
        "youtube": {"lions_tv": [], "kbo_highlights": [], "foreign_scouting": []},
        "community_rumors": [],
        "game_gifs": [],
        "go3base": []
    }

def extract_badge(title: str) -> str:
    if "단독" in title: return "단독"
    if any(k in title for k in ["오피셜", "공식발표", "계약체결"]): return "오피셜"
    if any(k in title for k in ["FA", "재계약", "협상"]): return "FA"
    if any(k in title for k in ["외국인", "용병", "영입", "레예스", "코너", "디아즈"]): return "용병"
    if "트레이드" in title: return "트레이드"
    return "일반"

async def fetch_naver_news(client: httpx.AsyncClient, db: dict):
    ss_collected, kbo_collected = [], []
    try:
        ss_rss = await client.get("https://news.google.com/rss/search?q=%EC%82%BC%EC%84%B1+%EB%9D%BC%EC%9D%B4%EC%98%A8%EC%A6%88+%EC%95%BC%EA%B5%AC&hl=ko&gl=KR&ceid=KR:ko", headers=HEADERS)
        if ss_rss.status_code == 200:
            feed = feedparser.parse(ss_rss.text)
            for entry in feed.entries[:20]:
                raw_title = entry.title
                press = raw_title.split(" - ")[-1] if " - " in raw_title else "스포츠뉴스"
                clean_title = raw_title.rsplit(" - ", 1)[0] if " - " in raw_title else raw_title
                ss_collected.append({
                    "id": entry.get("id", clean_title),
                    "title": clean_title,
                    "summary": "",
                    "press": press,
                    "time": entry.get("published", "")[17:22],
                    "badge": extract_badge(clean_title),
                    "url": entry.link
                })
    except Exception as e:
        print(f"[SS News Error] {e}")

    try:
        kbo_rss = await client.get("https://news.google.com/rss/search?q=KBO+%ED%94%84%EB%A1%9C%EC%95%BC%EA%B5%AC&hl=ko&gl=KR&ceid=KR:ko", headers=HEADERS)
        if kbo_rss.status_code == 200:
            feed = feedparser.parse(kbo_rss.text)
            for entry in feed.entries[:12]:
                raw_title = entry.title
                press = raw_title.split(" - ")[-1] if " - " in raw_title else "KBO뉴스"
                clean_title = raw_title.rsplit(" - ", 1)[0] if " - " in raw_title else raw_title
                kbo_collected.append({
                    "id": entry.get("id", clean_title),
                    "title": clean_title,
                    "press": press,
                    "time": entry.get("published", "")[17:22],
                    "url": entry.link
                })
    except Exception as e:
        print(f"[KBO News Error] {e}")

    if ss_collected: db["samsung_news"] = ss_collected
    if kbo_collected: db["kbo_news"] = kbo_collected

def extract_video_renderers(obj, found: list):
    if isinstance(obj, dict):
        if "videoRenderer" in obj:
            vr = obj["videoRenderer"]
            vid = vr.get("videoId")
            runs = vr.get("title", {}).get("runs", [])
            title = "".join(r.get("text", "") for r in runs) if runs else vr.get("title", {}).get("simpleText", "")
            if vid and title:
                found.append((vid, title))
        for v in obj.values():
            extract_video_renderers(v, found)
    elif isinstance(obj, list):
        for item in obj:
            extract_video_renderers(item, found)

async def fetch_kbo_highlights_deep(client: httpx.AsyncClient) -> list:
    queries = [
        "https://www.youtube.com/@kbo1982/search?query=%ED%95%98%EC%9D%B4%EB%9D%BC%EC%9D%B4%ED%8A%B8%EF%BD%9C2026+%EC%8B%A0%ED%95%9C+SOL+KBO+%ED%8F%AC%EC%8A%A4%ED%8A%B8%EC%8B%9C%EC%A6%8C",
        "https://www.youtube.com/@kbo1982/search?query=%ED%95%98%EC%9D%B4%EB%9D%BC%EC%9D%B4%ED%8A%B8%EF%BD%9C2026+KBO+%EB%A6%AC%EA%B7%B8"
    ]
    collected, seen = [], set()
    for url in queries:
        try:
            res = await client.get(url, headers=HEADERS)
            if res.status_code != 200:
                continue
            m = re.search(r"var ytInitialData = (\{.*?\});</script>", res.text)
            if not m:
                continue
            data = json.loads(m.group(1))
            pairs = []
            extract_video_renderers(data, pairs)
            for vid, title in pairs:
                if vid not in seen and is_official_kbo_highlight(title):
                    seen.add(vid)
                    date_m = re.search(r"(\d{1,2}\.\d{1,2}\([^)]+\))", title)
                    pub_label = date_m.group(1) if date_m else "2026 시즌"
                    collected.append({
                        "video_id": vid,
                        "title": title,
                        "published": pub_label,
                        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                        "url": f"https://www.youtube.com/watch?v={vid}"
                    })
        except Exception as e:
            print(f"[KBO Deep Search Error] {e}")
    return collected[:20]

async def fetch_youtube(client: httpx.AsyncClient, db: dict):
    for key, ch_id in YOUTUBE_CHANNELS.items():
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}"
        try:
            res = await client.get(url, headers=HEADERS)
            if res.status_code == 200:
                feed = feedparser.parse(res.text)
                videos = []
                for entry in feed.entries:
                    title = entry.title
                    if key == "kbo_highlights" and not is_official_kbo_highlight(title):
                        continue
                    vid = entry.yt_videoid
                    videos.append({
                        "video_id": vid,
                        "title": title,
                        "published": entry.published[:10],
                        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                        "url": entry.link
                    })
                if key != "kbo_highlights" and videos:
                    db["youtube"][key] = videos[:8]
                elif key == "kbo_highlights" and videos:
                    db["youtube"]["kbo_highlights"] = videos
        except Exception as e:
            print(f"[YouTube RSS Error - {key}] {e}")

    deep_kbo = await fetch_kbo_highlights_deep(client)
    merged = (db["youtube"].get("kbo_highlights") or []) + deep_kbo
    seen, final_kbo = set(), []
    for v in merged:
        vid = v.get("video_id")
        if vid and vid not in seen and is_official_kbo_highlight(v.get("title", "")):
            seen.add(vid)
            final_kbo.append(v)
    if final_kbo:
        db["youtube"]["kbo_highlights"] = final_kbo[:20]

def fetch_community_board(category: str, target_key: str, db: dict):
    url = f"https://www.fmkorea.com/index.php?mid=baseball&category={category}"
    try:
        res = cf_requests.get(url, impersonate="chrome124", timeout=15)
        if res.status_code != 200:
            return
        soup = BeautifulSoup(res.text, "html.parser")
        rows = soup.select("tbody > tr:not(.notice)")
        posts = []
        for row in rows:
            title_el = row.select_one("td.title a.hx")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            full_url = f"https://www.fmkorea.com{href}" if href.startswith("/") else href
            vote_el = row.select_one("td.m_no_voted")
            votes = int(re.sub(r"[^0-9]", "", vote_el.get_text(strip=True)) or 0) if vote_el else 0
            if target_key == "game_gifs" and any(ex in title.upper() for ex in ["MLB", "NPB", "오타니", "다저스", "양키스"]):
                continue
            is_ss = any(k in title for k in ["삼성", "라이온즈", "구자욱", "원태인", "김영웅", "이재현", "강민호", "레예스", "디아즈"])
            posts.append({"title": title, "votes": votes, "is_samsung": is_ss, "url": full_url})
        if posts:
            db[target_key] = posts[:15]
    except Exception as e:
        print(f"[Board Error - {target_key}] {e}")

async def fetch_go3base(client: httpx.AsyncClient, db: dict):
    try:
        res = await client.get("https://www.go3base.com/", headers=HEADERS)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            links = soup.select("a[href*='board'], a[href*='view'], .latest a")
            articles, seen = [], set()
            for a in links:
                title = a.get_text(strip=True)
                href = a.get("href", "")
                if len(title) > 8 and title not in seen:
                    seen.add(title)
                    full_url = href if href.startswith("http") else f"https://www.go3base.com/{href.lstrip('/')}"
                    articles.append({"title": title, "url": full_url})
            if articles:
                db["go3base"] = articles[:10]
    except Exception as e:
        print(f"[go3base Error] {e}")

async def main():
    db = load_existing_data()
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        await asyncio.gather(
            fetch_naver_news(client, db),
            fetch_youtube(client, db),
            fetch_go3base(client, db)
        )
    fetch_community_board("4392770108", "community_rumors", db)
    fetch_community_board("741054949", "game_gifs", db)

    db["last_updated"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    print(f"Updated at {db['last_updated']}")

if __name__ == "__main__":
    asyncio.run(main())
