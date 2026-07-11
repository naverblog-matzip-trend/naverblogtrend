#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
이번 주 블로그 언급 급상승 맛집 TOP 8 생성기
================================================

동작 방식 (상태 저장 없이 매번 완결되는 구조):
  1. (선택) CORE_REGIONS + CANDIDATE_REGIONS 화제성 비교로 이번에 쓸 지역을 정한다.
  2. 네이버 지역검색 API로 지역별 맛집 후보 목록을 가져온다.
  3. 네이버 블로그검색 API로 각 식당 이름의 최근 블로그 게시물을 가져온다.
     (응답에 postdate가 포함되어 있어 DB 없이도 "이번 주 vs 지난 주" 계산 가능.
     달력 기준이 아니라 "실행 시점 기준 최근 7일 vs 그 전 7일"을 매번 새로 계산하는
     롤링 방식이라, 매일 실행하면 매일 최신 트렌드로 갱신된다.)
  4. 협찬/광고 추정 게시물은 집계에서 제외한다.
  5. 이번 주 언급 수 - 지난 주 언급 수(증가폭) 기준으로 식당 TOP N, 지역 랭킹을 뽑는다.
  6. 결과를 index.html(웹페이지) + robots.txt(검색로봇 안내 파일)로 렌더링한다.

주요 기능:
  - 전체 / 지역랭킹 / 지역별 탭 (탭 클릭으로 화면 전환)
  - 카드 클릭 시 네이버 지도로 바로 연결
  - 카톡/문자 공유 시 미리보기 카드(썸네일/제목/설명) 표시
  - Google Search Console / 네이버 서치어드바이저 소유자 인증 지원
  - 지역 자동 선정 (고정 지역 + 화제성 상위 후보 지역 자동 조합)

사전 준비:
  - https://developers.naver.com 에서 애플리케이션 등록
  - 사용 API로 "검색" 카테고리를 체크 (지역검색/블로그검색 모두 포함됨)
  - 발급받은 Client ID / Client Secret을 config.py 에 입력

실행:
  python fetch_and_build.py

전체 설정 옵션은 config.example.py에 전부 주석과 함께 정리되어 있습니다.

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
    # --- 필수 설정값 (config.py에 반드시 있어야 함) ---------------------------
    from config import CLIENT_ID, CLIENT_SECRET, REGIONS, DISPLAY_PER_REGION, TOP_N

    # 환경변수(예: GitHub Actions Secrets)가 있으면 config.py 값보다 우선 적용한다.
    # -> 이렇게 하면 GitHub에 올리는 config.py에는 진짜 API 키를 안 적어도 되고,
    #    대신 GitHub Secrets에 등록해둔 값을 자동으로 가져다 쓴다 (키 노출 방지).
    # -> 로컬(내 컴퓨터)에서 그냥 실행할 때는 이 환경변수가 없으므로,
    #    config.py에 적어둔 값이 그대로 쓰인다.
    CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", CLIENT_ID)
    CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", CLIENT_SECRET)

    # --- 선택 설정값들 (아래는 전부 "config.py에 없어도 기본값으로 동작") --------
    # 패턴: try에서 config.py 값을 읽어보고, 없으면(ImportError) except에서
    #       기본값을 대신 넣어준다. 이 덕분에 새 기능이 추가돼도 예전 config.py를
    #       쓰던 사람이 에러 없이 계속 실행할 수 있다 (하위 호환).

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
        from config import GENUINE_KEYWORDS
    except ImportError:
        # "진짜 내돈내산" 판별용 키워드 - 협찬 문구가 없다고 자동으로 진짜인 건 아니라서,
        # 반대로 이런 문구가 있으면 더 신뢰도 높은 후기로 본다
        GENUINE_KEYWORDS = ["내돈내산", "내돈내먹", "영수증", "카드내역", "직접 결제"]
    try:
        from config import MIN_SAMPLE_FOR_GENUINE_RATIO
    except ImportError:
        MIN_SAMPLE_FOR_GENUINE_RATIO = 5  # 이 값 미만이면 지수를 표시하지 않음 (표본 너무 적음)
    try:
        from config import MIN_GENUINE_RATIO_TO_SHOW
    except ImportError:
        # 기본값 None = 필터링 기능 꺼짐 (기존 동작 그대로 유지, 하위 호환).
        # 숫자(예: 20)를 넣으면, 내돈내산 지수가 그 값 미만인 식당은 순위 후보에서
        # 아예 제외된다 (배지만 숨기는 게 아니라 결과 리스트 자체에서 빠짐).
        MIN_GENUINE_RATIO_TO_SHOW = None
    try:
        from config import TOP_N_PER_REGION
    except ImportError:
        TOP_N_PER_REGION = 5  # 지역별 탭에는 기본 5개까지만 표시
    try:
        from config import EXTRA_BADGES
    except ImportError:
        # 기존에 PERSONAL_BADGE_TEXT(문구 1개짜리 옛날 방식)를 쓰던 config.py와도
        # 호환되도록, 그 값이 있으면 리스트 형태로 자동 변환해서 재사용한다.
        try:
            from config import PERSONAL_BADGE_TEXT
            EXTRA_BADGES = [PERSONAL_BADGE_TEXT] if PERSONAL_BADGE_TEXT else []
        except ImportError:
            EXTRA_BADGES = []  # 비워두면 추가 배지가 표시되지 않음
    try:
        from config import MAX_BLOG_RESULTS
    except ImportError:
        MAX_BLOG_RESULTS = 300  # 식당 하나당 최대 조회할 게시물 수 (100의 배수, 최대 1000)
    try:
        from config import GOOGLE_SITE_VERIFICATION
    except ImportError:
        GOOGLE_SITE_VERIFICATION = ""  # Google Search Console 소유자 인증 코드
    try:
        from config import NAVER_SITE_VERIFICATION
    except ImportError:
        NAVER_SITE_VERIFICATION = ""  # 네이버 서치어드바이저 소유자 인증 코드
    try:
        from config import CORE_REGIONS
    except ImportError:
        CORE_REGIONS = []  # 항상 고정으로 포함할 지역 (비워두면 기존 REGIONS 방식 그대로 사용)
    try:
        from config import CANDIDATE_REGIONS
    except ImportError:
        CANDIDATE_REGIONS = []  # 화제성 검사 대상 후보 지역 풀 (비워두면 자동 선정 기능 꺼짐)
    try:
        from config import HOT_REGION_COUNT
    except ImportError:
        HOT_REGION_COUNT = 3  # 후보 지역 중 화제성 상위 몇 개를 골라 추가할지
