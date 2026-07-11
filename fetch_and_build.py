#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
이번 주 블로그 언급 급상승 맛집 TOP 8 생성기
================================================

동작 방식 (상태 저장 없이 매번 완결되는 구조):
  1. 네이버 지역검색 API로 지역별 맛집 후보 목록을 가져온다.
  2. 네이버 블로그검색 API로 각 식당 이름의 최근 블로그 게시물을 가져온다.
     (응답에 postdate가 포함되어 있어 DB 없이도 "이번 주 vs 지난 주" 계산 가능)
  3. 이번 주 언급 수 - 지난 주 언급 수(증가폭) 기준으로 TOP 8을 뽑는다.
  4. 결과를 index.html로 바로 렌더링한다.

사전 준비:
  - https://developers.naver.com 에서 애플리케이션 등록
  - 사용 API로 "검색" 중 "지역"과 "블로그"를 반드시 체크
  - 발급받은 Client ID / Client Secret을 config.py 에 입력

실행:
  python fetch_and_build.py

주의: 이 환경(샌드박스)은 openapi.naver.com에 네트워크 접근이 차단되어 있어
      실행 테스트는 본인 PC(또는 서버)에서 해야 합니다.
"""

import json
import os
import time
import datetime
import re
import urllib.request
import urllib.parse
from html import unescape

try:
    from config import CLIENT_ID, CLIENT_SECRET, REGIONS, DISPLAY_PER_REGION, TOP_N
    # 환경변수(예: GitHub Actions Secrets)가 있으면 config.py 값보다 우선 적용
    # -> 자동화 파이프라인에서는 config.py에 실제 키를 적어두지 않아도 됨
    CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", CLIENT_ID)
    CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", CLIENT_SECRET)
    try:
        from config import OG_IMAGE_URL
    except ImportError:
        # config.py에 OG_IMAGE_URL을 아직 안 넣었으면 빈 이미지로 대체 (에러 방지)
        OG_IMAGE_URL = ""
    try:
        from config import SPONSORED_KEYWORDS
    except ImportError:
        # config.py에 별도 설정이 없으면 기본 필터 키워드 사용
        SPONSORED_KEYWORDS = [
            "협찬", "제공받아", "제공받은", "원고료", "체험단", "기자단",
            "무료로 제공", "업체로부터 제공", "지원을 받아", "서포터즈",
            "본 포스팅은", "해당 게시물은", "소정의 활동비",
        ]
    try:
        from config import TOP_N_PER_REGION
    except ImportError:
        TOP_N_PER_REGION = 5  # 지역별 탭에는 기본 5개까지만 표시
    try:
        from config import EXTRA_BADGES
    except ImportError:
        # 기존에 PERSONAL_BADGE_TEXT를 쓰던 config.py와도 호환되도록 처리
        try:
            from config import PERSONAL_BADGE_TEXT
            EXTRA_BADGES = [PERSONAL_BADGE_TEXT] if PERSONAL_BADGE_TEXT else []
        except ImportError:
            EXTRA_BADGES = []  # 비워두면 추가 배지가 표시되지 않음
    try:
        from config import MAX_BLOG_RESULTS
    except ImportError:
        MAX_BLOG_RESULTS = 300  # 식당 하나당 최대 조회할 게시물 수 (100의 배수, 최대 1000)
except ImportError:
    raise SystemExit(
        "config.py가 없습니다. config.example.py를 config.py로 복사한 뒤 "
        "네이버 API 키와 지역 목록을 입력하세요."
    )

NAVER_API_BASE = "https://openapi.naver.com/v1/search"
REQUEST_DELAY_SEC = 0.15  # 네이버 API 과호출 방지용 딜레이


def _naver_get(path: str, params: dict) -> dict:
    """네이버 오픈API 공통 호출 함수"""
    url = f"{NAVER_API_BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)
    req.add_header("X-Naver-Client-Id", CLIENT_ID)
    req.add_header("X-Naver-Client-Secret", CLIENT_SECRET)
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8")
    time.sleep(REQUEST_DELAY_SEC)
    return json.loads(body)


def strip_tags(text: str) -> str:
    """네이버 응답의 <b> 태그 등을 제거하고 HTML 엔티티를 복원"""
    return unescape(re.sub(r"<[^>]+>", "", text)).strip()


def get_candidate_restaurants(region: str, display: int = 20) -> list:
    """지역검색 API로 '{region} 맛집' 후보 식당 이름 목록을 가져온다"""
    data = _naver_get("local.json", {
        "query": f"{region} 맛집",
        "display": display,
        "sort": "random",
    })
    names = []
    for item in data.get("items", []):
        name = strip_tags(item["title"])
        if name and name not in names:
            names.append(name)
    return names


def is_sponsored_post(title: str, description: str) -> bool:
    """제목+요약에 협찬/광고성 문구가 포함되어 있는지 확인"""
    text = strip_tags(title) + " " + strip_tags(description)
    return any(keyword in text for keyword in SPONSORED_KEYWORDS)


def get_weekly_mention_counts(restaurant_name: str, today: datetime.date) -> tuple:
    """
    블로그검색 API로 특정 식당의 '이번 주'/'지난 주' 언급 수를 계산한다.
    postdate(YYYYMMDD) 필드를 기준으로 집계.
    협찬/광고/기자단 문구가 포함된 게시물은 집계에서 제외한다.

    API 한 번 호출은 최대 100건까지만 주므로, MAX_BLOG_RESULTS까지 여러 번
    페이지를 넘겨가며 가져온다(start 파라미터 사용). 최신순 정렬이므로
    지난 주 시작일보다 오래된 게시물이 나오면 그 지점에서 바로 중단한다
    (게시물이 적은 대부분의 식당은 1번 호출만으로 끝나서 API 낭비가 없다).
    """
    this_week_start = today - datetime.timedelta(days=7)
    last_week_start = today - datetime.timedelta(days=14)

    this_week_count = 0
    last_week_count = 0
    filtered_count = 0

    start = 1
    while start <= MAX_BLOG_RESULTS:
        data = _naver_get("blog.json", {
            "query": restaurant_name,
            "display": 100,
            "start": start,
            "sort": "date",
        })
        items = data.get("items", [])
        if not items:
            break

        reached_older_post = False
        for item in items:
            postdate_str = item.get("postdate", "")
            if not postdate_str or len(postdate_str) != 8:
                continue
            try:
                post_date = datetime.datetime.strptime(postdate_str, "%Y%m%d").date()
            except ValueError:
                continue

            if post_date < last_week_start:
                reached_older_post = True
                break  # 최신순이므로 여기서부터는 더 볼 필요 없음

            if post_date > today:
                continue

            if is_sponsored_post(item.get("title", ""), item.get("description", "")):
                filtered_count += 1
                continue

            if post_date >= this_week_start:
                this_week_count += 1
            else:
                last_week_count += 1

        if reached_older_post or len(items) < 100:
            break
        start += 100

    return this_week_count, last_week_count, filtered_count


def build_ranking() -> tuple:
    """전 지역을 순회하며 급상승 맛집 TOP N을 계산. (결과 리스트, 협찬/광고 추정 총 제외 건수)를 반환"""
    today = datetime.date.today()
    results = []
    seen_names = set()
    total_filtered = 0

    for region in REGIONS:
        print(f"[지역검색] {region} 맛집 후보 수집 중...")
        candidates = get_candidate_restaurants(region, DISPLAY_PER_REGION)

        for name in candidates:
            if name in seen_names:
                continue
            seen_names.add(name)

            this_week, last_week, filtered = get_weekly_mention_counts(name, today)
            total_filtered += filtered
            if filtered:
                print(f"    (협찬/광고 추정 {filtered}건 제외)")
            growth = this_week - last_week

            # 최소 언급량 필터: 우연히 1~2건 튄 걸 급상승으로 착시하지 않도록
            if this_week < 2:
                continue

            results.append({
                "name": name,
                "region": region,
                "this_week": this_week,
                "last_week": last_week,
                "growth": growth,
                "filtered": filtered,
            })
            print(f"  - {name}: 이번주 {this_week} / 지난주 {last_week} (증가 {growth})")

    results.sort(key=lambda x: (x["growth"], x["this_week"]), reverse=True)
    print(f"\n총 협찬/광고 추정 제외 건수: {total_filtered}건")
    return results, total_filtered  # 자르지 않고 전체 반환 - 지역별 탭 계산에 필요


def build_tabs(all_results: list) -> dict:
    """
    전체 결과에서 '전체' 탭 + 지역별 탭 데이터를 만든다.
    REGIONS에 새 지역이 추가되면, 다음 실행 시 자동으로 그 지역 탭도 생긴다
    (코드 수정 불필요 - config.py의 REGIONS만 바뀌면 됨).
    """
    tabs = {"전체": all_results[:TOP_N]}
    for region in REGIONS:
        region_results = [r for r in all_results if r["region"] == region]
        if region_results:  # 데이터가 있는 지역만 탭으로 생성
            tabs[region] = region_results[:TOP_N_PER_REGION]
    return tabs


def naver_map_link(name: str, region: str) -> str:
    """식당 이름+지역으로 네이버 지도 검색 링크를 만든다 (좌표 없이도 동작하는 방식)"""
    query = urllib.parse.quote(f"{name} {region}")
    return f"https://map.naver.com/p/search/{query}"


def render_cards(items: list) -> str:
    """식당 카드 목록을 HTML로 렌더링 (탭 하나 분량)"""
    if not items:
        return '<p style="text-align:center;color:#999;padding:20px 0;">데이터가 없습니다.</p>'
    rows_html = ""
    for i, r in enumerate(items, start=1):
        growth_badge = f"+{r['growth']}" if r["growth"] > 0 else str(r["growth"])
        map_url = naver_map_link(r["name"], r["region"])
        rows_html += f"""
        <a class="card" href="{map_url}" target="_blank" rel="noopener">
          <div class="rank">{i}</div>
          <div class="info">
            <div class="name">{r['name']} <span class="map-icon">📍</span></div>
            <div class="region">{r['region']}</div>
          </div>
          <div class="stats">
            <span class="growth">{growth_badge}</span>
            <span class="count">이번 주 {r['this_week']}건 · 지난 주 {r['last_week']}건</span>
          </div>
        </a>"""
    return rows_html


def render_html(tabs: dict, total_filtered: int = 0, out_path: str = "index.html"):
    """
    tabs: {"전체": [...], "강남": [...], "성수": [...], ...} 형태의 딕셔너리.
    REGIONS에 새 지역이 생기면 build_tabs()가 자동으로 키를 추가해주므로,
    여기서는 tabs에 들어있는 만큼 탭 버튼도 자동으로 늘어난다 (코드 수정 불필요).
    total_filtered: 협찬/광고 추정으로 집계에서 제외된 전체 게시물 건수.
    """
    today_str = datetime.date.today().strftime("%Y년 %m월 %d일")
    region_tags = " ".join(f"#{r}" for r in REGIONS)
    extra_badges_html = "".join(
        f'<span class="hero-badge-secondary">{badge}</span>' for badge in EXTRA_BADGES
    )
    overall = tabs.get("전체", [])
    top_n = len(overall) if overall else TOP_N

    tab_names = list(tabs.keys())
    tab_buttons_html = ""
    tab_panels_html = ""
    for idx, name in enumerate(tab_names):
        active_btn = " active" if idx == 0 else ""
        active_panel = " active" if idx == 0 else ""
        tab_buttons_html += f'<button class="tab-btn{active_btn}" data-tab="tab-{idx}">{name}</button>'
        tab_panels_html += f'<div class="tab-panel{active_panel}" id="tab-{idx}">{render_cards(tabs[name])}</div>'

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>이번 주 블로그 언급 급상승 맛집 TOP 8</title>

<!-- 카톡/문자로 링크 공유 시 미리보기 카드에 쓰이는 정보 -->
<meta property="og:title" content="이번 주 블로그 언급 급상승 맛집 TOP 8">
<meta property="og:description" content="네이버 블로그 언급량 기준, 이번 주 가장 뜨는 맛집 TOP 8을 확인해보세요.">
<meta property="og:image" content="{OG_IMAGE_URL}">
<meta property="og:type" content="website">

<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
    background: #f5f5f7;
    margin: 0;
    padding: 24px 16px 64px;
    color: #1a1a1a;
  }}
  .hero {{
    max-width: 560px;
    margin: 0 auto 20px;
    border-radius: 24px;
    overflow: hidden;
    box-shadow: 0 8px 24px rgba(0,0,0,0.15);
    background: linear-gradient(to top right, #f43f5e, #ec4899, #fb923c);
    padding: 24px;
    color: white;
    position: relative;
  }}
  .hero-icon {{
    position: absolute;
    right: -15px;
    bottom: -20px;
    opacity: 0.15;
    pointer-events: none;
  }}
  .hero-inner {{
    position: relative;
    z-index: 1;
  }}
  .hero-badge {{
    background: rgba(255,255,255,0.2);
    font-size: 10px;
    font-weight: 800;
    padding: 5px 10px;
    border-radius: 999px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    display: inline-block;
  }}
  .hero-badge-row {{
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }}
  .hero-badge-secondary {{
    background: rgba(255,255,255,0.9);
    color: #d9376e;
    font-size: 10px;
    font-weight: 800;
    padding: 5px 10px;
    border-radius: 999px;
    display: inline-block;
  }}
  .hero h1 {{
    font-size: 28px;
    font-weight: 800;
    margin: 12px 0 0;
    line-height: 1.2;
    letter-spacing: -0.02em;
  }}
  .hero-meta {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 12px;
    color: rgba(255,255,255,0.9);
    font-weight: 700;
    margin-top: 16px;
  }}
  .hero-date {{
    background: rgba(0,0,0,0.2);
    padding: 5px 10px;
    border-radius: 12px;
    font-size: 11px;
  }}
  .tabs {{
    max-width: 560px;
    margin: 0 auto 14px;
    display: flex;
    gap: 6px;
    overflow-x: auto;
    padding-bottom: 4px;
  }}
  .tab-btn {{
    flex: 0 0 auto;
    border: none;
    background: white;
    color: #666;
    font-size: 13px;
    font-weight: 700;
    padding: 8px 16px;
    border-radius: 999px;
    cursor: pointer;
  }}
  .tab-btn.active {{
    background: #ff5a36;
    color: white;
  }}
  .tab-panel {{
    display: none;
  }}
  .tab-panel.active {{
    display: block;
  }}
  .filter-note {{
    max-width: 560px;
    margin: 0 auto 14px;
    font-size: 11px;
    color: #aaa;
    text-align: center;
  }}
  .list {{
    max-width: 560px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }}
  .card {{
    background: white;
    border-radius: 14px;
    padding: 16px 18px;
    display: flex;
    align-items: center;
    gap: 14px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    text-decoration: none;
    color: inherit;
    transition: box-shadow 0.15s;
  }}
  .card:hover {{
    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
  }}
  .map-icon {{
    font-size: 12px;
    opacity: 0.6;
  }}
  .rank {{
    font-size: 20px;
    font-weight: 800;
    color: #ff5a36;
    min-width: 28px;
  }}
  .info {{
    flex: 1;
  }}
  .name {{
    font-size: 16px;
    font-weight: 700;
  }}
  .region {{
    font-size: 12px;
    color: #999;
    margin-top: 2px;
  }}
  .stats {{
    text-align: right;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 4px;
  }}
  .growth {{
    font-size: 14px;
    font-weight: 700;
    color: #ff5a36;
  }}
  .count {{
    font-size: 11px;
    color: #aaa;
  }}
</style>
</head>
<body>
  <div class="hero">
    <div class="hero-icon">
      <svg width="160" height="160" fill="currentColor" viewBox="0 0 24 24">
        <path d="M17.66 11.57c-.77-3.95-2.85-6.86-5.27-9.4c-.25-.26-.68-.15-.77.19-.53 2.11-.96 4.98-2.5 7-1.72 2.25-3.68 3.19-4.43 5.92C3.96 18.02 6.07 22 10 22c4.83 0 8.64-4.08 7.66-10.43z"/>
      </svg>
    </div>
    <div class="hero-inner">
      <div class="hero-badge-row">
        <span class="hero-badge">REALTIME BLINK TREND</span>
        {extra_badges_html}
      </div>
      <h1>이주의 급상승<br>맛집 TOP {top_n}</h1>
      <div class="hero-meta">
        <span>{region_tags}</span>
        <span class="hero-date">{today_str}</span>
      </div>
    </div>
  </div>
  <div class="tabs">
    {tab_buttons_html}
  </div>
  <p class="filter-note">협찬·광고·체험단 추정 게시물 {total_filtered}건 제외 후 집계</p>
  <div class="list">
    {tab_panels_html}
  </div>
  <script>
    document.querySelectorAll('.tab-btn').forEach(function(btn) {{
      btn.addEventListener('click', function() {{
        document.querySelectorAll('.tab-btn').forEach(function(b) {{ b.classList.remove('active'); }});
        document.querySelectorAll('.tab-panel').forEach(function(p) {{ p.classList.remove('active'); }});
        btn.classList.add('active');
        document.getElementById(btn.dataset.tab).classList.add('active');
      }});
    }});
  </script>
</body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n완료: {out_path} 생성됨 (탭 {len(tab_names)}개: {', '.join(tab_names)})")


if __name__ == "__main__":
    all_results, total_filtered = build_ranking()
    tabs = build_tabs(all_results)
    render_html(tabs, total_filtered)
    # 원본 데이터도 별도로 저장 (검증/디버깅용)
    with open("top8_raw.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
