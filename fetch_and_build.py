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
        from config import TRUST_WEIGHTED_RANKING
    except ImportError:
        # True로 켜면, 순위를 정할 때 growth(증가폭)를 내돈내산 지수로 보정한
        # "신뢰도 가중 점수"를 쓴다. 기본값 False = 기존처럼 growth 그대로 사용 (하위 호환)
        TRUST_WEIGHTED_RANKING = False
    try:
        from config import STREAK_MIN_DAYS
    except ImportError:
        STREAK_MIN_DAYS = 3  # 며칠 연속 급상승이어야 "연속 상승" 배지를 보여줄지
    try:
        from config import STREAK_HISTORY_FILE
    except ImportError:
        STREAK_HISTORY_FILE = "streak_history.json"  # 연속 상승 기록을 저장할 파일명
    try:
        from config import DATALAB_ENABLED
    except ImportError:
        DATALAB_ENABLED = False  # True로 켜면 데이터랩(검색어 트렌드)으로 검색 관심도 배지를 추가함
    try:
        from config import DATALAB_MAX_ITEMS
    except ImportError:
        DATALAB_MAX_ITEMS = 8  # "전체" 탭 상위 몇 개까지만 데이터랩을 확인할지 (일일 호출 한도 절약용)
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
    try:
        from config import REGION_QUERY_VARIANTS
    except ImportError:
        # 지역검색 API가 한 번에 최대 5건만 주기 때문에, 검색어를 바꿔가며 여러 번
        # 호출해서 후보를 모은다. config.py에 없으면 기본으로 이 목록을 쓴다.
        REGION_QUERY_VARIANTS = [
            "맛집", "맛집 추천", "인기 맛집", "숨은 맛집", "맛집 웨이팅",
            "한식 맛집", "카페", "고기 맛집", "일식 맛집", "술집",
        ]
except ImportError:
    # CLIENT_ID 등 "필수" 설정값 import 자체가 실패했다는 뜻 = config.py가 아예 없음
    raise SystemExit(
        "config.py가 없습니다. config.example.py를 config.py로 복사한 뒤 "
        "네이버 API 키와 지역 목록을 입력하세요."
    )

NAVER_API_BASE = "https://openapi.naver.com/v1/search"
NAVER_DATALAB_URL = "https://openapi.naver.com/v1/datalab/search"  # 검색어 트렌드는 이 별도 주소를 씀
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


def _naver_datalab_post(body: dict) -> dict:
    """
    데이터랩(검색어 트렌드) API 호출. 지역검색/블로그검색과 달리
    GET이 아니라 POST + JSON 본문을 쓰는 방식이라 별도 함수로 분리했다.
    """
    data_bytes = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(NAVER_DATALAB_URL, data=data_bytes, method="POST")
    req.add_header("X-Naver-Client-Id", CLIENT_ID)
    req.add_header("X-Naver-Client-Secret", CLIENT_SECRET)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp_body = resp.read().decode("utf-8")
    time.sleep(REQUEST_DELAY_SEC)
    return json.loads(resp_body)


def strip_tags(text: str) -> str:
    """
    네이버 API 응답 텍스트에는 검색어 강조용 <b> 태그와 &amp; 같은 HTML 엔티티가
    섞여서 온다. 이걸 순수 텍스트로 정리해주는 함수.
    """
    return unescape(re.sub(r"<[^>]+>", "", text)).strip()


def simplify_category(raw_category: str) -> str:
    """
    네이버 지역검색 API가 주는 카테고리는 "음식점>한식>육류,고기요리"처럼
    계층형 문자열이다. 이걸 화면에 필터 버튼으로 쓸 수 있는 단순한 이름으로 바꾼다.
    목록에 없는 새로운 업종이 나오면 "기타"로 분류된다.
    """
    text = raw_category or ""
    checks = [
        ("카페", "카페"), ("디저트", "카페"), ("베이커리", "카페"),
        ("술집", "술집"), ("호프", "술집"), ("포차", "술집"), ("바", "술집"),
        ("한식", "한식"), ("일식", "일식"), ("중식", "중식"),
        ("양식", "양식"), ("치킨", "치킨"), ("고기", "고기"),
        ("분식", "분식"), ("패스트푸드", "패스트푸드"),
    ]
    for keyword, label in checks:
        if keyword in text:
            return label
    return "기타"