except ImportError:
    # CLIENT_ID 등 "필수" 설정값 import 자체가 실패했다는 뜻 = config.py가 아예 없음
    raise SystemExit(
        "config.py가 없습니다. config.example.py를 config.py로 복사한 뒤 "
        "네이버 API 키와 지역 목록을 입력하세요."
    )

NAVER_API_BASE = "https://openapi.naver.com/v1/search"
REQUEST_DELAY_SEC = 0.15  # 네이버 API 과호출 방지용 딜레이


def _naver_get(path: str, params: dict) -> dict:
    """
    네이버 오픈API를 호출하는 공통 함수.
    path 예: "local.json"(지역검색), "blog.json"(블로그검색)
    params는 그대로 URL 쿼리 파라미터로 붙는다 (query, display, start, sort 등).
    """
    url = f"{NAVER_API_BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)
    # 네이버 API는 URL이 아니라 요청 헤더에 인증 정보를 담아서 보내는 방식이다
    req.add_header("X-Naver-Client-Id", CLIENT_ID)
    req.add_header("X-Naver-Client-Secret", CLIENT_SECRET)
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8")
    time.sleep(REQUEST_DELAY_SEC)  # 짧은 시간에 너무 많이 호출하지 않도록 매번 살짝 쉬어준다
    return json.loads(body)


def strip_tags(text: str) -> str:
    """
    네이버 API 응답 텍스트에는 검색어 강조용 <b> 태그와 &amp; 같은 HTML 엔티티가
    섞여서 온다. 이걸 순수 텍스트로 정리해주는 함수.
    """
    return unescape(re.sub(r"<[^>]+>", "", text)).strip()


def get_candidate_restaurants(region: str, display: int = 20) -> list:
    """
    지역검색 API로 "{region} 맛집"을 검색해서 후보 식당 이름 목록을 가져온다.
    예: get_candidate_restaurants("강남") -> ["OO식당", "XX카페", ...]
    """
    data = _naver_get("local.json", {
        "query": f"{region} 맛집",
        "display": display,
        "sort": "random",  # 매번 똑같은 상위 업체만 나오지 않도록 무작위 정렬
    })
    names = []
    for item in data.get("items", []):
        name = strip_tags(item["title"])
        if name and name not in names:  # 같은 이름 중복 제거
            names.append(name)
    return names


def is_sponsored_post(title: str, description: str) -> bool:
    """
    게시물 제목+요약에 협찬/광고성 문구(SPONSORED_KEYWORDS)가 하나라도
    포함되어 있으면 True를 반환한다. (본문 전체는 API로 못 가져오므로
    제목+요약만 검사 - 본문 뒷부분에만 문구가 있으면 못 잡을 수 있음)
    """
    text = strip_tags(title) + " " + strip_tags(description)
    return any(keyword in text for keyword in SPONSORED_KEYWORDS)


