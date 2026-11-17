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
    "Referer": "https://m.sports.naver.com/kbaseball/news"
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

# 네이버 스포츠 공식 API를 통한 삼성 & KBO 뉴스 수집
async def fetch_naver_news(client: httpx.AsyncClient, db: dict):
    ss_collected, kbo_collected = [], []
    today = datetime.now(KST).strftime("%Y%m%d")
    
    ss_url = f"https://api-gw.sports.naver.com/news/articles/kbaseball?sectionId=kbo&team=SS&sort=latest&date={today}&page=1&pageSize=20&isPhoto=N"
    kbo_url = f"https://api-gw.sports.naver.com/news/articles/kbaseball?sectionId=kbo&sort=latest&date={today}&page=1&pageSize=15&isPhoto=N"

    try:
        ss_res, kbo_res = await asyncio.gather(
            client.get(ss_url, headers=HEADERS),
            client.get(kbo_url, headers=HEADERS)
        )
        if ss_res.status_code == 200:
            items = ss_res.json().get("result", {}).get("newsList", []) or []
            for i in items:
                oid, aid, title = i.get("oid"), i.get("aid"), i.get("title", "")
                if oid and aid and title:
                    ss_collected.append({
                        "id": aid,
                        "title": title,
                        "summary": i.get("subContent", ""),
                        "press": i.get("officeName", "네이버스포츠"),
                        "time": (i.get("datetime") or "")[-5:],
                        "badge": extract_badge(title),
                        "url": f"https://m.sports.naver.com/kbaseball/article/{oid}/{aid}"
                    })
        if kbo_res.status_code == 200:
            items = kbo_res.json().get("result", {}).get("newsList", []) or []
            for i in items:
                oid, aid, title = i.get("oid"), i.get("aid"), i.get("title", "")
                if oid and aid and title:
                    kbo_collected.append({
                        "id": aid,
                        "title": title,
                        "press": i.get("officeName", "네이버스포츠"),
                        "time": (i.get("datetime") or "")[-5:],
                        "url": f"https://m.sports.naver.com/kbaseball/article/{oid}/{aid}"
                    })
    except Exception as e:
        print(f"[Naver News API Error] {e}")

    if ss_collected: db["samsung_news"] = ss_collected
    if kbo_collected: db["kbo_news"] = kbo_collected

# (이하 유튜브, 커뮤니티, 고3야구 수집 함수 동일)