def get_candidate_restaurants(region: str, target_count: int = 20) -> list:
    """
    지역검색 API로 후보 식당 목록을 가져온다.

    주의: 네이버 지역검색 API는 한 번 호출에 display가 최대 5건까지만 나오고
    (그 이상 요청해도 5건으로 잘림), start로도 다음 페이지를 못 가져온다(공식 제한).
    그래서 "{region} 맛집" 한 번만 호출하면 지역당 후보가 5개로 묶여버려서,
    이후 언급수/내돈내산 필터를 거치면 지역별 결과가 8개도 안 되는 경우가 생긴다.

    이를 피하려고 REGION_QUERY_VARIANTS("맛집", "맛집 추천", "카페" 등)를 순서대로
    붙여가며 여러 번 호출하고, 이름 기준으로 중복 제거해서 합친다.
    target_count(=DISPLAY_PER_REGION)에 도달하면 그만 호출한다.

    예: get_candidate_restaurants("강남") -> [{"name": "OO식당", "category": "한식"}, ...]
    """
    results = []
    seen = set()
    for variant in REGION_QUERY_VARIANTS:
        if len(results) >= target_count:
            break
        data = _naver_get("local.json", {
            "query": f"{region} {variant}",
            "display": 5,  # API 최대치. 5보다 크게 요청해도 어차피 5건까지만 온다.
            "sort": "random",  # 매번 똑같은 상위 업체만 나오지 않도록 무작위 정렬
        })
        for item in data.get("items", []):
            name = strip_tags(item["title"])
            if name and name not in seen:  # 같은 이름 중복 제거 (검색어가 겹쳐서 나오는 경우 방지)
                seen.add(name)
                category = simplify_category(item.get("category", ""))
                results.append({"name": name, "category": category})
    return results


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