def is_genuine_post(title: str, description: str) -> bool:
    """
    "협찬 문구가 없다"는 건 소극적 증거일 뿐이고, "내돈내산/영수증/카드내역" 같은
    문구가 실제로 있는 건 훨씬 적극적인 증거다. 이 함수는 그런 '진짜 후기' 신호가
    있는지 확인한다. (SPONSORED_KEYWORDS 필터를 통과한 게시물에 대해서만 호출됨)
    """
    text = strip_tags(title) + " " + strip_tags(description)
    return any(keyword in text for keyword in GENUINE_KEYWORDS)


def get_weekly_mention_counts(restaurant_name: str, today: datetime.date) -> tuple:
    """
    블로그검색 API로 특정 식당의 '이번 주'/'지난 주' 언급 수를 계산한다.
    postdate(YYYYMMDD) 필드를 기준으로 집계.
    협찬/광고/기자단 문구가 포함된 게시물은 집계에서 제외한다.
    (제외되지 않은 게시물 중에서는 "내돈내산" 계열 문구 비율도 함께 집계한다
    -> get_genuine_ratio()에서 이 값으로 "내돈내산 지수"를 계산함)

    API 한 번 호출은 최대 100건까지만 주므로, MAX_BLOG_RESULTS까지 여러 번
    페이지를 넘겨가며 가져온다(start 파라미터 사용). 최신순 정렬이므로
    지난 주 시작일보다 오래된 게시물이 나오면 그 지점에서 바로 중단한다
    (게시물이 적은 대부분의 식당은 1번 호출만으로 끝나서 API 낭비가 없다).
    """
    # "이번 주"="오늘부터 7일 전까지", "지난 주"="8~14일 전까지" - 달력 기준이 아니라
    # 실행하는 날짜(today) 기준으로 매번 새로 계산되는 롤링(rolling) 방식이다.
    # 그래서 이 스크립트를 매일 실행하면, 매일 최신 7일 트렌드가 갱신된다.
    this_week_start = today - datetime.timedelta(days=7)
    last_week_start = today - datetime.timedelta(days=14)

    this_week_count = 0
    last_week_count = 0
    filtered_count = 0
    genuine_count = 0  # 협찬 필터를 통과한 글 중, "내돈내산" 계열 문구가 있었던 개수

    # start=1부터 100씩 늘려가며 페이지를 넘긴다 (네이버 API는 한 번에 최대 100건만 줌)
    start = 1
    while start <= MAX_BLOG_RESULTS:
        data = _naver_get("blog.json", {
            "query": restaurant_name,
            "display": 100,
            "start": start,
            "sort": "date",  # 최신순 정렬 - 오래된 글이 나오면 바로 멈출 수 있어서 효율적
        })
        items = data.get("items", [])
        if not items:
            break  # 더 이상 게시물이 없으면 종료

        reached_older_post = False
        for item in items:
            postdate_str = item.get("postdate", "")
            if not postdate_str or len(postdate_str) != 8:
                continue  # 날짜 정보가 없는 이상한 게시물은 건너뜀
            try:
                post_date = datetime.datetime.strptime(postdate_str, "%Y%m%d").date()
            except ValueError:
                continue

            if post_date < last_week_start:
                # 14일보다 오래된 글이 나왔다 = 최신순 정렬이므로 이 뒤로는
                # 전부 더 오래된 글이라는 뜻 -> 더 볼 필요 없이 바로 중단
                reached_older_post = True
                break

            if post_date > today:
                continue  # 혹시 미래 날짜로 잘못 찍힌 데이터 방어

            title = item.get("title", "")
            description = item.get("description", "")

            if is_sponsored_post(title, description):
                filtered_count += 1
                continue  # 협찬/광고 추정 게시물은 집계에서 제외

            if is_genuine_post(title, description):
                genuine_count += 1  # "내돈내산" 계열 문구가 실제로 있었던 글

            if post_date >= this_week_start:
                this_week_count += 1
            else:
                last_week_count += 1

        # 오래된 글에 도달했거나(더 볼 필요 없음), 이 페이지가 100건 미만이었다면
        # (=마지막 페이지) 여기서 멈춘다. 그렇지 않으면 다음 100건을 더 가져온다.
        if reached_older_post or len(items) < 100:
            break
        start += 100

    return this_week_count, last_week_count, filtered_count, genuine_count


def get_genuine_ratio(this_week_count: int, last_week_count: int, genuine_count: int):
    """
    협찬 필터를 통과한 전체 게시물(this_week+last_week) 중 "내돈내산" 계열
    문구가 있었던 비율(%)을 계산한다. 표본이 MIN_SAMPLE_FOR_GENUINE_RATIO보다
    적으면 신뢰도가 낮으므로 None을 반환한다 (화면에 지수를 표시하지 않음).
    """
    total = this_week_count + last_week_count
    if total < MIN_SAMPLE_FOR_GENUINE_RATIO:
        return None
    return round(genuine_count / total * 100)


