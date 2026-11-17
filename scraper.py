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
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; SM-S928N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

YOUTUBE_CHANNELS = {
    "lions_tv": "UCKP8I92724p5v571U6i25wA",         # 라이온즈티비
    "kbo_highlights": "UCoVz66yWHzVsXAFG8WhJK9g",  # KBO 공식채널
    "foreign_scouting": "UCQlxOv_3-nIpA352Afgdglw" # 아시아리그 용병 하이라이트
}

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

# 1. 네이버 스포츠 뉴스 (삼성 20개 + KBO 전체 7개)
async def fetch_naver_news(client: httpx.AsyncClient, db: dict):
    today = datetime.now(KST).strftime("%Y%m%d")
    ss_url = f"https://api-gw.sports.naver.com/news/articles/kbaseball?team=SS&sort=latest&date={today}&page=1&pageSize=20&isPhoto=N"
    kbo_url = f"https://api-gw.sports.naver.com/news/articles/kbaseball?sort=latest&date={today}&page=1&pageSize=7&isPhoto=N"

    try:
        ss_res, kbo_res = await asyncio.gather(
            client.get(ss_url, headers=HEADERS),
            client.get(kbo_url, headers=HEADERS)
        )
        if ss_res.status_code == 200:
            items = ss_res.json().get("result", {}).get("newsList", [])
            if items:
                db["samsung_news"] = [{
                    "id": i.get("aid"),
                    "title": i.get("title"),
                    "summary": i.get("subContent", ""),
                    "press": i.get("officeName"),
                    "time": i.get("datetime", "")[-5:],
                    "badge": extract_badge(i.get("title", "")),
                    "url": f"https://m.sports.naver.com/kbaseball/article/{i.get('oid')}/{i.get('aid')}"
                } for i in items]

        if kbo_res.status_code == 200:
            items = kbo_res.json().get("result", {}).get("newsList", [])
            if items:
                db["kbo_news"] = [{
                    "id": i.get("aid"),
                    "title": i.get("title"),
                    "press": i.get("officeName"),
                    "time": i.get("datetime", "")[-5:],
                    "url": f"https://m.sports.naver.com/kbaseball/article/{i.get('oid')}/{i.get('aid')}"
                } for i in items[:7]]
    except Exception as e:
        print(f"[Naver Error] {e}")

# 2. 유튜브 3개 채널 RSS
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
                    if key == "kbo_highlights" and not any(k in title for k in ["하이라이트", "HL", "vs"]):
                        continue
                    vid = entry.yt_videoid
                    videos.append({
                        "video_id": vid,
                        "title": title,
                        "published": entry.published[:10],
                        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                        "url": entry.link
                    })
                if videos:
                    db["youtube"][key] = videos[:8]
        except Exception as e:
            print(f"[YouTube Error - {key}] {e}")

# 3. 커뮤니티 찌라시 & 경기움짤/영상 수집
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

            if target_key == "game_gifs":
                if any(ex in title.upper() for ex in ["MLB", "NPB", "오타니", "다저스", "양키스"]):
                    continue

            is_ss = any(k in title for k in ["삼성", "라이온즈", "구자욱", "원태인", "김영웅", "이재현", "강민호", "레예스", "디아즈"])
            posts.append({
                "title": title,
                "votes": votes,
                "is_samsung": is_ss,
                "url": full_url
            })
        if posts:
            db[target_key] = posts[:15]
    except Exception as e:
        print(f"[Board Error - {target_key}] {e}")

# 4. 고3야구 (go3base.com)
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