def apply_streaks(results: list, today: datetime.date) -> None:
    """
    각 식당이 "며칠 연속" 급상승 목록(growth > 0)에 들었는지 계산해서
    results의 각 항목에 "streak" 키를 채워 넣는다 (제자리에서 수정, 반환값 없음).

    기록은 STREAK_HISTORY_FILE(기본 streak_history.json)에 저장해서 다음 실행 때도
    이어서 셀 수 있게 한다. 로컬에서 매번 실행하면 파일이 계속 쌓이지만,
    GitHub Actions에서는 이 파일을 저장소에 다시 커밋해야 실행 사이에 기록이
    유지된다 (workflow 파일에 그 커밋 단계가 포함되어 있다).
    """
    history = {}
    if os.path.exists(STREAK_HISTORY_FILE):
        try:
            with open(STREAK_HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError):
            history = {}  # 파일이 깨져 있으면 그냥 새로 시작 (에러로 죽지 않게)

    yesterday_str = (today - datetime.timedelta(days=1)).isoformat()
    today_str = today.isoformat()

    for r in results:
        name = r["name"]
        if r["growth"] <= 0:
            r["streak"] = 0
            continue  # 오늘 안 올랐으면 연속 기록 대상이 아님

        prev = history.get(name)
        if prev and prev.get("last_date") == yesterday_str:
            streak = prev.get("streak", 0) + 1  # 어제도 상승 중이었다 -> 연속 기록 이어감
        else:
            streak = 1  # 어제는 기록이 없거나 끊겼다 -> 오늘부터 새로 시작
        history[name] = {"last_date": today_str, "streak": streak}
        r["streak"] = streak

    # 기록 파일이 무한정 커지지 않도록, 최근 14일 안에 갱신 안 된 식당은 정리
    cutoff = (today - datetime.timedelta(days=14)).isoformat()
    history = {name: v for name, v in history.items() if v.get("last_date", "") >= cutoff}

    with open(STREAK_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def apply_search_trends(results: list, today: datetime.date) -> None:
    """
    상위 DATALAB_MAX_ITEMS개 식당에 한해, 데이터랩(검색어 트렌드) API로
    "실제 검색 관심도가 오르는 중인지"를 추가로 확인해 "search_rising" 키를 채운다.
    (블로그 포스팅 수와 달리, 이건 사람들이 네이버에 그 이름을 직접 검색한 지표라
    훨씬 직접적인 관심도 신호다. 다만 하루 호출 한도가 1,000회로 작아서,
    전체가 아니라 상위 일부에만 적용한다)

    데이터랩은 한 번 호출에 최대 5개 키워드 그룹을 같이 물어볼 수 있어서,
    5개씩 묶어(batch) 호출 횟수를 최대한 아낀다.
    """
    if not DATALAB_ENABLED or not results:
        return

    targets = results[:DATALAB_MAX_ITEMS]
    this_week_start = today - datetime.timedelta(days=7)

    # 5개씩 묶어서 처리 (데이터랩 API 한 번 호출당 최대 5개 키워드 그룹 허용)
    for i in range(0, len(targets), 5):
        batch = targets[i:i + 5]
        keyword_groups = [{"groupName": r["name"], "keywords": [r["name"]]} for r in batch]
        try:
            data = _naver_datalab_post({
                "startDate": (today - datetime.timedelta(days=13)).isoformat(),
                "endDate": today.isoformat(),
                "timeUnit": "date",
                "keywordGroups": keyword_groups,
            })
        except Exception as e:
            print(f"    (데이터랩 조회 실패, 이 배치는 건너뜀: {e})")
            continue

        for r, result_block in zip(batch, data.get("results", [])):
            this_week_sum = 0.0
            last_week_sum = 0.0
            for point in result_block.get("data", []):
                try:
                    point_date = datetime.datetime.strptime(point["period"], "%Y-%m-%d").date()
                except (ValueError, KeyError):
                    continue
                if point_date >= this_week_start:
                    this_week_sum += point.get("ratio", 0)
                else:
                    last_week_sum += point.get("ratio", 0)

            if this_week_sum == 0 and last_week_sum == 0:
                r["search_rising"] = None  # 검색량 자체가 거의 없어 판단 불가 -> 배지 표시 안 함
            else:
                r["search_rising"] = this_week_sum > last_week_sum
            print(f"    (데이터랩: {r['name']} 검색 관심도 {'상승' if r['search_rising'] else '유지/하락' if r['search_rising'] is not None else '데이터 부족'})")


def build_ranking() -> tuple:
    """전 지역을 순회하며 급상승 맛집 TOP N을 계산. (결과 리스트, 협찬/광고 추정 총 제외 건수)를 반환"""
    today = datetime.date.today()
    results = []
    seen_names = set()
    total_filtered = 0

    for region in REGIONS:
        print(f"[지역검색] {region} 맛집 후보 수집 중...")
        candidates = get_candidate_restaurants(region, DISPLAY_PER_REGION)

        for candidate in candidates:
            name = candidate["name"]
            category = candidate["category"]
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

            # 신뢰도 가중 점수: growth(증가폭)에 내돈내산 지수를 곱해서 보정한 값.
            # 지수가 낮을수록(애매한 후기 비율이 높을수록) 실제 순위에서 페널티를 받는다.
            # 표본 부족으로 지수가 없으면(None) 판단 근거가 없으니 그대로(가중치 1.0) 둔다.
            score = growth * (genuine_ratio / 100 if genuine_ratio is not None else 1.0)

            results.append({
                "name": name,
                "region": region,
                "category": category,
                "this_week": this_week,
                "last_week": last_week,
                "growth": growth,
                "filtered": filtered,
                "genuine_ratio": genuine_ratio,  # None이면 표본 부족 -> 화면에 배지 안 뜸
                "score": score,
            })
            ratio_note = f", 내돈내산 {genuine_ratio}%" if genuine_ratio is not None else ""
            print(f"  - {name}: 이번주 {this_week} / 지난주 {last_week} (증가 {growth}{ratio_note})")

    # TRUST_WEIGHTED_RANKING이 켜져 있으면 score(신뢰도 보정 점수)로,
    # 꺼져 있으면 기존처럼 growth(증가폭) 그대로 정렬 기준으로 쓴다.
    sort_key = "score" if TRUST_WEIGHTED_RANKING else "growth"
    results.sort(key=lambda x: (x[sort_key], x["this_week"]), reverse=True)

    # 며칠 연속으로 급상승 목록에 들었는지 계산 (streak_history.json에 기록을 남겨서
    # 다음 실행 때도 이어서 셀 수 있게 한다 - GitHub Actions에서는 이 파일을
    # 저장소에 다시 커밋해야 실행 간에 기록이 유지된다)
    apply_streaks(results, today)

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
    카테고리(data-category)는 JS 필터 버튼이, 즐겨찾기 버튼은 로컬 저장(localStorage)을 쓴다.
    """
    if not items:
        return '<p style="text-align:center;color:#999;padding:20px 0;">데이터가 없습니다.</p>'

    # 이 탭에 실제로 등장하는 카테고리 목록을 모아서 필터 버튼을 만든다
    categories = sorted(set(r.get("category", "기타") for r in items))
    category_filter_html = '<button class="cat-btn active" data-cat="전체" onclick="filterByCategory(this)">전체</button>'
    for cat in categories:
        category_filter_html += f'<button class="cat-btn" data-cat="{cat}" onclick="filterByCategory(this)">{cat}</button>'

    # 정렬 미니탭: 급상승순(기본)/언급많은순/진짜후기순. 서버를 다시 호출하지 않고
    # 지금 화면에 이미 그려진 카드들을 JS로 재배열하는 방식이라 추가 API 호출이 없다.
    sort_bar_html = (
        '<div class="sort-bar">'
        '<button class="sort-btn active" data-sort="rankmetric" onclick="sortByMetric(this)">🔥 급상승순</button>'
        '<button class="sort-btn" data-sort="thisweek" onclick="sortByMetric(this)">💬 언급많은순</button>'
        '<button class="sort-btn" data-sort="genuine" onclick="sortByMetric(this)">✅ 진짜후기순</button>'
        '</div>'
    )

    rows_html = ""
    for i, r in enumerate(items, start=1):
        growth_badge = f"+{r['growth']}" if r["growth"] > 0 else str(r["growth"])
        map_url = naver_map_link(r["name"], r["region"])
        category = r.get("category", "기타")
        fav_key = urllib.parse.quote(r["name"])  # localStorage 키에 안전하게 쓰기 위해 인코딩
        share_name = urllib.parse.quote(r["name"])  # 공유 버튼에서도 같은 방식으로 이름을 안전하게 담아둔다

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

        # 연속 상승 배지: STREAK_MIN_DAYS(기본 3일) 이상 연속으로 올랐을 때만 표시
        streak = r.get("streak", 0)
        streak_badge_html = f'<span class="streak-badge">🔥 {streak}일 연속</span>' if streak >= STREAK_MIN_DAYS else ""

        # 검색 관심도 배지: DATALAB_ENABLED가 켜져있고, 실제로 검색량도 오르는 중일 때만 표시
        # (search_rising이 None이면 "검색량 자체가 거의 없어 판단 불가"라는 뜻이라 배지를 안 보여준다.
        # False면 "블로그 글은 늘었지만 실제 검색 관심도는 그대로/하락"이라는 뜻이라 이것도 안 보여준다 -
        # 굳이 부정적인 신호를 배지로 강조할 필요는 없어서, 긍정적일 때만 표시)
        search_badge_html = (
            '<span class="search-badge">🔍 검색 관심도 상승</span>'
            if r.get("search_rising") is True else ""
        )

        # 정렬 미니탭용 data 속성. "진짜후기순"에서 표본 부족(genuine_ratio=None)인
        # 카드는 -1을 넣어 항상 맨 아래로 가도록 한다.
        genuine_for_sort = genuine_ratio if genuine_ratio is not None else -1

        # "급상승순" 정렬 기준값. 서버가 실제로 정렬에 쓴 값과 반드시 일치시켜야
        # (그래야 첫 화면 순서 == "급상승순" 버튼을 눌렀을 때 순서가 같아짐)
        # TRUST_WEIGHTED_RANKING이 켜져 있으면 build_ranking()에서 score(신뢰도 보정
        # 점수)로 정렬했으므로 여기서도 score를 써야 한다. 꺼져 있으면 growth 그대로.
        # (배지에 보이는 "+N" 숫자는 항상 순수 growth를 그대로 보여준다 - 안 바뀜)
        rank_metric_value = r["score"] if TRUST_WEIGHTED_RANKING else r["growth"]

        rows_html += f"""
        <a class="card" data-category="{category}" data-rankmetric="{rank_metric_value}"
           data-thisweek="{r['this_week']}" data-genuine="{genuine_for_sort}"
           href="{map_url}" target="_blank" rel="noopener">
          <button class="share-btn" data-name="{share_name}" data-map="{map_url}"
                  onclick="event.preventDefault(); event.stopPropagation(); shareCard(this);">🔗</button>
          <button class="fav-btn" data-key="{fav_key}"
                  onclick="event.preventDefault(); event.stopPropagation(); toggleFavorite(this);">♡</button>
          <div class="rank">{i}</div>
          <div class="info">
            <div class="name">{r['name']} <span class="map-icon">📍</span></div>
            <div class="region">{r['region']} · {category} {genuine_badge_html}</div>
            {search_badge_html}
          </div>
          <div class="stats">
            <span class="growth">{growth_badge}</span>
            {streak_badge_html}
            <span class="count">이번 주 {r['this_week']}건 · 지난 주 {r['last_week']}건</span>
          </div>
        </a>"""

    # 랜덤 뽑기 버튼은 카드 정렬 대상(card-list) 밖에 별도로 둬서, 정렬 시 카드들이
    # 재배치돼도 이 버튼은 항상 맨 아래 고정된다.
    pick_btn_html = '<button class="pick-btn" onclick="runRandomPick(this)">🎰 오늘 메뉴 랜덤 추천</button>'

    return (
        f'<div class="cat-filter">{category_filter_html}</div>'
        + sort_bar_html
        + f'<div class="card-list">{rows_html}</div>'
        + pick_btn_html
    )


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
    # "N분 전 갱신" 표시용 생성 시각. GitHub Actions는 UTC로 돌아가므로 KST(UTC+9)로
    # 변환해서 저장하고, 실제 "지금으로부터 몇 분 전"인지는 브라우저에서 JS로 계산한다
    # (그래야 페이지를 열어둔 채로 시간이 지나도 값이 계속 갱신된다).
    generated_at_kst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    generated_at_iso = generated_at_kst.strftime("%Y-%m-%dT%H:%M:%S+09:00")
    region_tags = " ".join(f"#{r}" for r in REGIONS)  # 헤더에 보이는 "#강남 #성수..." 문구
    # EXTRA_BADGES 리스트에 있는 문구들을 헤더 배지로 하나씩 만든다 (몇 개든 가능)
    extra_badges_html = "".join(
        f'<span class="hero-badge-secondary">{badge}</span>' for badge in EXTRA_BADGES
    )
    overall = tabs.get("전체", [])
    top_n = len(overall) if overall else TOP_N  # 헤더의 "TOP N" 숫자

    # --- SEO 개선 ② 구조화 데이터(JSON-LD) -------------------------------------
    # 검색엔진(특히 Google)이 페이지 내용을 명확히 이해하도록 도와주는 공식 규격.
    # "이 페이지는 식당 목록이고, 각 항목은 이런 이름/지역이다"를 기계가 읽을 수 있는
    # 형태로 명시한다. 화면에는 안 보이지만, 숨겨서 속이는 게 아니라 검색엔진 전용으로
    # 제공하는 정식 메타데이터라 클로킹 문제가 없다 (Google이 공식 지원하는 방식).
    structured_data = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": "이번 주 블로그 언급 급상승 맛집 TOP 8",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i,
                "item": {
                    "@type": "Restaurant",
                    "name": r["name"],
                    "areaServed": r["region"],
                    "servesCuisine": r.get("category", "기타"),
                },
            }
            for i, r in enumerate(overall, start=1)
        ],
    }
    structured_data_json = json.dumps(structured_data, ensure_ascii=False)

    # 하단 안내 문구: 협찬 제외 건수는 항상 표시하고, 내돈내산 지수 기준으로
    # 필터링하는 기능(MIN_GENUINE_RATIO_TO_SHOW)이 켜져 있으면 그 기준도 같이 안내한다
    filter_note = f"협찬·광고·체험단 추정 게시물 {total_filtered}건 제외"
    if MIN_GENUINE_RATIO_TO_SHOW is not None:
        filter_note += f" · 내돈내산 지수 {MIN_GENUINE_RATIO_TO_SHOW}% 이상 게시글로만 집계"

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
        # data-tabname: 공유 버튼(shareCard)이 "지금 어느 탭인지"를 알아야
        # "부산 급상승순 1위 - ..." 처럼 탭 이름을 공유 문구에 넣을 수 있다
        tab_panels_html += f'<div class="tab-panel{active_panel}" id="tab-{idx}" data-tabname="{name}">{panel_content}</div>'

    # "즐겨찾기" 탭은 서버(파이썬)가 아니라 브라우저(localStorage)가 아는 정보라서
    # 여기서는 빈 틀만 만들어두고, 실제 내용은 페이지가 열릴 때 JS가 채워 넣는다
    # (renderFavoritesTab 함수가 담당 - 다른 탭에 이미 그려진 카드들 중
    # 즐겨찾기 표시된 것만 모아서 이 탭 안에 복사해 넣는 방식)
    tab_buttons_html += '<button class="tab-btn" data-tab="tab-favorites">♥ 즐겨찾기</button>'
    tab_panels_html += (
        '<div class="tab-panel" id="tab-favorites" data-tabname="즐겨찾기">'
        '<p style="text-align:center;color:#999;padding:20px 0;">'
        '아직 즐겨찾기한 맛집이 없어요. 카드의 ♡를 눌러보세요.</p></div>'
    )

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

<!-- 구조화 데이터(JSON-LD): 검색엔진에게 "이 페이지는 식당 목록이다"를
     기계가 읽을 수 있는 형태로 명시한다. 화면에는 안 보이지만, Google이 공식
     지원하는 표준 메타데이터라 클로킹(속임수)이 아니다. -->
<script type="application/ld+json">
{structured_data_json}
</script>

<!-- PWA(홈 화면에 설치 가능한 웹앱) 설정.
     apple-touch-icon은 반드시 PNG여야 아이폰에서 정상 표시된다 (SVG는 깨짐). -->
<link rel="manifest" href="./manifest.json">
<meta name="theme-color" content="#ff5a36">
<link rel="icon" href="./icon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="./apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="맛집트렌드">

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
  .card-list {{
    display: flex;
    flex-direction: column;
    gap: 10px;
  }}
  .card {{
    background: white;
    border-radius: 14px;
    padding: 16px 66px 16px 18px;
    display: flex;
    align-items: center;
    gap: 14px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    text-decoration: none;
    color: inherit;
    transition: box-shadow 0.15s;
    position: relative;
  }}
  .card.cat-hidden {{
    display: none;
  }}
  .card:hover {{
    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
  }}
  .fav-btn {{
    position: absolute;
    top: 10px;
    right: 10px;
    width: 22px;
    height: 22px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: none;
    border: none;
    font-size: 16px;
    color: #ccc;
    cursor: pointer;
    padding: 0;
    line-height: 1;
  }}
  .fav-btn.active {{
    color: #ff5a36;
  }}
  .share-btn {{
    position: absolute;
    top: 10px;
    right: 38px;
    width: 22px;
    height: 22px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: none;
    border: none;
    font-size: 14px;
    color: #ccc;
    cursor: pointer;
    padding: 0;
    line-height: 1;
  }}
  .share-btn:hover {{
    color: #666;
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
  .streak-badge {{
    font-size: 10px;
    font-weight: 700;
    color: #d9376e;
    background: #fdeef3;
    padding: 2px 7px;
    border-radius: 999px;
    display: inline-block;
  }}
  .search-badge {{
    font-size: 10px;
    font-weight: 700;
    color: #0f6e9c;
    background: #e6f4fb;
    padding: 2px 7px;
    border-radius: 999px;
    display: inline-block;
    margin-top: 4px;
  }}
  .cat-filter {{
    display: flex;
    gap: 6px;
    overflow-x: auto;
    padding-bottom: 4px;
    margin-bottom: 2px;
  }}
  .cat-btn {{
    flex: 0 0 auto;
    border: none;
    background: #eee;
    color: #666;
    font-size: 12px;
    font-weight: 700;
    padding: 6px 14px;
    border-radius: 999px;
    cursor: pointer;
  }}
  .cat-btn.active {{
    background: #333;
    color: white;
  }}
  .sort-bar {{
    display: flex;
    gap: 6px;
    overflow-x: auto;
    padding-bottom: 4px;
    margin-bottom: 10px;
  }}
  .sort-btn {{
    flex: 0 0 auto;
    border: 1px solid #eee;
    background: white;
    color: #666;
    font-size: 12px;
    font-weight: 700;
    padding: 6px 14px;
    border-radius: 999px;
    cursor: pointer;
  }}
  .sort-btn.active {{
    background: #fff0eb;
    border-color: #ff5a36;
    color: #ff5a36;
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
  <div class="hero" data-generated-at="{generated_at_iso}">
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
        <span class="hero-date">{today_str} · <span id="update-relative">방금 갱신됨</span></span>
      </div>
    </div>
  </div>
  <!-- 탭 버튼들 (전체/지역랭킹/지역별) - 위에서 만들어둔 tab_buttons_html이 여기 들어감 -->
  <div class="tabs">
    {tab_buttons_html}
  </div>
  <p class="filter-note">{filter_note}</p>
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
    // 버튼이 속한 탭(panel) 안의 식당 카드들 중, 카테고리 필터로 숨겨지지 않은
    // (지금 화면에 실제로 보이는) 카드만 대상으로 무작위 하나를 고른다.
    function runRandomPick(btn) {{
      var panel = btn.closest('.tab-panel');
      var cards = Array.prototype.slice.call(panel.querySelectorAll('.card:not(.cat-hidden)'));
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

    // --- 카테고리 필터: 버튼 클릭 시 그 탭 안에서 해당 카테고리만 보이게 전환 ---
    function filterByCategory(btn) {{
      var filterBar = btn.closest('.cat-filter');
      var panel = btn.closest('.tab-panel');
      var selectedCat = btn.dataset.cat;

      filterBar.querySelectorAll('.cat-btn').forEach(function(b) {{ b.classList.remove('active'); }});
      btn.classList.add('active');

      panel.querySelectorAll('.card').forEach(function(card) {{
        if (selectedCat === '전체' || card.dataset.category === selectedCat) {{
          card.classList.remove('cat-hidden');
        }} else {{
          card.classList.add('cat-hidden');
        }}
      }});
    }}

    // --- 정렬 미니탭: 급상승순/언급많은순/진짜후기순 - 서버 재호출 없이 이미 그려진
    // 카드들을 data-growth/data-thisweek/data-genuine 값 기준으로 다시 배열한다 ---
    function sortByMetric(btn) {{
      var bar = btn.closest('.sort-bar');
      var panel = btn.closest('.tab-panel');
      var metric = btn.dataset.sort; // 'growth' | 'thisweek' | 'genuine'

      bar.querySelectorAll('.sort-btn').forEach(function(b) {{ b.classList.remove('active'); }});
      btn.classList.add('active');

      var list = Array.prototype.slice.call(panel.querySelectorAll('.card-list .card'));
      list.sort(function(a, b) {{
        var av = parseFloat(a.dataset[metric]) || 0;
        var bv = parseFloat(b.dataset[metric]) || 0;
        return bv - av; // 내림차순 (큰 값이 위로)
      }});
      var container = panel.querySelector('.card-list');
      if (container) {{
        list.forEach(function(card) {{ container.appendChild(card); }});
      }}
    }}

    // --- 공유 버튼: "{{탭 이름}} {{정렬기준}}순 {{순위}}위 - {{이름}}\\n{{지도링크}}" 형태로 클립보드에 복사.
    // 순위/정렬기준은 정적으로 미리 박아두지 않고, 클릭하는 시점에 화면에 보이는 상태
    // (카테고리 필터로 숨겨졌는지, 어떤 정렬 버튼이 활성 상태인지)를 그대로 반영해서 계산한다.
    var SORT_LABELS = {{ rankmetric: '급상승순', thisweek: '언급많은순', genuine: '진짜후기순' }};

    function shareCard(btn) {{
      var card = btn.closest('.card');
      var panel = btn.closest('.tab-panel');
      var tabName = (panel && panel.dataset.tabname) || '';

      // 지금 활성화된 정렬 기준 (탭에 정렬바가 없으면 - 예: 즐겨찾기 탭 - 기본값 사용)
      var activeSortBtn = panel ? panel.querySelector('.sort-bar .sort-btn.active') : null;
      var sortLabel = activeSortBtn ? (SORT_LABELS[activeSortBtn.dataset.sort] || '급상승순') : '급상승순';

      // 지금 화면에 실제로 보이는(카테고리 필터로 숨겨지지 않은) 카드들 중 몇 번째인지 계산
      var visibleCards = panel
        ? Array.prototype.slice.call(panel.querySelectorAll('.card:not(.cat-hidden)'))
        : [card];
      var rank = visibleCards.indexOf(card) + 1;
      if (rank <= 0) rank = 1;

      var name = decodeURIComponent(btn.dataset.name);
      var text = tabName + ' ' + sortLabel + ' ' + rank + '위 - ' + name + '\\n' + btn.dataset.map;

      function showCopied() {{
        var original = btn.textContent;
        btn.textContent = '✅';
        setTimeout(function() {{ btn.textContent = original; }}, 1200);
      }}

      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(text).then(showCopied).catch(function() {{
          window.prompt('아래 내용을 복사하세요:', text);
        }});
      }} else {{
        window.prompt('아래 내용을 복사하세요:', text);
      }}
    }}

    // --- 즐겨찾기: 브라우저(localStorage)에 저장하므로 서버 없이도 기기별로 기억된다 ---
    function loadFavorites() {{
      try {{
        return JSON.parse(localStorage.getItem('naver_trend_favorites') || '{{}}');
      }} catch (e) {{
        return {{}};
      }}
    }}

    // querySelector의 속성값 안에 "나 \가 들어가면 선택자가 깨지므로 직접 이스케이프한다
    // (CSS.escape()는 일부 구형 브라우저/웹뷰에서 지원이 안 될 수 있어 이 방식을 씀)
    function escapeAttrValue(value) {{
      return String(value).replace(/["\\\\]/g, '\\\\$&');
    }}

    function toggleFavorite(btn) {{
      var key = btn.dataset.key;
      var favs = loadFavorites();
      if (favs[key]) {{
        delete favs[key];
      }} else {{
        favs[key] = true;
      }}
      localStorage.setItem('naver_trend_favorites', JSON.stringify(favs));

      // 같은 식당 카드가 "전체"/지역별 탭 등 여러 곳에 동시에 있을 수 있으므로,
      // 지금 누른 버튼 하나만이 아니라 같은 key를 가진 버튼을 전부 같이 갱신한다
      document.querySelectorAll('.fav-btn[data-key="' + escapeAttrValue(key) + '"]').forEach(function(b) {{
        if (favs[key]) {{
          b.textContent = '♥';
          b.classList.add('active');
        }} else {{
          b.textContent = '♡';
          b.classList.remove('active');
        }}
      }});

      renderFavoritesTab();
    }}

    // "즐겨찾기" 탭 안에 실제 카드를 채워 넣는 함수. 다른 탭에 이미 그려져 있는
    // 카드들 중, 지금 즐겨찾기 표시된 것만 골라 복사해서 즐겨찾기 탭에 넣는다.
    // (별도 데이터를 새로 안 만들고, 화면에 이미 있는 카드를 재사용하는 방식)
    function renderFavoritesTab() {{
      var panel = document.getElementById('tab-favorites');
      if (!panel) return;
      var favs = loadFavorites();
      var favKeys = Object.keys(favs).filter(function(k) {{ return favs[k]; }});

      if (favKeys.length === 0) {{
        panel.innerHTML = '<p style="text-align:center;color:#999;padding:20px 0;">'
          + '아직 즐겨찾기한 맛집이 없어요. 카드의 ♡를 눌러보세요.</p>';
        return;
      }}

      var addedKeys = {{}};
      var cardsHtml = '';
      // 즐겨찾기 탭 자신은 검색 대상에서 제외하고, 다른 탭의 카드들만 훑는다
      document.querySelectorAll('.tab-panel:not(#tab-favorites) .card').forEach(function(card) {{
        var favBtn = card.querySelector('.fav-btn');
        if (!favBtn) return;
        var key = favBtn.dataset.key;
        if (favs[key] && !addedKeys[key]) {{
          addedKeys[key] = true;
          var clone = card.cloneNode(true);
          // 복제본은 항상 "즐겨찾기된 상태"이므로 하트를 확실히 채워서 보여준다
          var cloneFavBtn = clone.querySelector('.fav-btn');
          cloneFavBtn.textContent = '♥';
          cloneFavBtn.classList.add('active');
          cardsHtml += clone.outerHTML;
        }}
      }});

      panel.innerHTML = cardsHtml || '<p style="text-align:center;color:#999;padding:20px 0;">'
        + '아직 즐겨찾기한 맛집이 없어요. 카드의 ♡를 눌러보세요.</p>';
    }}

    // 페이지를 열었을 때, 예전에 즐겨찾기 눌러뒀던 식당이 있으면 하트를 채워서 보여주고,
    // "즐겨찾기" 탭도 그 내용으로 미리 채워둔다
    (function restoreFavorites() {{
      var favs = loadFavorites();
      document.querySelectorAll('.fav-btn').forEach(function(btn) {{
        if (favs[btn.dataset.key]) {{
          btn.textContent = '♥';
          btn.classList.add('active');
        }}
      }});
      renderFavoritesTab();
    }})();

    // "N분 전 갱신" 표시: 데이터가 실제로 만들어진 시각(data-generated-at)과
    // 지금 브라우저 시각을 비교해서 매분 갱신한다. 페이지를 열어둔 채로 시간이
    // 지나도 "3분 전" -> "4분 전"처럼 계속 최신 상태로 바뀐다.
    (function updateRelativeTimeLoop() {{
      var heroEl = document.querySelector('.hero');
      var relEl = document.getElementById('update-relative');
      if (!heroEl || !relEl) return;
      var generatedAt = new Date(heroEl.dataset.generatedAt);
      if (isNaN(generatedAt.getTime())) return;

      function render() {{
        var diffMin = Math.floor((Date.now() - generatedAt.getTime()) / 60000);
        if (diffMin < 1) {{
          relEl.textContent = '방금 갱신됨';
        }} else if (diffMin < 60) {{
          relEl.textContent = diffMin + '분 전 갱신';
        }} else {{
          var diffHour = Math.floor(diffMin / 60);
          if (diffHour < 24) {{
            relEl.textContent = diffHour + '시간 전 갱신';
          }} else {{
            relEl.textContent = Math.floor(diffHour / 24) + '일 전 갱신';
          }}
        }}
      }}
      render();
      setInterval(render, 60000);
    }})();

    // PWA: 홈 화면에 설치 가능하게 만들어주는 서비스 워커 등록.
    // (등록 자체는 여기서 하고, 실제 캐싱 없음 여부는 sw.js 파일 내용이 결정한다)
    if ('serviceWorker' in navigator) {{
      navigator.serviceWorker.register('./sw.js');
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

    # 2-1) (선택 기능, 기본 꺼짐) 상위 몇 개 식당만 데이터랩으로 실제 검색 관심도 추가 확인
    apply_search_trends(all_results, datetime.date.today())

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