def get_region_volume(region: str) -> int:
    """'{지역} 맛집' 블로그 검색의 총 게시물 수를 그 지역 화제성의 근사치로 사용"""
    try:
        data = _naver_get("blog.json", {"query": f"{region} 맛집", "display": 1, "sort": "date"})
        return int(data.get("total", 0))
    except Exception:
        return 0


def resolve_active_regions() -> list:
    """
    CORE_REGIONS(고정) + CANDIDATE_REGIONS 중 화제성 상위 HOT_REGION_COUNT개를 합쳐
    이번 실행에서 실제로 쓸 지역 목록을 정한다.
    CANDIDATE_REGIONS가 비어있으면(설정 안 했으면) 기존 REGIONS 방식 그대로 사용 (하위 호환).
    """
    if not CANDIDATE_REGIONS:
        return REGIONS

    print("[지역 자동 선정] 후보 지역별 화제성(블로그 총 게시물 수) 조회 중...")
    scored = []
    for region in CANDIDATE_REGIONS:
        volume = get_region_volume(region)
        print(f"  - {region}: {volume}건")
        scored.append((region, volume))
    scored.sort(key=lambda x: x[1], reverse=True)
    hot_picks = [r for r, _ in scored[:HOT_REGION_COUNT]]

    combined = list(CORE_REGIONS)
    for r in hot_picks:
        if r not in combined:
            combined.append(r)
    print(f"[지역 확정] 고정 {CORE_REGIONS} + 화제성 상위 {hot_picks} = {combined}")
    return combined


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
                continue  # 이미 다른 지역에서 나왔던 식당이면 중복 집계 방지
            seen_names.add(name)

            this_week, last_week, filtered, genuine = get_weekly_mention_counts(name, today)
            total_filtered += filtered
            if filtered:
                print(f"    (협찬/광고 추정 {filtered}건 제외)")
            growth = this_week - last_week  # 이게 바로 "급상승" 순위를 매기는 핵심 숫자
            genuine_ratio = get_genuine_ratio(this_week, last_week, genuine)

            # 최소 언급량 필터: 우연히 1~2건 튄 걸 급상승으로 착시하지 않도록
            if this_week < 2:
                continue

            # 내돈내산 지수 필터: 표본이 충분한데(genuine_ratio가 None이 아님)
            # 그 값이 기준(MIN_GENUINE_RATIO_TO_SHOW)보다 낮으면, 배지만 숨기는 게
            # 아니라 순위 후보 자체에서 뺀다. 표본이 부족해서 지수를 못 낸 경우는
            # (genuine_ratio가 None) 판단 근거가 없으므로 그냥 통과시킨다.
            if (
                MIN_GENUINE_RATIO_TO_SHOW is not None
                and genuine_ratio is not None
                and genuine_ratio < MIN_GENUINE_RATIO_TO_SHOW
            ):
                print(f"    ({name}: 내돈내산 지수 {genuine_ratio}%로 낮아 순위에서 제외)")
                continue

            results.append({
                "name": name,
                "region": region,
                "this_week": this_week,
                "last_week": last_week,
                "growth": growth,
                "filtered": filtered,
                "genuine_ratio": genuine_ratio,  # None이면 표본 부족 -> 화면에 배지 안 뜸
            })
            ratio_note = f", 내돈내산 {genuine_ratio}%" if genuine_ratio is not None else ""
            print(f"  - {name}: 이번주 {this_week} / 지난주 {last_week} (증가 {growth}{ratio_note})")

    # growth(증가폭) 큰 순서로 정렬. growth가 같으면 this_week가 큰 쪽을 우선(동점 처리)
    results.sort(key=lambda x: (x["growth"], x["this_week"]), reverse=True)
    print(f"\n총 협찬/광고 추정 제외 건수: {total_filtered}건")
    return results, total_filtered  # 자르지 않고 전체 반환 - 지역별 탭 계산에 필요


def build_region_ranking(all_results: list) -> list:
    """
    지역별로 증가폭 합계를 집계해 '어느 동네가 가장 뜨는지' 랭킹을 만든다.
    (개별 식당 랭킹과는 별개로, 지역 단위로 다시 한번 합산하는 것)
    """
    agg = {}  # {"강남": {누적 통계...}, "성수": {...}, ...}
    for r in all_results:
        region = r["region"]
        if region not in agg:
            # 이 지역을 처음 만났으면 0으로 초기화
            agg[region] = {
                "region": region,
                "total_growth": 0,
                "total_this_week": 0,
                "total_last_week": 0,
                "count": 0,  # 이 지역에서 조건을 만족한 식당이 몇 개였는지
            }
        agg[region]["total_growth"] += r["growth"]
        agg[region]["total_this_week"] += r["this_week"]
        agg[region]["total_last_week"] += r["last_week"]
        agg[region]["count"] += 1
    ranking = list(agg.values())
    ranking.sort(key=lambda x: x["total_growth"], reverse=True)  # 증가폭 합계 큰 지역이 위로
    return ranking


def build_tabs(all_results: list, region_ranking: list) -> dict:
    """
    전체 결과에서 '전체' 탭 + '지역랭킹' 탭 + 지역별 탭 데이터를 만든다.
    반환값은 {"탭 이름": [카드로 보여줄 데이터 목록], ...} 형태의 딕셔너리.
    REGIONS에 새 지역이 추가되면, 다음 실행 시 자동으로 그 지역 탭도 생긴다
    (코드 수정 불필요 - config.py의 REGIONS만 바뀌면 됨).
    """
    tabs = {"전체": all_results[:TOP_N]}  # 첫 번째 탭은 항상 "전체" (전 지역 통합 TOP N)
    if region_ranking:
        tabs["지역랭킹"] = region_ranking  # 두 번째 탭 = 지역별 랭킹 (데이터 있을 때만)
    for region in REGIONS:
        region_results = [r for r in all_results if r["region"] == region]
        if region_results:  # 데이터가 있는 지역만 탭으로 생성 (빈 탭 방지)
            tabs[region] = region_results[:TOP_N_PER_REGION]
    return tabs


def naver_map_link(name: str, region: str) -> str:
    """
    식당 이름+지역으로 네이버 지도 검색 링크를 만든다.
    실제 좌표(위도/경도) 없이 "이름으로 검색"하는 방식이라 구현이 간단하고,
    네이버 지도가 알아서 가장 근접한 결과를 찾아 보여준다.
    """
    query = urllib.parse.quote(f"{name} {region}")  # 한글 등을 URL에 넣을 수 있게 인코딩
    return f"https://map.naver.com/p/search/{query}"


def render_region_cards(ranking: list) -> str:
    """
    '지역랭킹' 탭 전용 카드 렌더러. render_cards()와 모양은 비슷하지만
    식당 하나가 아니라 "지역 하나"를 카드 하나로 보여준다는 점이 다르다.
    """
    if not ranking:
        return '<p style="text-align:center;color:#999;padding:20px 0;">데이터가 없습니다.</p>'
    rows_html = ""
    for i, r in enumerate(ranking, start=1):
        growth_badge = f"+{r['total_growth']}" if r["total_growth"] > 0 else str(r["total_growth"])
        map_url = f"https://map.naver.com/p/search/{urllib.parse.quote(r['region'] + ' 맛집')}"
        rows_html += f"""
        <a class="card" href="{map_url}" target="_blank" rel="noopener">
          <div class="rank">{i}</div>
          <div class="info">
            <div class="name">{r['region']} <span class="map-icon">📍</span></div>
            <div class="region">언급 식당 {r['count']}곳</div>
          </div>
          <div class="stats">
            <span class="growth">{growth_badge}</span>
            <span class="count">이번 주 {r['total_this_week']}건 · 지난 주 {r['total_last_week']}건</span>
          </div>
        </a>"""
    return rows_html


def render_cards(items: list) -> str:
    """
    일반 탭("전체", 지역별 탭)에서 쓰는 식당 카드 렌더러.
    각 식당을 카드 하나로 만들고, 클릭하면 네이버 지도로 연결되는 링크(<a>)로 감싼다.
    genuine_ratio(내돈내산 지수)가 있으면 이름 옆에 색깔 배지로 같이 보여준다.
    """
    if not items:
        return '<p style="text-align:center;color:#999;padding:20px 0;">데이터가 없습니다.</p>'
    rows_html = ""
    for i, r in enumerate(items, start=1):
        growth_badge = f"+{r['growth']}" if r["growth"] > 0 else str(r["growth"])
        map_url = naver_map_link(r["name"], r["region"])

        # 내돈내산 지수 배지: 70% 이상=초록(신뢰), 40~69%=주황(보통), 40% 미만=빨강(주의)
        # 표본이 너무 적으면(genuine_ratio가 None) 배지 자체를 안 보여준다
        genuine_ratio = r.get("genuine_ratio")
        genuine_badge_html = ""
        if genuine_ratio is not None:
            if genuine_ratio >= 70:
                tier_class = "genuine-high"
            elif genuine_ratio >= 40:
                tier_class = "genuine-mid"
            else:
                tier_class = "genuine-low"
            genuine_badge_html = f'<span class="genuine-badge {tier_class}">내돈내산 {genuine_ratio}%</span>'

        rows_html += f"""
        <a class="card" href="{map_url}" target="_blank" rel="noopener">
          <div class="rank">{i}</div>
          <div class="info">
            <div class="name">{r['name']} <span class="map-icon">📍</span></div>
            <div class="region">{r['region']} {genuine_badge_html}</div>
          </div>
          <div class="stats">
            <span class="growth">{growth_badge}</span>
            <span class="count">이번 주 {r['this_week']}건 · 지난 주 {r['last_week']}건</span>
          </div>
        </a>"""
    # 카드 목록 맨 아래에 랜덤 뽑기 버튼 추가 (식당 카드가 있는 탭에서만 의미가 있어서
    # render_region_cards에는 안 넣고 여기에만 넣는다)
    rows_html += """
        <button class="pick-btn" onclick="runRandomPick(this)">🎰 오늘 메뉴 랜덤 추천</button>"""
    return rows_html


def render_html(tabs: dict, total_filtered: int = 0, out_path: str = "index.html"):
    """
    tabs 데이터를 실제 웹페이지(index.html) 하나로 만드는 함수.
    이 함수가 하는 일을 순서대로 요약하면:
      1) 탭 버튼과 탭 내용(카드들)을 미리 문자열로 만들어둔다
      2) 그 문자열들을 큰 HTML 템플릿 안에 끼워 넣는다
      3) 완성된 HTML을 파일로 저장한다

    tabs: {"전체": [...], "지역랭킹": [...], "강남": [...], ...} 형태의 딕셔너리.
    REGIONS에 새 지역이 생기면 build_tabs()가 자동으로 키를 추가해주므로,
    여기서는 tabs에 들어있는 만큼 탭 버튼도 자동으로 늘어난다 (코드 수정 불필요).
    total_filtered: 협찬/광고 추정으로 집계에서 제외된 전체 게시물 건수.
    """
    today_str = datetime.date.today().strftime("%Y년 %m월 %d일")
    region_tags = " ".join(f"#{r}" for r in REGIONS)  # 헤더에 보이는 "#강남 #성수..." 문구
    # EXTRA_BADGES 리스트에 있는 문구들을 헤더 배지로 하나씩 만든다 (몇 개든 가능)
    extra_badges_html = "".join(
        f'<span class="hero-badge-secondary">{badge}</span>' for badge in EXTRA_BADGES
    )
    overall = tabs.get("전체", [])
    top_n = len(overall) if overall else TOP_N  # 헤더의 "TOP N" 숫자

    # --- 탭 버튼 + 탭 내용물을 미리 문자열로 만들어두기 -------------------------
    # tabs 딕셔너리를 순서대로 돌면서, 탭마다 버튼 하나 + 내용판(panel) 하나씩 생성.
    # 첫 번째 탭(idx==0)만 처음부터 화면에 보이도록 "active" 클래스를 붙인다.
    tab_names = list(tabs.keys())
    tab_buttons_html = ""
    tab_panels_html = ""
    for idx, name in enumerate(tab_names):
        active_btn = " active" if idx == 0 else ""
        active_panel = " active" if idx == 0 else ""
        tab_buttons_html += f'<button class="tab-btn{active_btn}" data-tab="tab-{idx}">{name}</button>'
        # "지역랭킹" 탭만 다른 모양의 카드(render_region_cards)를 쓰고, 나머지는 식당 카드
        panel_content = render_region_cards(tabs[name]) if name == "지역랭킹" else render_cards(tabs[name])
        tab_panels_html += f'<div class="tab-panel{active_panel}" id="tab-{idx}">{panel_content}</div>'

    # --- 여기부터 실제 HTML 문서 전체를 하나의 긴 문자열로 조립한다 ------------
    # 구조: <head> 메타태그(검색/공유용 정보) -> <style> CSS -> <body> 실제 화면
    #       -> <script> 탭 클릭 시 화면 전환 기능
    # 위에서 미리 만들어둔 tab_buttons_html / tab_panels_html / extra_badges_html
    # 등이 아래 {중괄호} 자리에 그대로 끼워 넣어진다.
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>이번 주 블로그 언급 급상승 맛집 TOP 8</title>

<!-- 카톡/문자로 링크 공유 시 미리보기 카드에 쓰이는 정보 -->
<meta name="description" content="네이버 블로그 언급량 기준, 이번 주 가장 뜨는 맛집 TOP 8을 확인해보세요.">
<meta property="og:title" content="이번 주 블로그 언급 급상승 맛집 TOP 8">
<meta property="og:description" content="네이버 블로그 언급량 기준, 이번 주 가장 뜨는 맛집 TOP 8을 확인해보세요.">
<meta property="og:image" content="{OG_IMAGE_URL}">
<meta property="og:type" content="website">

<!-- 검색엔진 소유자 인증용 (Google Search Console / 네이버 서치어드바이저)
     GOOGLE_SITE_VERIFICATION / NAVER_SITE_VERIFICATION이 config.py에 없으면
     빈 문자열이라 아래 줄들은 그냥 빈 줄로 남는다 (에러 없음) -->
{f'<meta name="google-site-verification" content="{GOOGLE_SITE_VERIFICATION}">' if GOOGLE_SITE_VERIFICATION else ''}
{f'<meta name="naver-site-verification" content="{NAVER_SITE_VERIFICATION}">' if NAVER_SITE_VERIFICATION else ''}

<!-- 아래 <style> 블록은 전부 화면 디자인(색상/여백/글씨크기)만 담당한다.
     기능(데이터/로직)과는 무관하니, 디자인만 바꾸고 싶으면 이 안의 숫자/색상 값만
     조정하면 된다. -->
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
  .genuine-badge {{
    display: inline-block;
    font-size: 10px;
    font-weight: 700;
    padding: 2px 7px;
    border-radius: 999px;
    margin-left: 4px;
  }}
  .genuine-high {{
    background: #e6f7ee;
    color: #0f8a4f;
  }}
  .genuine-mid {{
    background: #fff4e0;
    color: #b8720a;
  }}
  .genuine-low {{
    background: #fdeaea;
    color: #c92a2a;
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

  /* --- 랜덤 뽑기 버튼 & 슬롯머신 애니메이션 & 결과 모달 --- */
  .pick-btn {{
    width: 100%;
    max-width: 560px;
    margin: 4px auto 0;
    display: block;
    border: none;
    background: linear-gradient(to right, #f43f5e, #fb923c);
    color: white;
    font-size: 14px;
    font-weight: 800;
    padding: 14px;
    border-radius: 14px;
    cursor: pointer;
  }}
  .pick-btn:disabled {{
    opacity: 0.6;
    cursor: default;
  }}
  /* 뽑는 중 카드가 하나씩 반짝이며 지나가는 효과 */
  .card.picking {{
    box-shadow: 0 0 0 3px #ff5a36;
    transform: scale(1.02);
    transition: box-shadow 0.05s, transform 0.05s;
  }}
  .pick-modal-overlay {{
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.55);
    align-items: center;
    justify-content: center;
    z-index: 999;
    padding: 20px;
  }}
  .pick-modal-overlay.active {{
    display: flex;
  }}
  .pick-modal-box {{
    background: white;
    border-radius: 20px;
    padding: 32px 24px;
    max-width: 320px;
    width: 100%;
    text-align: center;
    box-shadow: 0 20px 50px rgba(0,0,0,0.3);
  }}
  .pick-modal-label {{
    font-size: 13px;
    color: #999;
    font-weight: 700;
  }}
  .pick-modal-name {{
    font-size: 24px;
    font-weight: 800;
    margin: 10px 0 22px;
    word-break: keep-all;
  }}
  .pick-modal-buttons {{
    display: flex;
    gap: 8px;
  }}
  .pick-modal-map-btn {{
    flex: 1;
    background: #ff5a36;
    color: white;
    text-decoration: none;
    padding: 12px;
    border-radius: 12px;
    font-size: 14px;
    font-weight: 700;
  }}
  .pick-modal-close-btn {{
    flex: 1;
    background: #f1f1f1;
    color: #666;
    border: none;
    padding: 12px;
    border-radius: 12px;
    font-size: 14px;
    font-weight: 700;
    cursor: pointer;
  }}
</style>
</head>
<body>
  <!-- 상단 그라데이션 헤더 영역 (제목, 배지, 지역 태그, 날짜) -->
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
  <!-- 탭 버튼들 (전체/지역랭킹/지역별) - 위에서 만들어둔 tab_buttons_html이 여기 들어감 -->
  <div class="tabs">
    {tab_buttons_html}
  </div>
  <p class="filter-note">협찬·광고·체험단 추정 게시물 {total_filtered}건 제외 후 집계</p>
  <!-- 탭 내용물 (카드 목록들) - tab_panels_html이 여기 들어감.
       JS가 탭 버튼 클릭에 맞춰 이 중 하나만 보이게(active) 전환해준다 -->
  <div class="list">
    {tab_panels_html}
  </div>
  <!-- 랜덤 뽑기 결과를 보여주는 팝업(모달). 평소엔 숨겨져 있다가(display:none)
       뽑기가 끝나면 JS가 display:flex로 바꿔서 화면 중앙에 띄운다 -->
  <div id="pick-modal" class="pick-modal-overlay" onclick="if(event.target===this) closePickModal()">
    <div class="pick-modal-box">
      <div class="pick-modal-label">오늘 당신의 픽은</div>
      <div class="pick-modal-name" id="pick-modal-name">-</div>
      <div class="pick-modal-buttons">
        <a id="pick-modal-map" href="#" target="_blank" rel="noopener" class="pick-modal-map-btn">지도에서 보기</a>
        <button class="pick-modal-close-btn" onclick="closePickModal()">닫기</button>
      </div>
    </div>
  </div>
  <!-- 탭 전환 기능: 버튼 클릭 시 모든 탭/버튼의 active를 지우고,
       클릭된 것에만 다시 active를 붙여서 그 내용만 보이게 만든다 -->
  <script>
    document.querySelectorAll('.tab-btn').forEach(function(btn) {{
      btn.addEventListener('click', function() {{
        document.querySelectorAll('.tab-btn').forEach(function(b) {{ b.classList.remove('active'); }});
        document.querySelectorAll('.tab-panel').forEach(function(p) {{ p.classList.remove('active'); }});
        btn.classList.add('active');
        document.getElementById(btn.dataset.tab).classList.add('active');
      }});
    }});

    // "오늘 메뉴 랜덤 추천" 버튼 클릭 시 실행되는 슬롯머신 애니메이션.
    // 버튼이 속한 탭(panel) 안의 식당 카드들만 대상으로 무작위 하나를 고른다.
    function runRandomPick(btn) {{
      var panel = btn.closest('.tab-panel');
      var cards = Array.prototype.slice.call(panel.querySelectorAll('.card'));
      if (cards.length === 0) return;

      btn.disabled = true;  // 애니메이션 도는 동안 중복 클릭 방지

      var finalIndex = Math.floor(Math.random() * cards.length);
      var loops = 3;  // 최종 결과가 나오기 전에 카드 목록을 몇 바퀴 훑을지
      var totalSteps = loops * cards.length + finalIndex;
      var counter = 0;

      var interval = setInterval(function() {{
        cards.forEach(function(c) {{ c.classList.remove('picking'); }});
        var idx = counter % cards.length;
        cards[idx].classList.add('picking');
        counter++;

        if (counter > totalSteps) {{
          clearInterval(interval);
          btn.disabled = false;
          showPickModal(cards[finalIndex]);
        }}
      }}, 80);
    }}

    function showPickModal(card) {{
      var nameEl = card.querySelector('.name');
      // .name 안에는 이름 텍스트 + 지도 아이콘(span)이 같이 있어서,
      // 첫 번째 텍스트 노드(이름 부분)만 뽑아서 보여준다
      var name = nameEl.childNodes[0].textContent.trim();
      document.getElementById('pick-modal-name').textContent = name;
      document.getElementById('pick-modal-map').href = card.href;
      document.getElementById('pick-modal').classList.add('active');
    }}

    function closePickModal() {{
      document.getElementById('pick-modal').classList.remove('active');
      document.querySelectorAll('.card.picking').forEach(function(c) {{ c.classList.remove('picking'); }});
    }}
  </script>
</body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n완료: {out_path} 생성됨 (탭 {len(tab_names)}개: {', '.join(tab_names)})")


if __name__ == "__main__":
    # 실제 실행 순서 (python fetch_and_build.py 했을 때 위에서 아래로 벌어지는 일):

    # 1) 이번 실행에서 쓸 지역 목록을 확정한다.
    #    CANDIDATE_REGIONS를 설정 안 했으면 그냥 REGIONS를 그대로 쓰고,
    #    설정했으면 CORE_REGIONS + 화제성 상위 지역을 합쳐서 새로 만든다.
    REGIONS = resolve_active_regions()

    # 2) 확정된 지역들을 돌면서 식당 후보 수집 -> 언급 수 집계 -> 급상승 순위 계산
    all_results, total_filtered = build_ranking()

    # 3) 식당 랭킹과는 별개로, 지역 단위 랭킹도 따로 집계
    region_ranking = build_region_ranking(all_results)

    # 4) "전체" / "지역랭킹" / 지역별 탭 데이터를 하나의 구조로 정리
    tabs = build_tabs(all_results, region_ranking)

    # 5) 위 데이터를 실제 웹페이지(index.html)로 만들어서 저장
    render_html(tabs, total_filtered)

    # 원본 데이터도 별도로 저장 (검증/디버깅용 - 나중에 "왜 이 순위가 나왔지?" 확인할 때 유용)
    with open("top8_raw.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    # 검색로봇 접근 허용 안내 파일 생성 (네이버 서치어드바이저 robots.txt 경고 해소용)
    with open("robots.txt", "w", encoding="utf-8") as f:
        f.write("User-agent: *\nAllow: /\n")
    print("완료: robots.txt 생성됨")
