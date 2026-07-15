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
import sys
import urllib.parse
import urllib.error
from html import unescape, escape

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
            # "영수증 리뷰"는 업체가 영수증 인증 조건으로 대가를 주는 마케팅
            # 프로그램이라, 내돈내산이 아니라 협찬성으로 분류한다
            "영수증 리뷰", "영수증리뷰",
        ]
    try:
        from config import GENUINE_KEYWORDS
    except ImportError:
        # "진짜 내돈내산" 판별용 키워드 - 협찬 문구가 없다고 자동으로 진짜인 건 아니라서,
        # 반대로 이런 문구가 있으면 더 신뢰도 높은 후기로 본다.
        # 주의: "영수증"은 예전엔 여기 있었지만, "영수증 리뷰"라는 대가성 마케팅
        # 프로그램과 겹쳐 신뢰 신호로 쓰기 애매해져서 목록에서 뺐다 (위의
        # SPONSORED_KEYWORDS 참고). 단독 "영수증 인증"류 진짜 후기 일부를 잃지만,
        # 마케팅 글을 신뢰 후기로 오인하는 것보다 낫다는 판단.
        GENUINE_KEYWORDS = ["내돈내산", "내돈내먹", "카드내역", "직접 결제"]
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
        from config import GENUINE_SMOOTHING_K
    except ImportError:
        # 베이지안 스무딩 강도(가상 표본 수). 순위 점수(score)를 계산할 때
        # "표본이 적은 식당의 내돈내산 비율"을 전체 평균 쪽으로 끌어당기는 정도.
        # 값이 클수록 표본이 많아야 자기 비율을 온전히 인정받는다.
        GENUINE_SMOOTHING_K = 5
    try:
        from config import GENUINE_PRIOR_RATIO
    except ImportError:
        # 스무딩의 기준점(사전 확률). 데이터가 전혀 없을 때 가정하는 내돈내산
        # 비율로, 실측 분포(대부분 5~15%)의 중간값 근처인 10%를 기본값으로 쓴다.
        GENUINE_PRIOR_RATIO = 0.10
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
        from config import RISING_REGION_COUNT
    except ImportError:
        # 급상승 지역 전용 슬롯 수. 화제성 절대값 상위는 대형 상권이 상시 점유하기
        # 쉬우므로, 전일 대비 신규 게시물 성장률 상위 지역을 별도로 선발한다.
        RISING_REGION_COUNT = 0  # 0 = 기능 끔 (config에서 켜야 동작)
    try:
        from config import RISING_MIN_DAILY_POSTS
    except ImportError:
        RISING_MIN_DAILY_POSTS = 30  # 하루 신규 게시물 최소치 (소규모 노이즈 방지)
    try:
        from config import MINIFY_OUTPUT
    except ImportError:
        # 결과 HTML에서 통짜 주석 줄/들여쓰기를 벗겨 전송량 절감 (소스 주석은 유지)
        MINIFY_OUTPUT = True
    try:
        from config import QUALITY_GUARD_MIN_RATIO
    except ImportError:
        # 품질 게이트: 수집 식당 수가 직전 실행의 이 비율 미만이면(급감 = API
        # 부분 장애 신호) 배포를 중단하고 기존 페이지를 유지한다. 0 = 기능 끔.
        QUALITY_GUARD_MIN_RATIO = 0.3
    try:
        from config import SEOUL_REGIONS
    except ImportError:
        SEOUL_REGIONS = []  # 비어 있으면 서울 전용 쿼터/탭 기능 전체가 꺼짐 (하위 호환)
    try:
        from config import SEOUL_TOP_COUNT
    except ImportError:
        SEOUL_TOP_COUNT = 10  # 서울 후보 중 화제성 상위 몇 개 권역을 선정할지
    try:
        from config import REGION_VOLUME_CACHE_FILE
    except ImportError:
        # 지역 화제성(블로그 총 게시물 수)은 하루 안에 크게 변하지 않으므로,
        # 하루 1번만 실제 API로 조회하고 나머지 실행(2시간마다 총 12회)에서는
        # 이 캐시 파일을 재사용한다. -> 후보 지역 100개 기준 하루 약 1,100회의
        # API 호출을 절약 (streak_history.json처럼 저장소에 커밋해서 유지)
        REGION_VOLUME_CACHE_FILE = "region_volume_cache.json"
    try:
        from config import CANDIDATE_CACHE_FILE
    except ImportError:
        # 지역별 후보 식당 목록은 하루 안에 크게 바뀌지 않으므로, 하루 1번만
        # 실제 지역검색 API(local.json)로 수집하고 나머지 실행(2시간마다 총 12회)
        # 에서는 이 캐시를 재사용한다. -> 지역 15개 × 검색어 10개 기준
        # 하루 약 1,650회의 API 호출 절약 (region_volume_cache와 같은 패턴)
        CANDIDATE_CACHE_FILE = "candidate_cache.json"
    try:
        from config import TREND_HISTORY_FILE
    except ImportError:
        # 순위 변동(▲▼ 화살표)과 스파크라인(언급량 추세 미니그래프) 계산에 쓰이는
        # 실행 이력 파일. streak_history.json과 같은 방식으로 저장소에 커밋해서
        # 실행 사이에 기록이 유지된다.
        TREND_HISTORY_FILE = "trend_history.json"
    try:
        from config import SITE_URL
    except ImportError:
        # sitemap.xml / robots.txt의 Sitemap 안내에 쓰이는 사이트 주소
        SITE_URL = "https://naverblog-matzip-trend.github.io/"
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

KST = datetime.timezone(datetime.timedelta(hours=9))  # 한국 표준시


def kst_now() -> datetime.datetime:
    """현재 시각을 한국시간(KST) 기준으로 반환"""
    return datetime.datetime.now(KST)


def kst_today() -> datetime.date:
    """
    오늘 날짜를 한국시간(KST) 기준으로 반환.

    중요: GitHub Actions 서버는 UTC로 돌아가기 때문에 datetime.date.today()를
    그대로 쓰면 한국시간 00:00~09:00 사이 실행에서는 "어제" 날짜가 나온다.
    네이버 블로그의 postdate는 한국 날짜 기준이라, 이 상태로 집계하면
    "오늘(KST) 올라온 게시물"이 전부 미래 날짜로 취급되어 집계에서 빠지는
    버그가 생긴다 (01:45/03:45/05:45/07:45 KST 실행이 모두 해당).
    그래서 날짜 계산은 반드시 이 함수를 통해 KST 기준으로 한다.
    """
    return kst_now().date()


NAVER_API_BASE = "https://openapi.naver.com/v1/search"
NAVER_DATALAB_URL = "https://openapi.naver.com/v1/datalab/search"  # 검색어 트렌드는 이 별도 주소를 씀
REQUEST_DELAY_SEC = 0.15  # 네이버 API 과호출 방지용 딜레이


def _urlopen_with_retry(req) -> str:
    """
    일시적인 네트워크 오류에 대비한 재시도 공통 함수.
    GitHub Actions의 공유 IP 환경에서는 네이버 API가 가끔 429(과호출)나
    5xx(서버 일시 오류), 타임아웃을 돌려줄 수 있는데, 현재 구조에서는 요청
    1건만 실패해도 그 회차 빌드 전체가 죽는다. 그래서 0.5초 -> 1.0초 점진적
    대기 후 최대 3회까지 재시도한다.

    단, 401/403 같은 4xx 오류(429 제외)는 API 키 문제 등 "재시도해도 결과가
    똑같은" 오류라서 즉시 실패시킨다 (의미 없는 재시도로 원인 파악만 늦어짐).
    """
    max_retries = 3
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code != 429 and 400 <= e.code < 500:
                raise  # 키 오류/잘못된 요청 등은 재시도 무의미 -> 바로 실패
            last_error = e
        except (urllib.error.URLError, TimeoutError) as e:
            last_error = e  # 네트워크 단절/타임아웃 계열 -> 재시도 대상
        if attempt == max_retries:
            print(f"!!! [API 최종 실패] {req.full_url.split('?')[0]}: {last_error}")
            raise last_error
        # 429(과호출)는 "잠깐 쉬라"는 신호라서 0.5초로는 부족한 경우가 많다.
        # 429일 때만 3초 -> 6초로 넉넉히 쉬고, 그 외 일시 오류는 0.5초 -> 1.0초.
        is_rate_limited = isinstance(last_error, urllib.error.HTTPError) and last_error.code == 429
        backoff = (3.0 if is_rate_limited else 0.5) * attempt
        print(f"    [일시적 오류] {last_error} - {backoff}초 후 재시도 ({attempt}/{max_retries})")
        time.sleep(backoff)


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
    body = _urlopen_with_retry(req)  # 일시 오류 시 최대 3회 재시도
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
    resp_body = _urlopen_with_retry(req)  # 일시 오류 시 최대 3회 재시도
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

    하루 단위 캐시: "{지역} 맛집" 검색 결과 상위 목록은 2시간 사이에 거의 바뀌지
    않으므로, 오늘(KST) 이미 수집한 지역이면 API를 다시 부르지 않고 캐시를
    재사용한다. sort=random이라 "그날의 첫 실행"이 그날 하루의 후보 구성을
    결정하게 되는데, 날짜가 바뀌면 캐시가 새로 만들어지므로 날마다 다양성은
    그대로 유지된다. (지역별 블로그 언급량 집계는 캐시와 무관하게 매 실행마다
    새로 계산되므로, 2시간마다 갱신되는 순위의 신선도에는 영향이 없다)
    """
    # --- 캐시 확인: 오늘 이미 이 지역 후보를 수집했으면 그대로 재사용 ---------
    today_str = kst_today().isoformat()
    cache = {}
    if os.path.exists(CANDIDATE_CACHE_FILE):
        try:
            with open(CANDIDATE_CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            cache = {}  # 캐시가 깨져 있으면 새로 수집 (에러로 죽지 않게)
    if cache.get("date") != today_str:
        cache = {"date": today_str, "regions": {}}  # 날짜가 바뀌면 캐시 초기화
    if region in cache.get("regions", {}):
        cached = cache["regions"][region]
        print(f"    (오늘자 후보 캐시 재사용: {len(cached)}개, API 호출 생략)")
        return cached[:target_count]

    results = []
    seen = set()
    for variant in REGION_QUERY_VARIANTS:
        if len(results) >= target_count:
            break
        try:
            data = _naver_get("local.json", {
                "query": f"{region} {variant}",
                "display": 5,  # API 최대치. 5보다 크게 요청해도 어차피 5건까지만 온다.
                "sort": "random",  # 매번 똑같은 상위 업체만 나오지 않도록 무작위 정렬
            })
        except Exception as e:
            # 검색어 하나가 재시도 3회 후에도 실패했다고 지역 전체(나아가 빌드 전체)를
            # 죽일 이유는 없다 - 이 검색어만 건너뛰고 다음 검색어로 계속 모은다.
            print(f"    ('{region} {variant}' 후보 검색 실패, 이 검색어는 건너뜀: {e})")
            continue
        for item in data.get("items", []):
            name = strip_tags(item["title"])
            if name and name not in seen:  # 같은 이름 중복 제거 (검색어가 겹쳐서 나오는 경우 방지)
                seen.add(name)
                category = simplify_category(item.get("category", ""))
                results.append({"name": name, "category": category})

    # 오늘 날짜로 캐시에 저장 (내일이 되면 date가 달라져서 자동으로 새로 수집됨)
    # 단, 결과가 0개면(API 전면 실패 등) 저장하지 않는다 - 빈 목록이 캐시되면
    # 그 지역이 "하루 종일" 빈 상태로 굳어버리므로, 다음 실행(2시간 뒤)에서
    # 다시 수집을 시도할 수 있게 캐시를 비워둔다.
    if results:
        cache.setdefault("regions", {})[region] = results
        try:
            with open(CANDIDATE_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        except OSError:
            pass  # 캐시 저장 실패는 치명적이지 않으므로 무시하고 계속
    else:
        print(f"    ({region}: 후보 0개 - 캐시에 저장하지 않고 다음 실행에서 재시도)")
    # 캐시 재사용 경로와 동일하게 target_count로 잘라 반환 (검색어 하나가 최대
    # 5건씩 더해지므로 target_count를 살짝 넘길 수 있음 - 동작 일관성 유지)
    return results[:target_count]


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


def get_weekly_mention_counts(restaurant_name: str, today: datetime.date, region: str = "") -> tuple:
    """
    블로그검색 API로 특정 식당의 '이번 주'/'지난 주' 언급 수를 계산한다.
    postdate(YYYYMMDD) 필드를 기준으로 집계.
    협찬/광고/기자단 문구가 포함된 게시물은 집계에서 제외한다.
    (제외되지 않은 게시물 중에서는 "내돈내산" 계열 문구 비율도 함께 집계한다
    -> get_genuine_ratio()에서 이 값으로 "내돈내산 지수"를 계산함)

    중요(체인점 오염 방지): 검색어를 식당 이름만으로 하면 "정돈"처럼 전국에
    분점이 있는 이름은 홍대점/대학로점 등 모든 지점의 글이 한 지역 점수로
    합산되어, 유명 프랜차이즈가 지역과 무관하게 순위를 독점하는 왜곡이 생긴다.
    그래서 region이 주어지면 "{지역} {이름}"으로 검색해 그 지역과 함께 언급된
    글만 집계한다. (네이버 블로그검색은 본문 전체를 대상으로 하므로, 글 어딘가에
    지역명이 등장하면 잡힌다. 지역명을 안 쓴 진짜 후기도 일부 빠지는 트레이드오프가
    있지만, 모든 식당에 같은 기준이 적용되므로 순위 비교의 공정성은 유지된다.
    전체적으로 집계 수치 자체는 이전보다 낮아지는 게 정상이다.)

    API 한 번 호출은 최대 100건까지만 주므로, MAX_BLOG_RESULTS까지 여러 번
    페이지를 넘겨가며 가져온다(start 파라미터 사용). 최신순 정렬이므로
    지난 주 시작일보다 오래된 게시물이 나오면 그 지점에서 바로 중단한다
    (게시물이 적은 대부분의 식당은 1번 호출만으로 끝나서 API 낭비가 없다).
    """
    query = f"{region} {restaurant_name}".strip()  # 지역명 결합으로 검색 범위를 좁힌다
    # "이번 주"="오늘 포함 최근 7일", "지난 주"="그 직전 7일" - 달력 기준이 아니라
    # 실행하는 날짜(today) 기준으로 매번 새로 계산되는 롤링(rolling) 방식이다.
    # 그래서 이 스크립트를 매일 실행하면, 매일 최신 7일 트렌드가 갱신된다.
    # 주의: 예전에는 -7일/-14일로 잡아서 이번 주가 8일(오늘 포함), 지난 주가
    # 7일로 집계되는 비대칭이 있었다 -> 모든 식당의 growth가 구조적으로 부풀려지는
    # 상향 편향 버그. 오늘을 포함해 정확히 7일 vs 7일이 되도록 -6일/-13일로 수정.
    this_week_start = today - datetime.timedelta(days=6)   # 이번 주: today-6 ~ today (7일)
    last_week_start = today - datetime.timedelta(days=13)  # 지난 주: today-13 ~ today-7 (7일)

    this_week_count = 0
    last_week_count = 0
    filtered_count = 0
    genuine_count = 0  # 협찬 필터를 통과한 글 중, "내돈내산" 계열 문구가 있었던 개수
    oldest_seen = None       # 이번 조회에서 실제로 확인한 가장 오래된 게시일
    reached_older_post = False  # 14일 창 밖(지난 주 이전)까지 도달했는지

    # start=1부터 100씩 늘려가며 페이지를 넘긴다 (네이버 API는 한 번에 최대 100건만 줌)
    start = 1
    while start <= MAX_BLOG_RESULTS:
        data = _naver_get("blog.json", {
            "query": query,  # "{지역} {이름}" - 체인점 오염 방지 (위 docstring 참고)
            "display": 100,
            "start": start,
            "sort": "date",  # 최신순 정렬 - 오래된 글이 나오면 바로 멈출 수 있어서 효율적
        })
        items = data.get("items", [])
        if not items:
            reached_older_post = True  # 게시물이 바닥남 = 창 전체를 다 본 것과 동일
            break

        for item in items:
            postdate_str = item.get("postdate", "")
            if not postdate_str or len(postdate_str) != 8:
                continue  # 날짜 정보가 없는 이상한 게시물은 건너뜀
            try:
                post_date = datetime.datetime.strptime(postdate_str, "%Y%m%d").date()
            except ValueError:
                continue

            if post_date < last_week_start:
                # 지난 주 시작일보다 오래된 글이 나왔다 = 최신순 정렬이므로 이 뒤로는
                # 전부 더 오래된 글이라는 뜻 -> 더 볼 필요 없이 바로 중단
                reached_older_post = True
                break

            if post_date > today:
                continue  # 혹시 미래 날짜로 잘못 찍힌 데이터 방어

            if oldest_seen is None or post_date < oldest_seen:
                oldest_seen = post_date

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
            if len(items) < 100:
                reached_older_post = True  # 마지막 페이지 = 창 전체 확인 완료
            break
        start += 100

    # --- 수집 상한 검열(censoring) 처리 -----------------------------------------
    # MAX_BLOG_RESULTS(예: 1000)를 다 소모하고도 14일 창 밖(지난 주 이전)까지
    # 도달하지 못했다면, 창의 오래된 쪽(=지난 주)이 잘려나간 상태다.
    # 최신순 정렬이라 잘리는 건 항상 지난 주 쪽이므로, 방치하면 last_week가
    # 과소집계되어 growth가 과대평가된다 -> 초인기 매장일수록 순위가 부풀어
    # 1위가 고착되는 왜곡 (예: 이번 주 1000 - 지난 주 0 = +1000처럼 보임).
    #
    # 보정: 실제로 확인한 지난 주 구간(oldest_seen ~ this_week_start-1)의
    # 일평균 게시량으로 지난 주 7일 전체를 선형 추정한다. 추정치는 순위 계산에만
    # 쓰이고, 화면 표기는 원시 집계 그대로 둔다 (사용자에게 알릴 필요 없는
    # 내부 보정이므로 실행 로그의 ⚠️ 경고로만 남긴다).
    capped = not reached_older_post
    last_week_est = None
    if capped and oldest_seen is not None and oldest_seen < this_week_start:
        covered_days = (this_week_start - oldest_seen).days  # 확인된 지난 주 일수(1~7)
        if covered_days >= 2 and last_week_count > 0:
            last_week_est = round(last_week_count * 7 / covered_days)
    # capped인데 oldest_seen이 이번 주 안이라면(=이번 주만으로 1000건 소진)
    # 지난 주는 통째로 미확인 -> 추정 불가(None). 이 경우 growth 자체를
    # 신뢰할 수 없으므로 호출부에서 보수적으로 다룬다.

    return this_week_count, last_week_count, filtered_count, genuine_count, capped, last_week_est


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


def get_region_volume(region: str):
    """
    '{지역} 맛집' 블로그 검색의 총 게시물 수를 그 지역 화제성의 근사치로 사용.
    조회 실패 시 None을 반환한다 - 예전에는 실패도 0으로 돌려줬는데, 그 0이
    하루 단위 캐시에 저장되면 일시적인 API 오류 한 번으로 그 지역이 "하루 종일"
    화제성 0으로 취급되어 hot pick 후보에서 부당하게 제외되는 문제가 있었다.
    None(실패)은 캐시에 저장하지 않아 다음 실행에서 자동으로 재조회된다.
    """
    try:
        data = _naver_get("blog.json", {"query": f"{region} 맛집", "display": 1, "sort": "date"})
        return int(data.get("total", 0))
    except Exception:
        return None


# 이번 실행에서 "급상승 슬롯"으로 선정된 지역들 (지역랭킹 화면의 🚀 배지 표시용).
# resolve_active_regions()가 채워 넣는다.
RISING_REGIONS = set()


def resolve_active_regions() -> list:
    """
    이번 실행에서 실제로 쓸 지역 목록을 정한다:
      CORE_REGIONS(고정)
      + CANDIDATE_REGIONS 중 화제성 절대값 상위 HOT_REGION_COUNT개
      + 전일 대비 신규 게시물 "성장률" 상위 RISING_REGION_COUNT개 (급상승 슬롯)
    CANDIDATE_REGIONS가 비어있으면(설정 안 했으면) 기존 REGIONS 방식 그대로 사용 (하위 호환).

    급상승 슬롯 설계 메모: 화제성 값은 "{지역} 맛집"의 누적 총 게시물 수라서
    전일 대비 '배율'은 의미가 없다 (누적치가 하루에 1.3배가 될 수 없음).
    대신 (오늘 누적 - 어제 누적) = 하루 신규 게시물 수를 누적량으로 나눈
    "일일 성장률"을 쓴다. 공주처럼 기반은 작아도 새 글이 폭증하는 지역이
    강남처럼 기반이 큰 지역보다 높게 나오는 지표다.
    """
    global RISING_REGIONS
    RISING_REGIONS = set()
    if not CANDIDATE_REGIONS:
        return REGIONS

    # --- 하루 단위 캐시: 지역 화제성은 하루 안에 크게 변하지 않으므로,
    # 오늘(KST) 이미 조회한 기록이 있으면 API를 다시 부르지 않고 재사용한다.
    # (2시간마다 실행 기준, 후보 100개 지역이면 하루 약 1,100회 호출 절약)
    #
    # 전일 데이터 롤링: 날짜가 바뀐 첫 실행 시점에는 파일에 어제 값이 아직
    # 남아 있으므로, 덮어쓰기 전에 prev_*로 옮겨 하루 더 보존한다.
    # (급상승 슬롯의 전일 대비 계산에 사용 - 워크플로우의 커밋/복원 대상에
    #  이미 이 파일이 있어서 추가 인프라 변경 없이 동작한다)
    today_str = kst_today().isoformat()
    cached_volumes = {}
    prev_volumes = {}
    prev_date = None
    if os.path.exists(REGION_VOLUME_CACHE_FILE):
        try:
            with open(REGION_VOLUME_CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("date") == today_str:
                # 오늘자 캐시: 그대로 재사용, 어제치는 이미 prev_*에 굴려져 있음
                cached_volumes = cache.get("volumes", {})
                prev_volumes = cache.get("prev_volumes", {})
                prev_date = cache.get("prev_date")
            else:
                # 날짜가 바뀜: 파일에 남아 있는 "어제의 오늘치"를 prev로 승격
                prev_volumes = cache.get("volumes", {})
                prev_date = cache.get("date")
        except (json.JSONDecodeError, OSError):
            cached_volumes = {}  # 캐시가 깨져 있으면 그냥 새로 조회 (에러로 죽지 않게)

    if cached_volumes:
        print("[지역 자동 선정] 오늘자 화제성 캐시 재사용 (API 호출 생략)")
    else:
        print("[지역 자동 선정] 후보 지역별 화제성(블로그 총 게시물 수) 조회 중...")

    scored = []
    volumes_to_save = {}
    for region in CANDIDATE_REGIONS:
        if region in cached_volumes:
            volume = cached_volumes[region]  # 오늘 이미 조회한 값 재사용
            volumes_to_save[region] = volume
        else:
            volume = get_region_volume(region)
            if volume is None:
                # 조회 실패 - 캐시에 저장하지 않고(하루 종일 0으로 굳는 것 방지)
                # 이번 실행에서만 0점 처리. 다음 실행(2시간 뒤)에 자동 재조회된다.
                print(f"  - {region}: 조회 실패 (이번 실행만 0점, 다음 실행에서 재시도)")
                volume = 0
            else:
                print(f"  - {region}: {volume}건")
                volumes_to_save[region] = volume
        scored.append((region, volume))

    # 오늘 날짜로 캐시 저장 + 어제치 보존 (내일이 되면 오늘치가 다시 prev로 굴러감)
    try:
        with open(REGION_VOLUME_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "date": today_str,
                "volumes": volumes_to_save,
                "prev_date": prev_date,
                "prev_volumes": prev_volumes,
            }, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # 캐시 저장 실패는 치명적이지 않으므로 무시하고 계속 진행
    scored.sort(key=lambda x: x[1], reverse=True)

    seoul_set = set(SEOUL_REGIONS)
    if seoul_set:
        # --- 서울 쿼터: 고정 지역 중 서울을 우선 채우고, 나머지는 서울 후보의
        # 화제성 볼륨순으로 SEOUL_TOP_COUNT까지. 전국(HOT) 슬롯과 급상승 슬롯은
        # 비서울 후보만 대상으로 바뀌어 서울이 전국 슬롯을 잠식하지 않는다. ---
        seoul_picks = [r for r in CORE_REGIONS if r in seoul_set][:SEOUL_TOP_COUNT]
        for r, _ in scored:
            if len(seoul_picks) >= SEOUL_TOP_COUNT:
                break
            if r in seoul_set and r not in seoul_picks:
                seoul_picks.append(r)
        hot_pool = [(r, v) for r, v in scored if r not in seoul_set]
        combined = seoul_picks + [r for r in CORE_REGIONS if r not in seoul_set]
    else:
        # 서울 분류 미설정: 기존 방식 그대로 (하위 호환)
        seoul_picks = []
        hot_pool = scored
        combined = list(CORE_REGIONS)

    hot_picks = [r for r, _ in hot_pool[:HOT_REGION_COUNT]]
    for r in hot_picks:
        if r not in combined:
            combined.append(r)

    # --- 급상승 슬롯: 아직 선정 안 된 후보 중 일일 성장률 상위 ---
    rising_picks = []
    if RISING_REGION_COUNT > 0 and prev_volumes:
        # 전일 캐시가 하루 이상 전일 수 있다 (Actions 중단으로 하루를 건너뛴 경우
        # 델타가 48시간치로 부풀어짐) -> 경과 일수로 나눠 "일평균 신규"로 정규화
        try:
            gap_days = max(1, (kst_today() - datetime.date.fromisoformat(prev_date)).days)
        except (TypeError, ValueError):
            gap_days = 1
        rising_scored = []
        for region, volume in scored:
            if region in combined:
                continue  # 이미 고정/화제성/서울 슬롯으로 선정된 지역은 제외
            if region in seoul_set:
                continue  # 서울은 전용 쿼터(SEOUL_TOP_COUNT)가 따로 있으므로 제외
            prev = prev_volumes.get(region)
            if not prev or volume <= prev:
                continue  # 전일 데이터 없음 or 신규 글 없음(검색 인덱스 요동 포함)
            daily_new = (volume - prev) / gap_days
            if daily_new < RISING_MIN_DAILY_POSTS:
                continue  # 하루 신규 글이 너무 적으면 노이즈로 간주
            ratio = daily_new / prev
            if ratio >= 0.30:
                # 누적 총량이 하루 만에 30% 이상 늘어나는 건 실제 유행이 아니라
                # 검색 인덱스 리빌드/집계 요동일 가능성이 압도적 (진짜 바이럴이어도
                # 누적치 기준 일 성장률은 보통 1% 미만). 오탐으로 지역 슬롯을
                # 낭비하지 않도록 건너뛴다.
                print(f"  - {region}: 일 성장률 {ratio*100:.0f}% - 인덱스 이상치로 판단, 제외")
                continue
            rising_scored.append((region, ratio, daily_new))
        rising_scored.sort(key=lambda x: (x[1], x[2]), reverse=True)
        rising_picks = [r for r, _, _ in rising_scored[:RISING_REGION_COUNT]]
        for r in rising_picks:
            combined.append(r)
        RISING_REGIONS = set(rising_picks)
        if rising_picks:
            detail = ", ".join(
                f"{r}(+{d:.0f}건/일, {ratio*100:.2f}%/일)" for r, ratio, d in rising_scored[:RISING_REGION_COUNT]
            )
            print(f"[급상승 슬롯] {detail}" + (f" (전일 캐시 {gap_days}일 전 - 일평균 환산)" if gap_days > 1 else ""))
        else:
            print("[급상승 슬롯] 기준(하루 신규 "
                  f"{RISING_MIN_DAILY_POSTS}건 이상) 충족 지역 없음")
    elif RISING_REGION_COUNT > 0:
        print("[급상승 슬롯] 전일 화제성 데이터 없음 - 내일부터 동작 (첫날은 건너뜀)")

    print(f"[지역 확정] "
          + (f"서울 {seoul_picks} + " if seoul_picks else "")
          + f"고정 {CORE_REGIONS} + 화제성 상위 {hot_picks}"
          + (f" + 급상승 {rising_picks}" if rising_picks else "")
          + f" = {combined}")
    return combined


def _entity_key(r: dict) -> str:
    """
    streak/트렌드 기록 파일에서 식당을 식별하는 키.
    같은 식당이 인접 지역(예: 성수/서울숲) 양쪽 후보에 다 들어갈 수 있고,
    블로그 집계도 "{지역} {이름}"으로 지역별로 따로 세기 때문에,
    이름만으로 키를 잡으면 두 지역의 서로 다른 시계열이 한 기록에 섞인다
    (스파크라인이 지그재그로 오염됨). 그래서 "이름|지역" 복합키를 쓴다.
    """
    return f"{r['name']}|{r['region']}"


def apply_streaks(results: list, today: datetime.date) -> None:
    """
    각 식당이 "며칠 연속" 급상승 목록(growth > 0)에 들었는지 계산해서
    results의 각 항목에 "streak" 키를 채워 넣는다 (제자리에서 수정, 반환값 없음).

    기록은 STREAK_HISTORY_FILE(기본 streak_history.json)에 저장해서 다음 실행 때도
    이어서 셀 수 있게 한다. 로컬에서 매번 실행하면 파일이 계속 쌓이지만,
    GitHub Actions에서는 이 파일을 저장소에 다시 커밋해야 실행 사이에 기록이
    유지된다 (workflow 파일에 그 커밋 단계가 포함되어 있다).

    주의: 이 스크립트는 하루에 여러 번(예: 2시간마다) 실행될 수 있다. "어제 기록이
    있으면 +1, 없으면 1로 리셋" 이라는 단순한 규칙만 쓰면, 오늘 두 번째 실행부터는
    "어제 기록"이 아니라 "오늘 아까 실행에서 남긴 기록"을 보게 되어 조건이 어긋나서
    매번 1로 리셋돼버린다. 그래서 "오늘 이미 기록을 남긴 식당"인지를 먼저 확인해서,
    같은 날 재실행 시에는 streak를 더 늘리지 않고 그대로 유지한다 (날짜가 실제로
    넘어갔을 때만 +1). 하루에 몇 번을 돌리든 streak는 "며칠째 연속인지"만 센다.
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
        key = _entity_key(r)  # "이름|지역" - 같은 식당이라도 지역별 기록을 분리
        if r["growth"] <= 0:
            r["streak"] = 0
            continue  # 오늘 안 올랐으면 연속 기록 대상이 아님

        prev = history.get(key)
        if prev and prev.get("last_date") == today_str:
            # 오늘 이미 한 번 이상 실행되어 기록을 남긴 식당 - 같은 날 재실행이면
            # 며칠 연속인지는 그대로 유지한다 (여기서 또 +1 하면 하루에 여러 번
            # 돌릴 때마다 연속 일수가 부풀려짐)
            streak = prev.get("streak", 1)
        elif prev and prev.get("last_date") == yesterday_str:
            streak = prev.get("streak", 0) + 1  # 어제도 상승 중이었다 -> 연속 기록 이어감
        else:
            streak = 1  # 어제는 기록이 없거나 끊겼다 -> 오늘부터 새로 시작
        history[key] = {"last_date": today_str, "streak": streak}
        r["streak"] = streak

    # 기록 파일이 무한정 커지지 않도록, 최근 14일 안에 갱신 안 된 식당은 정리
    cutoff = (today - datetime.timedelta(days=14)).isoformat()
    history = {key: v for key, v in history.items() if v.get("last_date", "") >= cutoff}

    try:
        with open(STREAK_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # 기록 저장 실패는 치명적이지 않음 (다른 기록 파일 저장과 동일한 정책)


def apply_trend_history(results: list, today: datetime.date) -> None:
    """
    직전 실행과 비교한 "순위 변동"(rank_delta)과, 실행마다의 이번 주 언급 수를
    누적한 "추세 데이터"(spark_points)를 results의 각 항목에 채워 넣는다.

    기록은 TREND_HISTORY_FILE(기본 trend_history.json)에 저장하며, 구조는:
      {
        "prev_ranks": {"식당명": 3, ...},     # 직전 실행에서의 전체 순위
        "mentions": {"식당명": {"points": [["2026-07-12T09:45", 12], ...],
                                 "last": "2026-07-12"}, ...}
      }

    - rank_delta: (직전 순위 - 현재 순위). 양수=상승(▲), 음수=하락(▼), 0=유지.
      직전 실행에 없던 식당은 None (화살표 표시 안 함 - "첫 등장" 배지가 대신함).
    - spark_points: 실행할 때마다 이번 주 언급 수를 한 점씩 쌓은 목록.
      2시간마다 실행 기준 최근 48개(약 4일치)만 유지해 파일이 무한정 안 커지게 한다.
      점이 2개 이상 모이면 카드에 미니 추세 그래프(스파크라인)가 그려진다.
    - streak처럼 하루 여러 번 실행돼도 안전하다: 순위 비교는 "직전 실행"과 하는
      것이 의도된 동작이고(2시간 사이 움직임을 보여줌), 언급 추세도 실행마다
      한 점씩 쌓이는 게 맞는 동작이라 별도의 같은 날 보정이 필요 없다.
    """
    history = {}
    if os.path.exists(TREND_HISTORY_FILE):
        try:
            with open(TREND_HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError):
            history = {}  # 파일이 깨져 있으면 새로 시작 (에러로 죽지 않게)

    prev_ranks = history.get("prev_ranks", {})
    mentions = history.get("mentions", {})
    now_label = kst_now().strftime("%Y-%m-%dT%H:%M")

    for idx, r in enumerate(results, start=1):
        key = _entity_key(r)  # "이름|지역" - 같은 식당이라도 지역별 시계열을 분리

        # 순위 변동: 직전 실행 순위와 비교 (기록이 없으면 None -> 화살표 없음)
        prev = prev_ranks.get(key)
        r["rank_delta"] = (prev - idx) if isinstance(prev, int) else None

        # 언급량 추세: 이번 실행의 이번 주 언급 수를 한 점 추가
        entry = mentions.get(key, {"points": []})
        entry["points"].append([now_label, r["this_week"]])
        entry["points"] = entry["points"][-48:]  # 최근 48회(약 4일치)만 유지
        entry["last"] = today.isoformat()
        mentions[key] = entry
        r["spark_points"] = [p[1] for p in entry["points"]]

    # 14일 넘게 갱신 안 된 식당 기록은 정리 (파일 크기 관리)
    cutoff = (today - datetime.timedelta(days=14)).isoformat()
    mentions = {k: v for k, v in mentions.items() if v.get("last", "") >= cutoff}

    # 이번 실행의 순위를 "직전 순위"로 저장 -> 다음 실행 때 비교 기준이 됨
    # ("datalab" 키는 apply_search_trends()가 쓰는 하루 단위 캐시라 그대로 보존)
    new_ranks = {_entity_key(r): i for i, r in enumerate(results, start=1)}
    try:
        with open(TREND_HISTORY_FILE, "w", encoding="utf-8") as f:
            # separators로 공백 없이 압축 저장 - 기록 파일 중 가장 크고(식당당 스파크
            # 포인트 48개) 2시간마다 data 브랜치에 push되는 파일이라 용량을 아낀다
            # (indent=2 대비 약 30% 절감. 사람이 볼 일은 드물어 가독성 손해는 미미)
            json.dump({"prev_ranks": new_ranks, "mentions": mentions,
                       "datalab": history.get("datalab", {})}, f,
                      ensure_ascii=False, separators=(",", ":"))
    except OSError:
        pass  # 기록 저장 실패는 치명적이지 않으므로 무시하고 계속


def apply_search_trends(results: list, today: datetime.date) -> None:
    """
    상위 DATALAB_MAX_ITEMS개 식당에 한해, 데이터랩(검색어 트렌드) API로
    "실제 검색 관심도가 오르는 중인지"를 추가로 확인해 "search_rising" 키를 채운다.
    (블로그 포스팅 수와 달리, 이건 사람들이 네이버에 그 이름을 직접 검색한 지표라
    훨씬 직접적인 관심도 신호다. 상한은 일일 호출 한도 1,000회 보호용 안전핀:
    상한 300 기준 최악(캐시 전멸)에도 300/5 x 12실행 = 720회로 한도 안이다.
    과거에는 8로 잡아 "전체" 탭 상위만 확인했으나, 여유가 커서 표시되는 전
    매장으로 확대했다 - 하루 단위 이름 캐시 덕에 평소엔 ~60-80회/일 수준)

    데이터랩은 한 번 호출에 최대 5개 키워드 그룹을 같이 물어볼 수 있어서,
    5개씩 묶어(batch) 호출 횟수를 최대한 아낀다.
    """
    if not DATALAB_ENABLED or not results:
        return

    # 대상 선정: "전체" 탭과 동일하게 이름 기준 중복 제거 후 상위 DATALAB_MAX_ITEMS개.
    # (예전의 results[:N] 슬라이스는 같은 식당이 두 지역 항목으로 상위권에 들면
    # 중복이 자리를 차지해, 전체 탭에 실제로 표시되는 N곳을 전부 못 덮었다.
    # 호출량은 동일 - 어차피 이름 단위로 조회하므로 고유 이름 N개가 상한이다)
    targets = []
    _seen_target_names = set()
    for r in results:
        if r["growth"] <= 0:
            continue  # build_tabs()의 "전체" 탭 선정 기준과 동일하게 상승 매장만
        if r.get("low_genuine"):
            continue  # 전체 탭에 표시되지 않는 매장은 데이터랩 호출 낭비 (기준 동일 유지)
        if r["name"] in _seen_target_names:
            continue
        _seen_target_names.add(r["name"])
        targets.append(r)
        if len(targets) == DATALAB_MAX_ITEMS:
            break
    # 주의(과거 버그): 예전에는 -7일로 잡아서, 조회 구간 14일(-13일~오늘)을
    # "이번 주 8일 vs 지난 주 6일"로 나누는 비대칭이 있었다 -> 모든 식당의
    # 검색 관심도가 구조적으로 "상승"으로 판정되기 쉬운 상향 편향 버그.
    # (블로그 집계의 -7일/-14일 비대칭 버그와 같은 클래스의 문제)
    # 오늘 포함 정확히 7일 vs 7일이 되도록 -6일로 수정.
    this_week_start = today - datetime.timedelta(days=6)   # 이번 주: today-6 ~ today (7일)
    today_str = today.isoformat()

    # --- 하루 단위 캐시: 데이터랩 지표는 일 단위로 갱신되는 성격이라, 같은 날
    # 안에서는 같은 식당을 다시 물어봐도 결과가 같다. 그래서 식당별로 "오늘 이미
    # 조회한 결과"가 있으면 재사용하고, 오늘 새로 상위권에 진입한 식당만 조회한다.
    # (상위 8개 구성이 실행마다 바뀔 수 있어서, "하루 1번 통째로 스킵"이 아니라
    # 식당 단위로 따져야 새 진입 식당의 배지가 누락되지 않는다)
    # 캐시는 trend_history.json의 "datalab" 영역을 같이 쓴다 (파일 추가 없음).
    history = {}
    if os.path.exists(TREND_HISTORY_FILE):
        try:
            with open(TREND_HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, OSError):
            history = {}
    datalab_cache = history.get("datalab", {}) if isinstance(history, dict) else {}

    to_query = []
    queued_names = set()
    for r in targets:
        cached = datalab_cache.get(r["name"])
        if cached and cached.get("date") == today_str:
            r["search_rising"] = cached.get("rising")  # 오늘자 결과 재사용
        elif r["name"] not in queued_names:
            to_query.append(r)
            queued_names.add(r["name"])
        # (같은 이름이 두 지역 항목으로 중복 등장하면 한 번만 조회하고,
        # 나머지 항목은 아래 마지막 단계에서 캐시로 채운다 - 데이터랩은
        # 이름 단위 검색 지표라 지역이 달라도 결과가 같기 때문)
    if targets and not to_query:
        print("    (데이터랩: 전 항목 오늘자 캐시 재사용, API 호출 생략)")

    # 5개씩 묶어서 처리 (데이터랩 API 한 번 호출당 최대 5개 키워드 그룹 허용)
    for i in range(0, len(to_query), 5):
        batch = to_query[i:i + 5]
        # 참고: 블로그 집계와 달리 데이터랩은 지역명을 결합하지 않는다.
        # 사람들이 검색창에 실제로 치는 건 대부분 식당 이름 그 자체라서,
        # "{지역} {이름}"으로 좁히면 검색량이 0에 수렴해 전부 "데이터 부족"이 된다.
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
            datalab_cache[r["name"]] = {"date": today_str, "rising": r["search_rising"]}
            print(f"    (데이터랩: {r['name']} 검색 관심도 {'상승' if r['search_rising'] else '유지/하락' if r['search_rising'] is not None else '데이터 부족'})")

    # 오늘 날짜가 아닌 낡은 캐시는 정리하고 파일에 반영 (내일이 되면 자동으로 새로 조회됨)
    datalab_cache = {n: v for n, v in datalab_cache.items() if v.get("date") == today_str}

    # 마무리: 같은 이름의 다른 지역 항목이나 배치 실패로 아직 값이 없는 항목에,
    # 방금 채워진 캐시 값을 공유한다. targets가 아니라 results 전체를 훑는 이유:
    # 데이터랩은 "이름 단위" 지표라 지역이 달라도 결과가 같으므로, 이미 조회한
    # 이름과 같은 식당이 지역 탭에 있으면 추가 호출 없이 배지를 함께 보여줄 수 있다.
    for r in results:
        if "search_rising" not in r:
            cached = datalab_cache.get(r["name"])
            if cached:
                r["search_rising"] = cached.get("rising")

    if isinstance(history, dict):
        history["datalab"] = datalab_cache
        try:
            with open(TREND_HISTORY_FILE, "w", encoding="utf-8") as f:
                # apply_trend_history()의 저장과 동일한 압축 포맷 유지
                json.dump(history, f, ensure_ascii=False, separators=(",", ":"))
        except OSError:
            pass  # 캐시 저장 실패는 치명적이지 않으므로 무시


def build_ranking() -> tuple:
    """전 지역을 순회하며 급상승 맛집 TOP N을 계산. (결과 리스트, 협찬/광고 추정 총 제외 건수)를 반환"""
    today = kst_today()  # UTC 서버에서 실행돼도 반드시 한국 날짜 기준으로 집계
    results = []
    total_filtered = 0

    for region in REGIONS:
        print(f"[지역검색] {region} 맛집 후보 수집 중...")
        # 급상승 슬롯 지역은 후보 풀을 2배로 심층 스캔한다: "지역이 뜨는 순간" =
        # 신생 매장이 쏟아지는 순간이라, 로컬검색 상위 몇 개만 보면 새 매장을
        # 놓치기 쉽다. 전 지역 확대는 API 낭비이므로 필요한 지역에만 집중 투자.
        # (급상승 여부는 그날 첫 실행에서 확정되고 후보 캐시도 같은 날짜 기준으로
        #  초기화되므로, 하루 안에서 캐시와 스캔 깊이가 어긋날 일은 없다)
        _deep = region in RISING_REGIONS
        candidates = get_candidate_restaurants(
            region, DISPLAY_PER_REGION * (2 if _deep else 1)
        )
        if _deep:
            print(f"    (급상승 지역 심층 스캔: 후보 {len(candidates)}개 수집)")

        for candidate in candidates:
            name = candidate["name"]
            category = candidate["category"]
            # 주의: 예전에는 여기서 이름 기준 전역 중복 제거(seen_names)를 했는데,
            # 그러면 인접 지역(예: 성수/서울숲) 양쪽 후보에 다 들어가는 식당이
            # REGIONS 배열에서 먼저 나온 지역에만 집계되고 뒤 지역의 통계
            # (지역랭킹 합산)에서는 통째로 누락되는 왜곡이 있었다.
            # 블로그 집계가 "{지역} {이름}"으로 지역별로 따로 세는 구조라,
            # 지역마다 독립적으로 집계하는 게 맞다 (수집 단계에서는 중복 허용).
            # "전체" 탭의 이름 중복 제거는 build_tabs()에서 표시 단계에만 적용한다.

            # 부분 실패 내성: 재시도 3회 후에도 실패한 식당 1곳 때문에 15분짜리
            # 빌드 전체(그리고 그 회차에 이미 쓴 수백 건의 API 호출)가 통째로
            # 죽지 않도록, 해당 식당만 이번 실행에서 건너뛰고 계속 진행한다.
            try:
                this_week, last_week, filtered, genuine, capped, last_week_est = \
                    get_weekly_mention_counts(name, today, region)
            except Exception as e:
                print(f"    ({name}: 언급 수 집계 실패, 이번 실행에서 건너뜀: {e})")
                continue
            total_filtered += filtered
            if filtered:
                print(f"    (협찬/광고 추정 {filtered}건 제외)")

            growth = this_week - last_week  # 이게 바로 "급상승" 순위를 매기는 핵심 숫자

            # --- 수집 상한 검열 보정: MAX_BLOG_RESULTS를 다 쓰고도 14일 창을 다
            # 못 본 경우, 지난 주가 잘려나가 growth가 부풀어 있다. 순위 계산에는
            # 확인된 구간의 일평균으로 추정한 지난 주(last_week_est)를 쓰고,
            # 화면 표기는 원시 집계 그대로 둔다 (로그 경고로만 확인).
            growth_for_score = growth
            if capped:
                if last_week_est is not None:
                    growth_for_score = this_week - last_week_est
                    print(f"    ⚠️ {name}: 수집 상한({MAX_BLOG_RESULTS}건) 도달 - "
                          f"지난 주 {last_week}건(부분)을 {last_week_est}건으로 추정해 "
                          f"순위 반영 (growth {growth} -> {growth_for_score})")
                else:
                    # 이번 주만으로 상한을 소진해 지난 주를 한 건도 못 봄:
                    # 증가폭 추정 근거가 없으므로 보수적으로 0으로 처리
                    # (전체/급상승 표시에서는 빠지고, 지역 통계·지역 탭에는 남는다)
                    growth_for_score = 0
                    print(f"    ⚠️ {name}: 수집 상한 도달 + 지난 주 데이터 전무 - "
                          f"급상승 판정 보류 (지역 통계에는 포함)")

            genuine_ratio = get_genuine_ratio(this_week, last_week, genuine)

            # 최소 언급량 필터: 우연히 1~2건 튄 걸 급상승으로 착시하지 않도록
            if this_week < 2:
                continue

            # 내돈내산 지수 필터: 표본이 충분한데(genuine_ratio가 None이 아님)
            # 그 값이 기준(MIN_GENUINE_RATIO_TO_SHOW)보다 낮으면 순위/티커 표시
            # 대상에서 제외한다. 표본이 부족해서 지수를 못 낸 경우는
            # (genuine_ratio가 None) 판단 근거가 없으므로 그냥 통과시킨다.
            #
            # 주의(과거 버그): 예전에는 여기서 continue로 수집 결과(all_results)에서
            # 통째로 뺐는데, 그러면 이 식당들의 언급량이 지역랭킹 합산("이번 주 N건",
            # 침체 판정)에서도 빠져서 협찬성 매장이 많은 지역일수록 화제성이
            # 저평가되는 왜곡이 생겼다 (growth<=0을 표시 단계에서만 거르는 것과
            # 같은 원칙 위반). 그래서 데이터는 남기고 low_genuine 플래그만 달아,
            # 전체 탭/지역별 탭/티커 등 "표시 단계"에서만 거른다.
            low_genuine = (
                MIN_GENUINE_RATIO_TO_SHOW is not None
                and genuine_ratio is not None
                and genuine_ratio < MIN_GENUINE_RATIO_TO_SHOW
            )
            if low_genuine:
                print(f"    ({name}: 내돈내산 지수 {genuine_ratio}%로 낮아 표시에서 제외 "
                      f"- 지역 통계에는 포함)")

            # 신뢰도 가중 점수: growth(증가폭)에 내돈내산 비율을 곱해서 보정한 값.
            #
            # 주의(과거 버그): 예전에는 표본 부족(genuine_ratio=None)이면 가중치를
            # 1.0으로 뒀는데, 지수가 있는 식당은 대부분 5~15%(가중치 0.05~0.15)라
            # 오히려 "데이터가 적을수록 특혜"를 받는 역전이 생겼다 (언급 3건짜리가
            # +12건짜리를 이기는 실제 사례 발생). 그래서 None 분기를 폐기하고,
            # 모든 식당에 베이지안 스무딩(라플라스 스무딩)을 일괄 적용한다:
            #
            #   보정 비율 = (내돈내산 글 수 + K×P0) / (전체 표본 수 + K)
            #
            # 표본이 적으면 자동으로 전체 평균(P0=10%) 근처로 수렴하고, 표본이
            # 많아질수록 실제 비율을 그대로 따라간다 -> 별도의 표본 수 분기가
            # 필요 없어진다. (화면에 보이는 "내돈내산 N%" 배지는 이 보정값이
            # 아니라 기존 그대로 실측 비율 + 표본 수 제한을 사용한다)
            total_sample = this_week + last_week
            smoothed_ratio = (genuine + GENUINE_SMOOTHING_K * GENUINE_PRIOR_RATIO) / (
                total_sample + GENUINE_SMOOTHING_K
            )
            # 주의: 스무딩 비율 곱은 growth가 양수일 때만 적용한다. 음수에 0~1 사이
            # 비율을 곱하면 페널티가 오히려 "축소"되어, 하락 폭이 크고 신뢰도가
            # 낮은 매장일수록 score가 0에 가까워지는 역전이 생긴다 (예: growth -10에
            # 지수 5%면 -0.5가 되어, growth -2에 지수 50%인 매장의 -1.0을 이김).
            # 음수/0은 growth 그대로 써서 하락 폭 순서가 정직하게 유지되게 한다.
            # (지역랭킹 합산과 티커의 침체 판정은 score가 아니라 growth를 쓰므로 영향 없음)
            # 수집 상한(capped) 보정: 순위 점수는 검열 보정된 growth_for_score 기준.
            # 화면의 +N 배지/건수는 원시 집계 그대로 (보정은 내부에서만, 로그로 확인).
            score = growth_for_score * smoothed_ratio if growth_for_score > 0 else growth_for_score

            results.append({
                "name": name,
                "region": region,
                "category": category,
                "this_week": this_week,
                "last_week": last_week,
                "growth": growth,
                "filtered": filtered,
                "genuine_ratio": genuine_ratio,  # None이면 표본 부족 -> 화면에 배지 안 뜸
                "low_genuine": low_genuine,  # True면 표시 단계(탭/티커)에서만 제외
                "capped": capped,  # True면 수집 상한 도달 (건수는 하한값, 카드에 칩 표시)
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

    # 직전 실행 대비 순위 변동(▲▼)과 언급량 추세(스파크라인) 데이터 계산
    # (trend_history.json에 기록 - GitHub Actions에서는 이 파일도 커밋되어야 유지됨)
    apply_trend_history(results, today)

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
    # 급상승 슬롯으로 선정된 지역 표시 (지역랭킹 카드의 🚀 배지용).
    # 선정 근거(전일 대비 성장률)는 resolve_active_regions()가 계산했고,
    # 여기서는 그 결과 집합(RISING_REGIONS)만 플래그로 옮겨 단다.
    for row in ranking:
        row["is_rising"] = row["region"] in RISING_REGIONS
    ranking.sort(key=lambda x: x["total_growth"], reverse=True)  # 증가폭 합계 큰 지역이 위로
    return ranking


def build_tabs(all_results: list, region_ranking: list) -> dict:
    """
    전체 결과에서 '전체' 탭 + '지역랭킹' 탭 + 지역별 탭 데이터를 만든다.
    반환값은 {"탭 이름": [카드로 보여줄 데이터 목록], ...} 형태의 딕셔너리.
    REGIONS에 새 지역이 추가되면, 다음 실행 시 자동으로 그 지역 탭도 생긴다
    (코드 수정 불필요 - config.py의 REGIONS만 바뀌면 됨).
    """
    # "전체" 탭: 같은 식당이 여러 지역에 걸쳐 있으면(수집 단계에서는 지역별로
    # 각각 집계됨) 화면에는 한 번만 보여준다. all_results는 이미 점수순으로
    # 정렬되어 있으므로, 앞에서부터 훑으면 "가장 점수가 높은 지역의 항목"이
    # 그 식당의 대표로 남는다. 지역별 탭/지역랭킹 집계에는 영향 없음.
    seen_global = set()
    overall = []
    for r in all_results:
        if r["growth"] <= 0:
            # "전체" 탭은 사이트의 간판인 "급상승 TOP N"이므로, 상위권 풀이 말라붙은
            # 극단 상황에서도 하락/보합 매장이 끼어들지 않게 표시 단계에서만 거른다.
            # 수집 데이터(all_results)에서 빼는 게 아니라 여기서만 거르는 이유:
            # 지역랭킹 합산("이번 주 N건"과 침체 상권 판정)과 지역별 탭은 하락
            # 매장의 데이터도 필요로 하기 때문 (수집 단계에서 자르면 지역 통계가
            # 상승분만 남아 상향 왜곡되고, 티커의 "침체" 문구는 영원히 못 뜬다).
            continue
        if r.get("low_genuine"):
            continue  # 내돈내산 지수 미달 - growth<=0과 같은 원칙으로 표시 단계에서만 제외
        if r["name"] in seen_global:
            continue
        seen_global.add(r["name"])
        overall.append(r)
        if len(overall) == TOP_N:
            break
    tabs = {"전체": overall}  # 첫 번째 탭은 항상 "전체" (전 지역 통합 TOP N)
    # "협찬 포함" 토글용 추가 카드: low_genuine(내돈내산 지수 미달) 상승 매장도
    # 카드로 미리 구워서 리스트 뒤에 붙여두되, 기본은 숨김(lg-hidden 클래스).
    # 화면의 토글 버튼이 켜지면 JS가 보여주고 현재 정렬 기준으로 재배열한다.
    # (서버 재호출 없이 표시 모드만 바꾸는 방식 - 클라이언트 사이드 원칙 유지)
    lg_overall = []
    seen_lg = set()
    for r in all_results:
        if r["growth"] <= 0 or not r.get("low_genuine"):
            continue
        if r["name"] in seen_global or r["name"] in seen_lg:
            continue
        seen_lg.add(r["name"])
        lg_overall.append(r)
        if len(lg_overall) == TOP_N:
            break
    tabs["전체"] = overall + lg_overall
    if region_ranking:
        tabs["지역랭킹"] = region_ranking  # 두 번째 탭 = 지역별 랭킹 (데이터 있을 때만)

    # --- "서울 전체" 탭: 서울 권역들을 한데 모은 미니 "전체" 랭킹.
    # 전체 탭과 같은 규칙(상승 매장, 이름 중복 제거, TOP_N개 + 협찬 숨김분)을
    # 서울 소속 지역에만 적용한다. 서울 접이식 서브탭의 첫 항목으로 쓰인다. ---
    _seoul = set(SEOUL_REGIONS)
    if _seoul and any(r["region"] in _seoul for r in all_results):
        seoul_overall, seoul_lg = [], []
        _seen_s, _seen_s_lg = set(), set()
        for r in all_results:
            if r["region"] not in _seoul or r["growth"] <= 0:
                continue
            if r.get("low_genuine"):
                if r["name"] not in _seen_s and r["name"] not in _seen_s_lg and len(seoul_lg) < TOP_N:
                    _seen_s_lg.add(r["name"])
                    seoul_lg.append(r)
                continue
            if r["name"] in _seen_s or r["name"] in _seen_s_lg:
                continue  # 서울 내 타 권역 동명 매장 중복 방지 (숨김분 포함)
            _seen_s.add(r["name"])
            if len(seoul_overall) < TOP_N:
                seoul_overall.append(r)
        if seoul_overall or seoul_lg:
            tabs["서울 전체"] = seoul_overall + seoul_lg
    # 지역 탭 순서: 수집 순서(REGIONS = 고정 + 화제성 + 급상승)가 아니라
    # 이번 실행의 지역랭킹(증가폭 합계 내림차순)을 그대로 따른다.
    # -> 탭 줄에서 왼쪽일수록 "지금 뜨는 지역"이고, 지역랭킹 탭의 순위와
    #    탭 나열 순서가 항상 일치해서 화면 간 어긋남이 없다.
    # 랭킹에 없는 지역(집계 결과 0곳)은 어차피 탭도 안 생기지만,
    # 방어적으로 뒤에 원래 순서대로 붙인다.
    _ranked_regions = [row["region"] for row in region_ranking] if region_ranking else []
    _ordered_regions = _ranked_regions + [r for r in REGIONS if r not in _ranked_regions]
    for region in _ordered_regions:
        # 기본 표시분(내돈내산 지수 충족) 상위 N개 + 협찬 포함 모드용 숨김분 상위 N개.
        # 숨김분도 all_results 정렬 순서(점수순)를 그대로 물려받는다.
        clean = [
            r for r in all_results
            if r["region"] == region and not r.get("low_genuine")
        ]
        lg = [
            r for r in all_results
            if r["region"] == region and r.get("low_genuine")
        ]
        region_items = clean[:TOP_N_PER_REGION] + lg[:TOP_N_PER_REGION]
        if region_items:  # 데이터가 있는 지역만 탭으로 생성 (빈 탭 방지)
            tabs[region] = region_items
    return tabs


def naver_map_link(name: str, region: str) -> str:
    """
    식당 이름+지역으로 네이버 지도 검색 링크를 만든다.
    실제 좌표(위도/경도) 없이 "이름으로 검색"하는 방식이라 구현이 간단하고,
    네이버 지도가 알아서 가장 근접한 결과를 찾아 보여준다.
    """
    query = urllib.parse.quote(f"{name} {region}")  # 한글 등을 URL에 넣을 수 있게 인코딩
    return f"https://map.naver.com/p/search/{query}"


def render_sparkline(points) -> str:
    """
    실행 이력(spark_points)으로 작은 추세 그래프(SVG)를 만든다.
    점이 2개 미만이면(첫 실행 직후 등) 아직 추세랄 게 없으므로 빈 문자열 반환.
    stroke에 currentColor를 써서 CSS(.spark의 color)로 색을 제어한다
    -> 다크모드에서도 CSS만으로 색이 자동 전환된다.
    """
    if not points or len(points) < 2:
        return ""
    w, h, pad = 64, 20, 2
    mn, mx = min(points), max(points)
    span = (mx - mn) or 1  # 전부 같은 값이면 0으로 나누지 않도록 방어
    step = (w - 2 * pad) / (len(points) - 1)
    coords = " ".join(
        f"{pad + i * step:.1f},{h - pad - (v - mn) / span * (h - 2 * pad):.1f}"
        for i, v in enumerate(points)
    )
    return (
        f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}" aria-hidden="true">'
        f'<polyline points="{coords}" fill="none" stroke="currentColor" '
        f'stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>'
    )


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
        region_map_query = urllib.parse.quote(r["region"] + " 맛집")
        map_url = f"https://map.naver.com/p/search/{region_map_query}"
        # 급상승 슬롯으로 선정된 지역 구분 배지: 화제성 절대값(볼륨 상위)이 아니라
        # "전일 대비 신규 게시물 성장률"로 뽑혀 들어온 지역임을 표시한다
        rising_badge_html = (
            ' <span class="rising-badge">🚀 급상승 지역</span>'
            if r.get("is_rising") else ""
        )
        rows_html += f"""
        <a class="card" data-mapquery="{region_map_query}" href="{map_url}" target="_blank" rel="noopener">
          <div class="rank">{i}</div>
          <div class="info">
            <div class="name">{escape(r['region'])} <span class="map-icon">📍</span></div>
            <div class="region">언급 식당 {r['count']}곳{rising_badge_html}</div>
          </div>
          <div class="stats">
            <span class="growth">{growth_badge}</span>
            <span class="count">이번 주 {r['total_this_week']}건 · 지난 주 {r['total_last_week']}건</span>
          </div>
        </a>"""
    # 카드 사이 간격(gap: 10px)은 .card 자체가 아니라 .card-list 래퍼가 담당한다.
    # 예전에는 이 래퍼 없이 카드 <a>들만 반환해서 지역랭킹 탭만 카드가 0px로
    # 붙어 그림자/모서리가 겹쳐 보였다 (특히 모바일 2단 배치에서 도드라짐).
    # render_cards()와 동일하게 감싸서 간격을 통일한다. 정렬/필터 JS는 전부
    # 버튼이 속한 패널 스코프로만 동작하고 이 탭에는 그 버튼이 없으므로 안전.
    return f'<div class="card-list">{rows_html}</div>'


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
    # 카테고리 필터 순서: 가나다순이 아니라 음식점 관습 순서로 고정한다.
    # 목록에 없는 돌발 카테고리(예: "분식")는 술집 뒤 · 기타 앞에 가나다순으로 끼운다.
    _CAT_ORDER = {"한식": 0, "중식": 1, "일식": 2, "양식": 3, "카페": 4, "술집": 5, "기타": 99}
    categories = sorted(
        set(r.get("category", "기타") for r in items),
        key=lambda c: (_CAT_ORDER.get(c, 50), c),
    )
    # 카테고리는 simplify_category()의 고정 화이트리스트("카페"/"한식"/.../"기타")에서만
    # 나오므로 현재는 escape가 항등 변환이지만, 나중에 simplify_category가 원본 문자열을
    # 그대로 통과시키는 방향으로 바뀌어도 구멍이 안 생기도록 명시적으로 방어해 둔다
    # (카드 쪽 data-category와 같은 기준 - 둘 다 escape해야 필터 매칭이 항상 일치)
    category_filter_html = '<button class="cat-btn active" data-cat="전체" onclick="filterByCategory(this)">전체</button>'
    for cat in categories:
        category_filter_html += f'<button class="cat-btn" data-cat="{escape(cat)}" onclick="filterByCategory(this)">{escape(cat)}</button>'

    # 정렬 미니탭: 급상승순(기본)/언급많은순/진짜후기순. 서버를 다시 호출하지 않고
    # 지금 화면에 이미 그려진 카드들을 JS로 재배열하는 방식이라 추가 API 호출이 없다.
    sort_bar_html = (
        '<div class="sort-bar">'
        '<button class="sort-btn active" data-sort="rankmetric" onclick="sortByMetric(this)">🔥 급상승순</button>'
        '<button class="sort-btn" data-sort="thisweek" onclick="sortByMetric(this)">💬 언급많은순</button>'
        '<button class="sort-btn" data-sort="genuine" onclick="sortByMetric(this)">✅ 진짜후기순</button>'
        # 급상승률순: 절대 증가폭이 아니라 "지난 주 대비 몇 배 뛰었는지"(상대 증가율)
        # 기준. 블로그 모수가 큰 대형 상권(부산/경주 등)이 절대 증가폭에서 항상
        # 유리한 체급 문제를 보완해, 소형 상권의 진짜 급상승 매장이 위로 올라온다.
        '<button class="sort-btn" data-sort="rate" onclick="sortByMetric(this)">📈 급상승률순</button>'
        '</div>'
        # 현재 순위 매장 항목 복사(카톡 투표용 등): 지금 화면(필터/정렬 상태 그대로)의
        # 상위 5개 이름만 줄바꿈으로 복사. 예전엔 정렬 바 우측 끝에 인라인으로 붙어
        # 있어서 정렬 버튼으로 오인 클릭되기 쉬웠는데, 카드 리스트 직전의 독립된
        # 한 줄(utility-row)로 분리해 와이드하게 단독 배치한다.
        # 유틸리티 줄: 카드 리스트 직전의 독립된 한 줄.
        # 배치: [협찬 포함][순위 복사] | [즐겨찾기] - 왼쪽 둘은 "지금 보는 목록을
        # 조작하는" 기능이고 즐겨찾기는 "다른 화면으로 이동"이라, 즐겨찾기만
        # 핑크 톤 + 여백으로 살짝 떼어 성격이 다름을 시각적으로 구분한다.
        '<div class="utility-row">'
        '<button class="util-btn lg-toggle-btn" onclick="toggleSponsored(this)" '
        'title="내돈내산 지수 미달로 숨겨진 매장까지 표시">⚠️ 협찬 포함</button>'
        '<button class="util-btn vote-btn" onclick="copyVoteList(this)">📊 순위 복사</button>'
        '<button class="util-btn util-fav-btn" onclick="openFavoritesTab()">♥ 즐겨찾기</button>'
        '</div>'
    )

    rows_html = ""
    for i, r in enumerate(items, start=1):
        growth_badge = f"+{r['growth']}" if r["growth"] > 0 else str(r["growth"])
        map_url = naver_map_link(r["name"], r["region"])
        # 모바일에서 네이버 지도 "앱"을 바로 열기 위한 검색어 (nmap:// 스키마용).
        # 웹 링크(map_url)와 같은 검색어를 쓰되, 스키마 URL은 JS에서 조립한다.
        map_query = urllib.parse.quote(f"{r['name']} {r['region']}")
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

        # 첫 등장 배지: 지난주 언급이 아예 0건이었다가 이번 주 갑자기 나타난 식당.
        # 신규 오픈이거나 이제 막 입소문이 시작된 곳일 가능성이 높다.
        # (별도 저장/조회 없이 이미 있는 last_week 값으로 판별하므로 API 비용 0)
        new_badge_html = (
            '<span class="new-badge">✨ 첫 등장</span>'
            if r["last_week"] == 0 and r["this_week"] > 0 else ""
        )

        # 순위 변동 화살표: 직전 실행(2시간 전)의 전체 순위와 비교.
        # 주의: 이 화살표는 서버가 계산한 "전체 급상승 순위" 기준이라, 화면에서
        # 정렬/필터를 바꿔도 값이 바뀌지 않는다 (그게 의도 - 시간에 따른 변동을
        # 보여주는 것이지, 지금 화면 배치를 보여주는 게 아님).
        delta = r.get("rank_delta")
        if delta is None:
            delta_html = ""  # 직전 실행에 없던 식당 -> 비교 기준이 없어 표시 안 함
        elif delta > 0:
            delta_html = f'<span class="rank-delta delta-up">▲{delta}</span>'
        elif delta < 0:
            delta_html = f'<span class="rank-delta delta-down">▼{-delta}</span>'
        else:
            delta_html = '<span class="rank-delta delta-same">-</span>'

        # 언급량 추세 미니그래프 (기록이 2회 이상 쌓인 식당만 그려짐)
        spark_html = render_sparkline(r.get("spark_points"))

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

        # "급상승률순" 정렬 기준값: 지난 주 대비 상대 증가율. 분모를 max(지난주, 3)으로
        # 잡아서, 지난 주 0~2건이었던 초소형 표본이 무한대/과대 배율로 튀는 것을 막는다
        # (빌드 시점에 계산해서 카드에 심어두고, 정렬은 클라이언트 JS가 수행 - API 비용 0)
        rate_value = round(r["growth"] / max(r["last_week"], 3), 3)

        # 식당 이름은 API에서 오는 외부 데이터라, strip_tags()가 &amp; 등을 일반
        # 문자(&)로 풀어놓은 상태다. 그대로 HTML에 넣으면 이름에 & < > " 가 포함된
        # 식당(예: "밥&술")에서 카드가 깨질 수 있으므로 반드시 이스케이프한다.
        safe_name = escape(r["name"])
        safe_region = escape(r["region"])

        # 협찬 포함 모드용 표시: low_genuine 매장 카드는 기본 숨김(lg-hidden)으로
        # 렌더링하고, 구분용 경고 배지를 단다. 화면의 "협찬 포함" 토글이 켜지면
        # JS가 lg-hidden만 벗겨서 보여준다 (내돈내산 % 배지는 그대로 함께 표시됨).
        lg_class = " lg-card lg-hidden" if r.get("low_genuine") else ""
        lg_badge_html = (
            ' <span class="lg-badge">⚠️ 협찬성 높음</span>'
            if r.get("low_genuine") else ""
        )
        # 참고: 수집 상한(capped) 도달은 화면에 표시하지 않는다 - 순위는 이미
        # 빌드 단계에서 보정됐고, 사용자가 알 필요 없는 내부 사정이라서.
        # 확인이 필요하면 Actions 실행 로그의 ⚠️ 경고나 top8_raw.json의
        # capped 필드를 보면 된다.

        rows_html += f"""
        <a class="card{lg_class}" data-category="{escape(category)}" data-rankmetric="{rank_metric_value}"
           data-thisweek="{r['this_week']}" data-genuine="{genuine_for_sort}"
           data-rate="{rate_value}" data-region="{safe_region}"
           data-mapquery="{map_query}"
           href="{map_url}" target="_blank" rel="noopener">
          <button class="share-btn" data-name="{share_name}" data-map="{map_url}"
                  onclick="event.preventDefault(); event.stopPropagation(); shareCard(this);">🔗</button>
          <button class="fav-btn" data-key="{fav_key}"
                  onclick="event.preventDefault(); event.stopPropagation(); toggleFavorite(this);">♡</button>
          <div class="rank"><span class="rank-num">{i}</span>{delta_html}</div>
          <div class="info">
            <div class="name">{safe_name} <span class="map-icon">📍</span></div>
            <div class="region">{safe_region} · {category} {genuine_badge_html}{lg_badge_html}</div>
            {search_badge_html}
          </div>
          <div class="stats">
            <span class="growth">{growth_badge}</span>
            {streak_badge_html}{new_badge_html}
            <span class="count">이번 주 {r['this_week']}건 · 지난 주 {r['last_week']}건</span>
            {spark_html}
          </div>
        </a>"""

    # 랜덤 뽑기/월드컵/결제 뽑기 버튼은 카드 정렬 대상(card-list) 밖에 별도로 둬서, 정렬 시
    # 카드들이 재배치돼도 항상 맨 아래 고정된다. 월드컵 버튼은 화면에 보이는 카드가
    # 4개 미만이면 JS(updateWorldcupButton)가 숨기고, 나머지 버튼들이 폭을 나눠 갖는다.
    # 세 버튼 모두 flex:1이라 화면 너비를 균등하게 나눈다.
    pick_btn_html = (
        '<div class="action-row">'
        '<button class="pick-btn" onclick="runRandomPick(this)">🎰 오늘 메뉴 랜덤 추천</button>'
        '<button class="wc-btn" onclick="startWorldcup(this)">🏆 이주의 핫플 월드컵</button>'
        '<button class="pay-btn" onclick="openPayModal()">💵 결제할 사람 뽑기</button>'
        '</div>'
    )

    return (
        f'<div class="cat-filter">{category_filter_html}</div>'
        + sort_bar_html
        + f'<div class="card-list">{rows_html}</div>'
        + pick_btn_html
    )


def build_ticker_slides(tabs: dict, all_results=None) -> list:
    """
    롤링 전광판(티커)에 순환 표시할 슬라이드 문구들을 만든다.
    모든 연산과 문구 조립을 백엔드(여기)에서 끝내고, 브라우저 JS는 7초마다
    슬라이드를 전환만 한다 (100% 정적 SSR 원칙 유지, 인터랙션 없음).

    중요: 내돈내산 1위(Slide 3)와 스테디 매장(Slide 4)은 화면의 "전체" 탭
    (상위 8개)이 아니라 all_results(수집된 전체 식당) 중에서 뽑아야 한다.
    지역 탭에만 있는 매장이 더 높은 지수/더 긴 연속 기록을 가질 수 있기 때문.
    (Slide 1은 전 지역 합산 기반, Slide 2는 전국 1위 = 전체 탭 1위라 영향 없음)

    Slide 1 - 상권 대첩 헤드라인 (지역랭킹 기반, 계층형 분기):
      1순위: 1위 지역 증가폭이 0 이하 -> 전체 침체 문구
      2순위: 1위는 양수인데 2위 지역이 없음 -> 단독 독점 문구
      3순위: 2위/1위 비율이 0.90 이상 -> 초박빙 문구
      4순위: 그 외 -> 일반 격차 문구
    Slide 2 - 급상승 1위 식당 (growth > 0인 매장이 1곳도 없으면 슬라이드 제외
              - 전국 하락장에서 하락 매장이 "급상승 1위"로 뜨는 모순 방지)
    Slide 3 - 내돈내산 지수 최고 식당 (유효 지수 보유 식당이 1곳도 없으면 슬라이드 제외)
    Slide 4 - 연속 상승(스테디) 식당 (streak >= STREAK_MIN_DAYS 매장 있을 때만 포함)
    """
    slides = []
    region_ranking = tabs.get("지역랭킹") or []
    # 슬라이드 2~4의 후보 풀: 전체 식당 목록이 있으면 그걸, 없으면(하위 호환) 전체 탭.
    # low_genuine(내돈내산 지수 미달) 매장은 탭 화면과 마찬가지로 티커에서도
    # 표시 대상이 아니므로 풀에서 제외한다 (탭에는 없는 매장이 티커에만 뜨면
    # 이상하니까). 지역랭킹 기반의 슬라이드 1은 합산 통계라 영향 없음.
    pool = all_results if all_results else (tabs.get("전체") or [])
    pool = [r for r in pool if not r.get("low_genuine")]

    if region_ranking:
        top = region_ranking[0]
        second = region_ranking[1] if len(region_ranking) > 1 else None
        if top["total_growth"] <= 0:
            slides.append("📍 이번 주 전국 주요 상권의 언급 트렌드가 전반적으로 차분한 흐름을 보이고 있습니다.")
        elif second is None:
            slides.append(f"📍 현재 {escape(top['region'])} 상권이 상위 트렌드를 압도적 독식 중!")
        else:
            diff = top["total_growth"] - second["total_growth"]
            if second["total_growth"] / top["total_growth"] >= 0.90:
                slides.append(f"⚡ {escape(top['region'])} vs {escape(second['region'])} 단 {diff}건 차이 초박빙!")
            else:
                slides.append(f"🔥 이번 주 {escape(top['region'])}, {escape(second['region'])}보다 {diff}건 차로 화제성 1위!")

    if pool:
        # 급상승 1위(슬라이드 2)는 실제로 "상승 중"인 매장만 자격이 있다.
        # 전국 전멸 상황(명절/서버 점검 등으로 모든 매장이 하락)에서는 pool[0]이
        # "가장 덜 하락한" 매장인데, 필터 없이 쓰면 그 하락 매장이 "급상승 1위"로
        # 박제되는 모순이 생긴다 (그 상황에서 전체 탭은 growth<=0 필터로 비어
        # 있으므로, 탭에 없는 매장이 티커에만 뜨는 불일치이기도 하다).
        # 상승 매장이 하나도 없으면 이 슬라이드는 통째로 생략 - 슬라이드 1의
        # 침체 문구가 상황 설명을 대신한다.
        # 주의: 필터는 이 슬라이드에만 건다. 슬라이드 3(내돈내산 1위)은 급상승이
        # 아니라 신뢰도 지표라 하락 매장도 자격이 있고(지역 탭에는 하락 매장의
        # 지수 배지가 계속 표시되므로, 여기서 빼면 화면 배지와 티커 1위가 어긋나는
        # 새 모순이 생긴다), 슬라이드 4는 apply_streaks()가 growth<=0이면 streak를
        # 0으로 리셋해 구조적으로 상승 매장만 남으므로 별도 필터가 필요 없다.
        risers = [r for r in pool if r["growth"] > 0]
        if risers:
            slides.append(f"🚀 이번 주 언급 급상승 1위 : {escape(risers[0]['name'])}")

        genuines = [r for r in pool if r.get("genuine_ratio") is not None]
        if genuines:  # 유효 지수 보유 식당이 없으면 이 슬라이드는 통째로 제외
            g = max(genuines, key=lambda r: r["genuine_ratio"])
            slides.append(f"💝 내돈내산 1위 : {escape(g['name'])} ({g['genuine_ratio']}%)")

        steadies = [r for r in pool if r.get("streak", 0) >= STREAK_MIN_DAYS]
        if steadies:  # 조건 매장이 없으면 이 슬라이드는 롤링에서 제외
            s = max(steadies, key=lambda r: r["streak"])
            slides.append(f"💎 {escape(s['region'])} 블로그스테디 매장 : {escape(s['name'])} ({s['streak']}일 연속)")

    return slides


def _slim_html(html: str) -> str:
    """
    결과 HTML에서 "통짜 주석 줄"과 들여쓰기, 빈 줄만 벗겨 전송량을 줄인다
    (실측: gzip 기준 약 30% 절감). 소스 코드의 주석은 그대로 유지된다.

    안전성 원칙: 정규식 기반 압축기는 JS 문자열 속 '//'(URL 등)를 오인해
    코드를 깨뜨릴 수 있으므로, 여기서는 "줄 전체가 주석인 경우"만 라인 단위로
    제거한다. 코드와 주석이 한 줄에 섞인 경우(트레일링 주석)는 건드리지 않고
    그대로 둔다 - 몇 바이트 아끼자고 코드가 깨질 위험을 지지 않는다.
    처리 대상: JS의 // 줄, CSS/JS의 /* ... */ 줄(여러 줄 블록 포함),
    HTML의 <!-- ... --> 줄(여러 줄 블록 포함), 줄 앞 들여쓰기, 빈 줄.
    """
    kept = []
    in_css_block = False   # /* ... */ 여러 줄 블록 안인지
    in_html_block = False  # <!-- ... --> 여러 줄 블록 안인지
    for line in html.split("\n"):
        s = line.strip()
        if in_css_block:
            if s.endswith("*/"):
                in_css_block = False
            elif "*/" in s:
                # 블록이 끝나면서 같은 줄에 코드가 이어지는 특이 케이스:
                # 판단하지 않고 통째로 보존 (이 템플릿에는 없는 패턴이지만 방어)
                in_css_block = False
                kept.append(s)
            continue
        if in_html_block:
            if s.endswith("-->"):
                in_html_block = False
            elif "-->" in s:
                in_html_block = False
                kept.append(s)
            continue
        if not s:
            continue  # 빈 줄 제거
        if s.startswith("//"):
            continue  # JS 한 줄 주석 (줄 전체가 주석일 때만)
        if s.startswith("/*"):
            if s.endswith("*/"):
                continue  # 한 줄짜리 /* */ 주석
            in_css_block = True
            continue
        if s.startswith("<!--"):
            if s.endswith("-->"):
                continue  # 한 줄짜리 HTML 주석
            in_html_block = True
            continue
        kept.append(s)  # 들여쓰기 제거된 형태로 보존
    return "\n".join(kept)


def render_html(tabs: dict, total_filtered: int = 0, out_path: str = "index.html", all_results=None):
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
    # "N분 전 갱신" 표시용 생성 시각. GitHub Actions는 UTC로 돌아가므로 KST(UTC+9)로
    # 변환해서 저장하고, 실제 "지금으로부터 몇 분 전"인지는 브라우저에서 JS로 계산한다
    # (그래야 페이지를 열어둔 채로 시간이 지나도 값이 계속 갱신된다).
    # (datetime.utcnow()는 파이썬에서 사용 중단 예고된 함수라 timezone 방식으로 교체)
    generated_at_kst = kst_now()
    generated_at_iso = generated_at_kst.strftime("%Y-%m-%dT%H:%M:%S+09:00")
    # 상단 배지에 넣을 오늘 날짜(KST). 예전엔 헤더 우측 하단에 "2026년 07월 12일"
    # 전체 날짜가 따로 있었는데, 지역 태그와 겹쳐 깨지는 문제로 제거하고
    # 그 대신 상단 배지를 "07.12 BLINK TREND" 형태로 바꿔 날짜 정보를 유지한다.
    badge_date = generated_at_kst.strftime("%m.%d")
    region_tags = " ".join(f"#{r}" for r in REGIONS)  # 헤더에 보이는 "#강남 #성수..." 문구
    # EXTRA_BADGES 리스트에 있는 문구들을 헤더 배지로 하나씩 만든다 (몇 개든 가능)
    extra_badges_html = "".join(
        f'<span class="hero-badge-secondary">{badge}</span>' for badge in EXTRA_BADGES
    )
    overall = tabs.get("전체", [])
    # 주의: overall에는 "협찬 포함" 토글용 숨김(lg) 카드가 뒤에 붙어 있다.
    # 제목/헤더의 "TOP N"이 숨김 카드까지 세면 "TOP 16"처럼 부풀어 보이므로
    # 기본 화면에 실제로 표시되는 분량만 센다.
    visible_overall = [r for r in overall if not r.get("low_genuine")]
    top_n = len(visible_overall) if visible_overall else TOP_N  # 헤더의 "TOP N" 숫자

    # --- 공유 미리보기(OG) 문구 동적화: 빌드 시점의 급상승 1위를 박아 넣어
    # 카톡/문자 링크 미리보기가 "살아있는 정보"가 되게 한다 (2시간마다 갱신되는
    # 정적 페이지의 장점을 공유 카드에도 반영) ---
    if visible_overall:
        _top1 = visible_overall[0]
        og_description = escape(
            f"이번 주 급상승 1위: {_top1['name']} ({_top1['region']}) "
            f"+{_top1['growth']}건 · 2시간마다 자동 갱신"
        )
    else:
        og_description = f"네이버 블로그 언급량 기준, 이번 주 가장 뜨는 맛집 TOP {top_n}을 확인해보세요."

    # --- 롤링 전광판(티커): 문구는 백엔드에서 완성, JS는 7초 순환만 담당 ---
    ticker_slides = build_ticker_slides(tabs, all_results)
    ticker_items_html = "".join(
        f'<div class="ticker-slide{" active" if i == 0 else ""}">{s}</div>'
        for i, s in enumerate(ticker_slides)
    )
    ticker_html = f'<div class="ticker">{ticker_items_html}</div>' if ticker_slides else ""

    # --- SEO 개선 ② 구조화 데이터(JSON-LD) -------------------------------------
    # 검색엔진(특히 Google)이 페이지 내용을 명확히 이해하도록 도와주는 공식 규격.
    # "이 페이지는 식당 목록이고, 각 항목은 이런 이름/지역이다"를 기계가 읽을 수 있는
    # 형태로 명시한다. 화면에는 안 보이지만, 숨겨서 속이는 게 아니라 검색엔진 전용으로
    # 제공하는 정식 메타데이터라 클로킹 문제가 없다 (Google이 공식 지원하는 방식).
    structured_data = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": f"이번 주 블로그 언급 급상승 맛집 TOP {top_n}",  # config의 TOP_N과 자동 연동
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
    # "<"를 \u003c로 치환: 식당 이름에 "</script>" 같은 문자열이 섞여 들어와도
    # <script> 블록이 중간에 끊기지 않도록 방어한다 (JSON 의미는 동일하게 유지됨)
    structured_data_json = json.dumps(structured_data, ensure_ascii=False).replace("<", "\\u003c")

    # 하단 안내 문구: 협찬 제외 건수는 항상 표시하고, 내돈내산 지수 기준으로
    # 필터링하는 기능(MIN_GENUINE_RATIO_TO_SHOW)이 켜져 있으면 그 기준도 같이 안내한다
    # 집계 범위 표기: 배너 배지에 넣었더니 미관을 해쳐서 안내 문구 앞으로 이동.
    # "전체 상위 T개 지역" = 이번 실행의 전체 집계 지역 수(서울 포함),
    # 괄호 안의 "서울 상위 S" = 그중 서울 쿼터로 선정된 권역 수. 둘 다 실제
    # 탭이 생긴 지역만 센다.
    #
    # 주의(과거 표기 버그): 예전엔 "서울 상위 10 · 전체 상위 24개 지역"처럼
    # 나열했는데, T가 이미 S를 포함하는 값인데도 두 수가 병렬로 보여서
    # "10 + 24 = 34개 지역"으로 읽히는 중의성이 있었다. 포함 관계가 표기에서
    # 바로 드러나도록 괄호 종속 형태로 바꿨다.
    _seoul_cnt = len([k for k in tabs if k in set(SEOUL_REGIONS)])
    _total_cnt = len([k for k in tabs if k not in ("전체", "지역랭킹", "서울 전체")])
    _scope_note = (
        f"전체 상위 {_total_cnt}개 지역(서울 상위 {_seoul_cnt} 포함) " if _seoul_cnt
        else (f"상위 {_total_cnt}개 지역 " if _total_cnt else "")
    )
    filter_note = f"{_scope_note}집계 합산 기준 협찬·광고·체험단 추정 게시물 {total_filtered}건 제외"
    if MIN_GENUINE_RATIO_TO_SHOW is not None:
        filter_note += f" · 내돈내산 지수 {MIN_GENUINE_RATIO_TO_SHOW}% 이상 게시글로만 집계"

    # --- 탭 버튼 + 탭 내용물을 미리 문자열로 만들어두기 -------------------------
    # tabs 딕셔너리를 순서대로 돌면서, 탭마다 버튼 하나 + 내용판(panel) 하나씩 생성.
    # 첫 번째 탭(idx==0)만 처음부터 화면에 보이도록 "active" 클래스를 붙인다.
    tab_names = list(tabs.keys())
    tab_buttons_html = ""
    seoul_subtab_html = ""
    tab_panels_html = ""
    _seoul_set = set(SEOUL_REGIONS)
    for idx, name in enumerate(tab_names):
        active_btn = " active" if idx == 0 else ""
        active_panel = " active" if idx == 0 else ""
        # 버튼 배치: 전체/지역랭킹은 메인 줄 고정, "서울 전체"와 서울 소속 권역은
        # 접이식 서울 서브탭 줄로, 나머지 지역은 메인 줄로 간다.
        # 지역 버튼에는 data-region-tab(구분자)과 data-rank(지역랭킹 순번)를 심어
        # 가나다/랭킹 정렬 토글이 각 줄 안에서 버튼만 재배열할 수 있게 한다.
        if name in ("전체", "지역랭킹"):
            tab_buttons_html += f'<button class="tab-btn{active_btn}" data-tab="tab-{idx}">{name}</button>'
        elif name == "서울 전체":
            seoul_subtab_html += f'<button class="tab-btn{active_btn}" data-tab="tab-{idx}">{name}</button>'
        elif name in _seoul_set:
            seoul_subtab_html += (
                f'<button class="tab-btn{active_btn}" data-tab="tab-{idx}" '
                f'data-region-tab="1" data-rank="{idx}">{name}</button>'
            )
        else:
            tab_buttons_html += (
                f'<button class="tab-btn{active_btn}" data-tab="tab-{idx}" '
                f'data-region-tab="1" data-rank="{idx}">{name}</button>'
            )
        # "지역랭킹" 탭만 다른 모양의 카드(render_region_cards)를 쓰고, 나머지는 식당 카드
        panel_content = render_region_cards(tabs[name]) if name == "지역랭킹" else render_cards(tabs[name])
        # data-tabname: 공유 버튼(shareCard)이 "지금 어느 탭인지"를 알아야
        # "부산 급상승순 1위 - ..." 처럼 탭 이름을 공유 문구에 넣을 수 있다
        tab_panels_html += f'<div class="tab-panel{active_panel}" id="tab-{idx}" data-tabname="{name}">{panel_content}</div>'
    # 서울 서브탭이 있으면 메인 줄의 지역랭킹 뒤에 "서울" 접이식 토글 버튼을 끼운다.
    # (tab-btn 클래스가 아니라 seoul-btn - 패널과 직접 연결되지 않는 펼침 버튼)
    if seoul_subtab_html:
        _anchor = '</button>'  # 지역랭킹 버튼 닫힘 직후 위치
        _pos = tab_buttons_html.find('지역랭킹</button>')
        _cut = _pos + len('지역랭킹</button>')
        tab_buttons_html = (
            tab_buttons_html[:_cut]
            + '<button class="seoul-btn" onclick="toggleSeoulTabs(this)">🏙️ 서울 ▾</button>'
            + tab_buttons_html[_cut:]
        )

    # "즐겨찾기" 탭은 서버(파이썬)가 아니라 브라우저(localStorage)가 아는 정보라서
    # 여기서는 빈 틀만 만들어두고, 실제 내용은 페이지가 열릴 때 JS가 채워 넣는다
    # (renderFavoritesTab 함수가 담당 - 다른 탭에 이미 그려진 카드들 중
    # 즐겨찾기 표시된 것만 모아서 이 탭 안에 복사해 넣는 방식)
    # 참고: 예전엔 지역 탭 줄 맨 끝에 "♥ 즐겨찾기" 탭 버튼이 있었는데, 지역이
    # 많아지면서 가로 스크롤 끝에 숨어 잘 안 보여서 유틸리티 줄의 버튼
    # (openFavoritesTab)으로 이동했다. 패널 자체는 그대로 유지된다.
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
<title>이번 주 블로그 언급 급상승 맛집 TOP {top_n}</title>

<!-- 카톡/문자로 링크 공유 시 미리보기 카드에 쓰이는 정보 (문구는 빌드마다
     이번 주 급상승 1위로 갱신됨 - render_html의 og_description 참고) -->
<meta name="description" content="{og_description}">
<meta property="og:title" content="이번 주 블로그 언급 급상승 맛집 TOP {top_n}">
<meta property="og:description" content="{og_description}">
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
    /* env(safe-area-inset-bottom): 아이폰 하단 홈 인디케이터 영역만큼 여백 추가
       (지원 안 하는 브라우저에선 0px로 계산되어 기존과 동일) */
    padding: 24px 16px calc(64px + env(safe-area-inset-bottom, 0px));
    color: #1a1a1a;
    /* 모바일에서 탭/카드 터치 시 나오는 반투명 회색 깜빡임 제거
       (active/hover 스타일이 따로 있어서 피드백은 유지됨) */
    -webkit-tap-highlight-color: transparent;
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
  /* "N분 전 갱신" 문구 전용 독립 행: 지역 태그와 같은 줄에 뭉치지 않게 분리 */
  .hero-update-row {{
    margin-top: 8px;
  }}
  .hero-date {{
    display: inline-block;
    background: rgba(0,0,0,0.2);
    color: rgba(255,255,255,0.9);
    font-weight: 700;
    padding: 5px 10px;
    border-radius: 12px;
    font-size: 11px;
  }}
  /* 업데이트 지연 경고: 마지막 갱신이 3시간을 넘으면 JS(updateRelativeTimeLoop)가
     이 클래스를 붙인다. 정상 주기가 2시간(+빌드 약 15분)이라, 3시간 초과는 예약
     실행 한 회차 이상이 통째로 빠졌다는 뜻 = cron-job.org/PAT/네이버 API/품질
     게이트 중 어딘가가 멈춘 신호. 배지를 붉게 바꿔 열람만으로 이상을 감지한다. */
  .hero-date.stale {{
    background: #dc3545;
    color: #fff;
  }}
  /* --- 배너 색상 테마: 🎨 버튼으로 선택, localStorage 저장 (다크모드와 같은 패턴).
     기본(클래스 없음)=현재 로즈 그라데이션. 서버는 관여하지 않고 JS가 .hero에
     ht-v* 클래스만 붙였다 뗀다. 새 테마 추가 시 여기 CSS + 아래 팔레트 점 + JS의
     HERO_THEMES 배열까지 세 곳을 함께 수정할 것 --- */
  .hero.ht-v2 {{ background: linear-gradient(135deg, #1e1b4b, #4338ca, #7c3aed); }}
  .hero.ht-v2 .hero-badge-secondary {{ color: #4338ca; }}
  .hero.ht-v3 {{ background: linear-gradient(to top right, #064e3b, #059669, #34d399); }}
  .hero.ht-v3 .hero-badge-secondary {{ color: #047857; }}
  .hero.ht-v4 {{ background: linear-gradient(to top right, #7c2d92, #db2777, #f59e0b); }}
  .hero.ht-v4 .hero-badge-secondary {{ color: #a21caf; }}
  .hero.ht-v5 {{ background: linear-gradient(to top right, #0c4a6e, #0284c7, #22d3ee); }}
  .hero.ht-v5 .hero-badge-secondary {{ color: #0369a1; }}
  .hero.ht-v6 {{ background: #1c1c1e; }}
  .hero.ht-v6 .hero-icon {{ color: #ff6b35; opacity: 0.25; }}
  .hero.ht-v6 .hero-badge {{ background: rgba(255,107,53,0.18); color: #ff8c5a; }}
  .hero.ht-v6 .hero-badge-secondary {{ background: #ff6b35; color: #fff; }}
  .hero.ht-v6 h1 {{ background: linear-gradient(90deg, #ff6b35, #ffb347);
    -webkit-background-clip: text; background-clip: text; color: transparent; }}
  .hero.ht-v6 .hero-date {{ background: rgba(255,255,255,0.1); }}
  .hero.ht-v7 {{ background:
      radial-gradient(at 20% 0%, #f43f5e 0%, transparent 55%),
      radial-gradient(at 90% 15%, #fb923c 0%, transparent 55%),
      radial-gradient(at 80% 100%, #8b5cf6 0%, transparent 55%),
      radial-gradient(at 10% 90%, #ec4899 0%, transparent 55%),
      #be185d; }}
  .hero.ht-v8 {{ background: #fff; color: #1a1a1a; box-shadow: 0 8px 24px rgba(0,0,0,0.08); }}
  .hero.ht-v8 .hero-icon {{ color: #f43f5e; opacity: 0.12; }}
  .hero.ht-v8 .hero-badge {{ background: #fee2e2; color: #dc2626; }}
  .hero.ht-v8 .hero-badge-secondary {{ background: #1a1a1a; color: #fff; }}
  .hero.ht-v8 h1 {{ color: #1a1a1a; }}
  .hero.ht-v8 .hero-meta {{ color: #888; }}
  .hero.ht-v8 .hero-date {{ background: #f3f4f6; color: #666; }}
  .hero.ht-v8 .hero-date.stale {{ background: #dc3545; color: #fff; }}
  .hero.ht-v8 .theme-toggle {{ background: #f3f4f6; }}
  body.dark .hero.ht-v8 {{ background: #2c2c2e; color: #eee; }}
  body.dark .hero.ht-v8 h1 {{ color: #eee; }}
  body.dark .hero.ht-v8 .hero-meta {{ color: #aaa; }}
  body.dark .hero.ht-v8 .hero-date {{ background: #3a3a3c; color: #aaa; }}
  body.dark .hero.ht-v8 .hero-date.stale {{ background: #dc3545; color: #fff; }}
  body.dark .hero.ht-v8 .theme-toggle {{ background: #3a3a3c; }}
  .hero.ht-v9 {{ background: linear-gradient(160deg, #0f0f0f 40%, #2d1f00 100%);
    border: 1px solid rgba(212,175,55,0.35); }}
  .hero.ht-v9 .hero-icon {{ color: #d4af37; opacity: 0.2; }}
  .hero.ht-v9 .hero-badge {{ background: rgba(212,175,55,0.15); color: #e6c96a; }}
  .hero.ht-v9 .hero-badge-secondary {{ background: #d4af37; color: #1a1a1a; }}
  .hero.ht-v9 h1 {{ color: #f5e6b8; }}
  .hero.ht-v9 .hero-meta {{ color: rgba(230,201,106,0.8); }}
  .hero.ht-v9 .hero-date {{ background: rgba(255,255,255,0.08); }}
  /* 🎨 배너 색상 버튼 (정렬 토글 줄 오른쪽 끝) + 색상 점 팔레트 줄 */
  .hero-theme-btn {{
    border: none;
    background: transparent;
    font-size: 17px;
    padding: 4px 2px;
    cursor: pointer;
    line-height: 1;
  }}
  .hero-theme-pop {{
    max-width: 560px;
    margin: 0 auto 6px;
    display: none;
    gap: 9px;
    flex-wrap: wrap;
    justify-content: flex-end;
    padding: 2px 2px 4px;
  }}
  .hero-theme-pop.open {{ display: flex; }}
  .ht-dot {{
    width: 26px;
    height: 26px;
    border-radius: 50%;
    border: 2px solid rgba(0,0,0,0.12);
    cursor: pointer;
    padding: 0;
    flex: 0 0 auto;
  }}
  .ht-dot.active {{ border-color: #1a1a1a; transform: scale(1.15); }}
  body.dark .ht-dot {{ border-color: rgba(255,255,255,0.2); }}
  body.dark .ht-dot.active {{ border-color: #fff; }}
  /* 탭 영역 래퍼: 메인 탭 줄 + 서울 서브탭 줄을 함께 상단 고정(sticky).
     배경색을 페이지와 같게 깔아 카드가 탭 뒤로 지나갈 때 비치지 않게 한다.
     (예전엔 .tabs 단일 줄에 sticky가 있었는데, 서울 서브탭이 생기면서
      두 줄을 같이 고정해야 해서 래퍼로 이동) */
  .tabs-area {{
    position: sticky;
    top: 0;
    z-index: 20;
    background: #f5f5f7;
    padding-top: 8px;
    margin-bottom: 14px;
    transition: background 0.25s;
  }}
  body.dark .tabs-area {{
    background: #14161b;
  }}
  .tabs {{
    max-width: 560px;
    margin: 0 auto;
    display: flex;
    gap: 6px;
    overflow-x: auto;
    padding-bottom: 4px;
  }}
  /* 서울 접이식 서브탭 줄: 기본 접힘, 서울 버튼으로 펼침 */
  .seoul-subtabs {{
    display: none;
    margin-top: 6px;
  }}
  .seoul-subtabs.open {{
    display: flex;
  }}
  /* 서울 토글 버튼: 브랜드 주황과 구분되는 파란 계열 전용 색 */
  .seoul-btn {{
    flex: 0 0 auto;
    border: none;
    background: #e8edff;
    color: #3d5af1;
    font-size: 13px;
    font-weight: 700;
    padding: 8px 16px;
    border-radius: 999px;
    cursor: pointer;
    white-space: nowrap;
  }}
  .seoul-btn.open,
  .seoul-btn.seoul-active {{
    background: #3d5af1;
    color: white;
  }}
  body.dark .seoul-btn {{
    background: #1d2440;
    color: #8fa3ff;
  }}
  body.dark .seoul-btn.open,
  body.dark .seoul-btn.seoul-active {{
    background: #3d5af1;
    color: white;
  }}
  /* 서울 서브탭의 활성 탭도 서울 전용 파란색 */
  .seoul-subtabs .tab-btn.active {{
    background: #3d5af1;
  }}
  /* 정렬 토글 독립 줄: 배너/티커와 탭 사이, 전체 탭과 같은 왼쪽 라인 */
  .tab-tools {{
    max-width: 560px;
    margin: 0 auto 6px;
    display: flex;
    justify-content: space-between; /* 좌: 정렬 토글 / 우: 🎨 배너 색상 버튼 */
    align-items: center;
  }}
  /* iOS 설정풍 토글 스위치: 트랙 위 원형 썸이 좌우로 미끄러지고,
     옆 라벨이 현재 모드(🔥 랭킹순 / 🔤 가나다순)를 이모지와 함께 표기 */
  .tab-order-btn {{
    display: flex;
    align-items: center;
    gap: 7px;
    border: none;
    background: transparent;
    padding: 4px 2px;
    cursor: pointer;
  }}
  .tot-track {{
    width: 36px;
    height: 21px;
    border-radius: 999px;
    background: #d5d5db;
    position: relative;
    transition: background 0.2s;
    flex: 0 0 auto;
  }}
  .tot-thumb {{
    position: absolute;
    top: 2.5px;
    left: 2.5px;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: white;
    box-shadow: 0 1px 3px rgba(0,0,0,0.25);
    transition: transform 0.2s;
  }}
  .tab-order-btn.on .tot-track {{
    background: #ff5a36;
  }}
  .tab-order-btn.on .tot-thumb {{
    transform: translateX(15px);
  }}
  .tot-label {{
    font-size: 12px;
    font-weight: 700;
    color: #666;
  }}
  body.dark .tot-track {{
    background: #3a3f4a;
  }}
  body.dark .tab-order-btn.on .tot-track {{
    background: #ff5a36;
  }}
  body.dark .tot-label {{
    color: #9aa0ab;
  }}
  /* 가로 스크롤 칩 바(지역 탭/카테고리/정렬)의 스크롤바 처리.
     주의(과거 버그): 예전엔 숨김을 전역 적용했더니 데스크톱에서 드래그할
     스크롤바가 사라지고 마우스 휠은 세로 전용이라, 화면 밖 지역 탭에 아예
     접근할 수 없었다. 그래서 숨김은 터치 스와이프가 되는 모바일에만 걸고,
     데스크톱은 얇은 스크롤바 + 휠 가로 변환(JS)으로 이동 수단을 보장한다. */
  @media (max-width: 520px) {{
    .tabs, .cat-filter, .sort-bar {{
      scrollbar-width: none;      /* Firefox */
    }}
    .tabs::-webkit-scrollbar,
    .cat-filter::-webkit-scrollbar,
    .sort-bar::-webkit-scrollbar {{
      display: none;              /* Chrome/Safari */
    }}
  }}
  @media (min-width: 521px) {{
    .tabs, .cat-filter, .sort-bar {{
      scrollbar-width: thin;                       /* Firefox */
      scrollbar-color: #d0d0d5 transparent;
    }}
    body.dark .tabs, body.dark .cat-filter, body.dark .sort-bar {{
      scrollbar-color: #3a3f4a transparent;
    }}
    .tabs::-webkit-scrollbar,
    .cat-filter::-webkit-scrollbar,
    .sort-bar::-webkit-scrollbar {{
      height: 5px;                                 /* Chrome/Safari: 얇게 */
    }}
    .tabs::-webkit-scrollbar-thumb,
    .cat-filter::-webkit-scrollbar-thumb,
    .sort-bar::-webkit-scrollbar-thumb {{
      background: #d0d0d5;
      border-radius: 999px;
    }}
    body.dark .tabs::-webkit-scrollbar-thumb,
    body.dark .cat-filter::-webkit-scrollbar-thumb,
    body.dark .sort-bar::-webkit-scrollbar-thumb {{
      background: #3a3f4a;
    }}
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
    display: flex;
    flex-direction: column;
    align-items: center;
    line-height: 1.1;
  }}
  .rank-delta {{
    font-size: 9px;
    font-weight: 700;
  }}
  .delta-up {{
    color: #e03131;
  }}
  .delta-down {{
    color: #4c6ef5;
  }}
  .delta-same {{
    color: #ccc;
  }}
  .spark {{
    color: #ffb09c;
    display: block;
  }}
  .info {{
    flex: 1;
    min-width: 0;   /* flex 텍스트 컨테이너 표준 위생: 자식이 아무리 길어도
                       카드 폭 안에서 줄바꿈되게 (모바일 쿼리와 규칙 일관) */
  }}
  .name {{
    font-size: 16px;
    font-weight: 700;
    word-break: keep-all;       /* 한글 이름이 단어(어절) 단위로만 줄바꿈되게 */
    overflow-wrap: anywhere;    /* 공백 없는 초장문 이름도 카드 밖으로 안 넘치게 */
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
  .new-badge {{
    font-size: 10px;
    font-weight: 700;
    color: #6d28d9;
    background: #f1ebfd;
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

  /* --- 모바일(좁은 화면) 전용: 카드 2단 배치 -------------------------------
     기본 레이아웃(순위|이름|통계 3열 가로 배치)은 좁은 화면에서 통계 열과
     공유/하트 버튼 예약 공간이 오른쪽을 고정으로 차지해, 이름이 쓸 수 있는
     폭이 100px 안팎으로 줄어 긴 매장명("강릉원조누룽지 황금오징어순대..."류)이
     2~3줄로 꺾이는 문제가 있었다. 그래서 520px 이하에서는:
       1행 = 순위 + 이름/지역/배지 (화면 폭 전체 사용 -> 긴 이름도 한 줄)
       2행 = 증가폭 + 연속/첫등장 배지 + 언급 건수 + 스파크라인 (오른쪽 정렬)
     로 재배치한다. HTML 구조는 그대로 두고 flex-wrap만으로 처리하므로
     JS(정렬/복사/월드컵의 .name 파싱 등)에는 아무 영향이 없다.
     지역랭킹 탭 카드도 같은 클래스를 쓰므로 동일하게 정돈된다. --- */
  @media (max-width: 520px) {{
    .card {{
      flex-wrap: wrap;
      padding: 14px 16px 12px 16px;  /* 버튼용 오른쪽 66px 예약 해제 -> 폭 회수 */
      gap: 0 12px;
    }}
    .info {{
      flex: 1;
      min-width: 0;
      /* 이름 행만 우상단의 공유(🔗)/하트(♡) 절대배치 버튼을 피하면 되므로,
         카드 전체가 아니라 info에만 오른쪽 여백을 준다 (2행은 폭 전부 사용) */
      padding-right: 52px;
    }}
    .stats {{
      flex-basis: 100%;             /* 다음 줄로 내려서 카드 폭 전체를 쓴다 */
      flex-direction: row;          /* 세로 스택 -> 가로 한 줄 */
      flex-wrap: wrap;              /* 배지가 많으면 자연스럽게 다음 줄로 */
      align-items: center;
      justify-content: flex-end;
      gap: 4px 10px;
      margin-top: 10px;
      padding-top: 9px;
      /* 반투명 회색이라 라이트/다크 양쪽 모드에서 별도 오버라이드 없이 동작 */
      border-top: 1px solid rgba(125, 125, 125, 0.14);
    }}
    .growth {{
      font-size: 15px;              /* 한 줄 배치에서 증가폭이 묻히지 않게 살짝 강조 */
      margin-right: auto;           /* 증가폭만 왼쪽 끝, 나머지는 오른쪽 정렬 */
    }}
    /* 하트/공유 버튼 터치 영역 확대: 기존 22x22px는 모바일 권장 크기의 절반이라
       옆의 카드 링크(네이버 지도)가 대신 눌리는 오터치가 잦았다. 아이콘 크기와
       중심점은 그대로 두고 투명한 히트 영역만 32x32px로 키운다.
       (공유 버튼 왼쪽 끝 = 오른쪽에서 33+32=65px <- .info의 padding-right
        52px + .card의 padding 16px = 68px 여유 안쪽이라 매장명과 안 겹침) */
    .fav-btn {{
      width: 32px;
      height: 32px;
      top: 5px;                     /* 기존 중심(top10+11=21px) 유지: 5+16=21 */
      right: 5px;                   /* 기존 중심(right10+11=21px) 유지 */
    }}
    .share-btn {{
      width: 32px;
      height: 32px;
      top: 5px;
      right: 33px;                  /* 기존 중심(right38+11=49px) 유지: 33+16=49 */
    }}
  }}

  /* --- 다크모드 전환 버튼 (헤더 오른쪽 위 고정 - 지역 탭들과 분리된 독립 위치) --- */
  .theme-toggle {{
    position: absolute;
    top: 14px;
    right: 14px;
    z-index: 2;
    width: 36px;
    height: 36px;
    border-radius: 50%;
    border: none;
    background: rgba(255,255,255,0.25);
    font-size: 17px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0;
  }}

  /* --- 다크모드: body에 dark 클래스가 붙으면 아래 색상들로 덮어씌워진다.
       (헤더의 그라데이션과 포인트 주황색은 브랜드 색이라 그대로 유지) --- */
  body {{
    transition: background 0.25s, color 0.25s;
  }}
  body.dark {{
    background: #14161b;
    color: #e8e8ea;
  }}
  body.dark .card {{
    background: #1e2129;
    box-shadow: 0 1px 3px rgba(0,0,0,0.5);
  }}
  body.dark .card:hover {{
    box-shadow: 0 4px 12px rgba(0,0,0,0.6);
  }}
  body.dark .tab-btn {{
    background: #1e2129;
    color: #9aa0ab;
  }}
  body.dark .tab-btn.active {{
    background: #ff5a36;
    color: white;
  }}
  body.dark .cat-btn {{
    background: #2a2e38;
    color: #9aa0ab;
  }}
  body.dark .cat-btn.active {{
    background: #e8e8ea;
    color: #14161b;
  }}
  body.dark .sort-btn {{
    background: #1e2129;
    border-color: #2a2e38;
    color: #9aa0ab;
  }}
  body.dark .sort-btn.active {{
    background: #32201a;
    border-color: #ff5a36;
    color: #ff8a66;
  }}
  body.dark .region,
  body.dark .count {{
    color: #8a8f99;
  }}
  body.dark .filter-note {{
    color: #666;
  }}
  body.dark .fav-btn,
  body.dark .share-btn {{
    color: #555c68;
  }}
  body.dark .fav-btn.active {{
    color: #ff5a36;
  }}
  body.dark .delta-same {{
    color: #555c68;
  }}
  body.dark .spark {{
    color: #ff8a66;
  }}
  body.dark .genuine-high {{
    background: rgba(15,138,79,0.2);
    color: #5ad08f;
  }}
  body.dark .genuine-mid {{
    background: rgba(184,114,10,0.2);
    color: #e6a23c;
  }}
  body.dark .genuine-low {{
    background: rgba(201,42,42,0.2);
    color: #ff7b7b;
  }}
  body.dark .streak-badge {{
    background: rgba(217,55,110,0.2);
  }}
  body.dark .new-badge {{
    background: rgba(109,40,217,0.25);
    color: #b79df5;
  }}
  body.dark .search-badge {{
    background: rgba(15,110,156,0.22);
    color: #6cc4ee;
  }}
  body.dark .pick-modal-box {{
    background: #1e2129;
    color: #e8e8ea;
  }}
  body.dark .pick-modal-close-btn {{
    background: #2a2e38;
    color: #9aa0ab;
  }}

  /* --- 롤링 전광판: 클릭/호버 무반응(pointer-events:none) 순수 자동 롤링.
       전환 시 현재 문구는 위로 밀려 나가고 다음 문구가 아래에서 올라온다.
       이름이 아무리 길어도 한 줄로 말려 들어가 레이아웃이 안 깨진다 --- */
  .ticker {{
    max-width: 560px;
    height: 37px;
    margin: 0 auto 12px;
    background: white;
    border-radius: 12px;
    padding: 0 14px;
    font-size: 12px;
    font-weight: 700;
    color: #555;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    overflow: hidden;
    pointer-events: none;
    box-sizing: border-box;
    position: relative;
  }}
  .ticker-slide {{
    position: absolute;
    left: 14px;
    right: 14px;
    top: 0;
    line-height: 37px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    transform: translateY(100%);  /* 기본 대기 위치: 아래쪽 (보이지 않음) */
    opacity: 0;
    transition: transform 0.45s ease, opacity 0.45s ease;
  }}
  .ticker-slide.active {{
    transform: translateY(0);     /* 현재 표시 위치 */
    opacity: 1;
  }}
  .ticker-slide.leaving {{
    transform: translateY(-100%); /* 위로 밀려 나가는 위치 */
    opacity: 0;
  }}
  .ticker-slide.no-transition {{
    transition: none;             /* 대기 위치로 즉시 리셋할 때 사용 (JS) */
  }}
  body.dark .ticker {{
    background: #1e2129;
    color: #9aa0ab;
  }}

  /* --- 하단 액션 버튼 줄 (랜덤 추천 + 월드컵) --- */
  .action-row {{
    display: flex;
    gap: 8px;
    margin-top: 4px;
  }}
  .action-row .pick-btn {{
    flex: 1;
    margin: 0;
    max-width: none;
  }}
  .wc-btn {{
    flex: 1;
    border: none;
    background: linear-gradient(to right, #6d28d9, #4c6ef5);
    color: white;
    font-size: 14px;
    font-weight: 800;
    padding: 14px;
    border-radius: 14px;
    cursor: pointer;
  }}
  .wc-btn.hidden {{
    display: none;
  }}
  /* --- 결제할 사람 뽑기 버튼 & 모달 입력 UI --- */
  .pay-btn {{
    flex: 1;
    border: none;
    background: linear-gradient(to right, #0d9488, #059669);
    color: white;
    font-size: 14px;
    font-weight: 800;
    padding: 14px;
    border-radius: 14px;
    cursor: pointer;
  }}
  .pay-field {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin: 10px 0;
    font-size: 13px;
    font-weight: 700;
    color: #666;
  }}
  .pay-field input[type="number"] {{
    width: 72px;
    padding: 8px 10px;
    border: 1px solid #eee;
    border-radius: 10px;
    font-size: 14px;
    font-weight: 700;
    text-align: center;
  }}
  #pay-names {{
    display: flex;
    flex-direction: column;
    gap: 6px;
    max-height: 40vh;       /* 인원이 많아도 모달이 화면을 넘지 않게 */
    overflow-y: auto;
    margin: 10px 0;
  }}
  #pay-names input {{
    width: 100%;
    box-sizing: border-box;
    padding: 9px 12px;
    border: 1px solid #eee;
    border-radius: 10px;
    font-size: 14px;
  }}
  .pay-draw-btn {{
    width: 100%;
    margin-top: 6px;
    border: none;
    background: linear-gradient(to right, #0d9488, #059669);
    color: white;
    font-size: 14px;
    font-weight: 800;
    padding: 13px;
    border-radius: 12px;
    cursor: pointer;
  }}
  body.dark .pay-field input[type="number"],
  body.dark #pay-names input {{
    background: #2a2e38;
    border-color: #3a3f4a;
    color: #e6e8ec;
  }}
  /* 월드컵 선택 버튼: 좁은 화면에서 "지역·카테고리·이름"을 한 줄로 이으면
     글자가 꺾이거나 잘리므로, 메타(위)/이름(아래) 수직 스택으로 분리한다 */
  .wc-choice {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
    width: 100%;
    border: 2px solid #eee;
    background: white;
    color: inherit;
    padding: 13px 10px;
    border-radius: 14px;
    cursor: pointer;
  }}
  .wc-choice-meta {{
    font-size: 11px;
    font-weight: 700;
    color: #999;
  }}
  .wc-choice-name {{
    display: block;
    width: 100%;
    font-size: 16px;
    font-weight: 800;
    white-space: nowrap;      /* 긴 이름은 줄바꿈 대신 */
    overflow: hidden;         /* 넘치는 부분을 숨기고 */
    text-overflow: ellipsis;  /* ...으로 잘라 레이아웃 터짐을 방어 */
  }}
  .wc-choice:hover {{
    border-color: #ff5a36;
    color: #ff5a36;
  }}
  .wc-choice:hover .wc-choice-name {{
    color: #ff5a36;
  }}
  .wc-vs {{
    font-size: 12px;
    font-weight: 800;
    color: #999;
    margin: 8px 0;
  }}
  body.dark .wc-choice {{
    background: #2a2e38;
    border-color: #3a3f4a;
  }}

  /* --- 현재 순위 매장 항목 복사 버튼 --------------------------------------
     정렬 바 안에 인라인으로 붙어 있으면 정렬 버튼으로 오인 클릭되기 쉬워서,
     정렬 바와 카드 리스트 사이의 독립된 한 줄(utility-row)에 와이드로 단독 배치 */
  .utility-row {{
    display: flex;
    gap: 8px;
    margin-bottom: 10px;
  }}
  .util-btn {{
    flex: 1;
    min-width: 0;
    border: 1px dashed #ddd;
    background: white;
    color: #666;
    font-size: 12px;
    font-weight: 700;
    padding: 9px 4px;
    border-radius: 12px;
    cursor: pointer;
    white-space: nowrap;
    overflow: hidden;         /* 320px급 좁은 화면에서 라벨이 버튼 밖으로 */
    text-overflow: ellipsis;  /* 삐져나와 옆 버튼과 겹치는 대신 "…"로 잘리게 */
  }}
  body.dark .util-btn {{
    background: #1e2129;
    border-color: #2a2e38;
    color: #9aa0ab;
  }}
  /* 즐겨찾기 버튼: 목록 조작 버튼들(협찬/복사)과 성격이 달라(화면 이동)
     핑크 톤 + 왼쪽 여백으로 살짝 떼어 구분한다 */
  .util-fav-btn {{
    margin-left: 8px;           /* 기본 gap 8px에 더해 총 16px 간격 */
    border-color: #ffc4d6;
    background: #fff5f8;
    color: #e0447a;
  }}
  body.dark .util-fav-btn {{
    border-color: #4a2733;
    background: #2b1a22;
    color: #ff8ab0;
  }}
  /* "협찬 포함" 토글이 켜진 상태 표시 */
  .lg-toggle-btn.active {{
    border-style: solid;
    border-color: #ff5a36;
    background: #fff0eb;
    color: #ff5a36;
  }}
  body.dark .lg-toggle-btn.active {{
    background: #32201a;
    border-color: #ff5a36;
    color: #ff8a66;
  }}
  /* 협찬성 매장 카드: 기본 숨김. "협찬 포함" 토글이 켜지면 JS가
     lg-hidden 클래스를 벗겨서 보여준다 (cat-hidden과는 독립적으로 동작) */
  .card.lg-hidden {{
    display: none;
  }}
  .lg-badge {{
    font-size: 10px;
    font-weight: 700;
    color: #8a6d3b;
    background: #f7ecd8;
    padding: 2px 7px;
    border-radius: 999px;
    display: inline-block;
  }}
  body.dark .lg-badge {{
    background: #3a3020;
    color: #d9b36a;
  }}
  /* 지역랭킹 탭: 급상승 슬롯(전일 대비 성장률)으로 선정된 지역 구분 배지 */
  .rising-badge {{
    font-size: 10px;
    font-weight: 700;
    color: #ff5a36;
    background: #fff0eb;
    padding: 2px 7px;
    border-radius: 999px;
    display: inline-block;
  }}
  body.dark .rising-badge {{
    background: #32201a;
    color: #ff8a66;
  }}

  /* --- 즐겨찾기 목록 통째로 공유 버튼 --- */
  .fav-share-btn {{
    width: 100%;
    margin-top: 10px;
    border: none;
    background: linear-gradient(to right, #ec4899, #f43f5e);
    color: white;
    font-size: 14px;
    font-weight: 800;
    padding: 14px;
    border-radius: 14px;
    cursor: pointer;
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
  /* 다크모드 전용 재선언 필수: body.dark .card의 box-shadow가 명시도에서
     .card.picking을 이겨버려서, 이 규칙이 없으면 랜덤 추천 슬롯이 돌 때
     반짝이는 주황 링이 다크모드에서 전혀 안 보인다 */
  body.dark .card.picking {{
    box-shadow: 0 0 0 3px #ff8a66;
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
    position: relative;  /* 우상단 즐겨찾기 하트(absolute)의 기준점 */
  }}
  /* 모달 우상단 즐겨찾기 하트 (랜덤 추천 결과 / 월드컵 우승 공용).
     카드의 .fav-btn과 저장소·키를 공유하지만, 스타일은 모달에 맞게 크게 별도 정의 */
  .modal-fav-btn {{
    position: absolute;
    top: 10px;
    right: 12px;
    width: 40px;
    height: 40px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: none;
    background: transparent;
    font-size: 28px;
    line-height: 1;
    color: #ccc;
    cursor: pointer;
    padding: 0;
  }}
  .modal-fav-btn.active {{ color: #ff5a36; }}
  body.dark .modal-fav-btn {{ color: #555; }}
  body.dark .modal-fav-btn.active {{ color: #ff5a36; }}
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
  /* 결과 모달의 지역 · 카테고리 서브라인 (랜덤 추천/월드컵 우승 공용).
     이름의 margin-bottom(22px)과 여기의 -16px가 마진 상쇄로 합쳐져
     이름-서브라인 간격은 6px, 서브라인-버튼 간격은 22px이 된다.
     메타 정보가 없는 카드면 JS가 display:none으로 숨기는데, 그 경우
     이름의 22px 마진이 그대로 살아나 기존 레이아웃과 동일해진다. */
  .pick-modal-sub {{
    font-size: 13px;
    color: #999;
    font-weight: 700;
    margin: -16px 0 22px;
  }}
  body.dark .pick-modal-sub {{
    color: #8a8f99;
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
    <!-- 다크모드 전환 버튼: 지역 탭 줄과 분리된 고정 위치 (항상 같은 자리) -->
    <button class="theme-toggle" id="theme-toggle" onclick="toggleTheme()" aria-label="다크모드 전환">🌙</button>
    <div class="hero-icon">
      <svg width="160" height="160" fill="currentColor" viewBox="0 0 24 24">
        <path d="M17.66 11.57c-.77-3.95-2.85-6.86-5.27-9.4c-.25-.26-.68-.15-.77.19-.53 2.11-.96 4.98-2.5 7-1.72 2.25-3.68 3.19-4.43 5.92C3.96 18.02 6.07 22 10 22c4.83 0 8.64-4.08 7.66-10.43z"/>
      </svg>
    </div>
    <div class="hero-inner">
      <div class="hero-badge-row">
        <span class="hero-badge">{badge_date} BLINK TREND</span>
        {extra_badges_html}
      </div>
      <h1>이주의 급상승<br>맛집 TOP {top_n}</h1>
      <!-- 지역 태그가 늘어나면 우측 시간 문구와 겹쳐 깨지는 문제가 있어서,
           고정 날짜는 제거하고 "N분 전 갱신" 문구만 태그 아래 독립된 줄로 분리했다
           (날짜 정보는 어차피 갱신 시각 문구가 대신하므로 중복이었음) -->
      <div class="hero-meta">
        <span>{region_tags}</span>
      </div>
      <div class="hero-update-row">
        <span class="hero-date"><span id="update-relative">방금 갱신됨</span></span>
      </div>
    </div>
  </div>
  <!-- 롤링 전광판: 백엔드가 구운 슬라이드를 JS가 7초마다 순환.
       클릭/호버 어떤 인터랙션도 받지 않는 순수 자동 롤링 (pointer-events:none) -->
  {ticker_html}
  <!-- 지역 탭 정렬 토글: 배너/티커와 탭 줄 사이의 독립된 작은 줄.
       발견성을 위해 점선 칩 대신 iOS 설정풍 토글 스위치로 - 끔(회색)=랭킹순(기본),
       켬(주황)=가나다순. 위치는 전체 탭과 같은 왼쪽 라인 -->
  <div class="tab-tools">
    <button class="tab-order-btn" onclick="toggleTabOrder()" role="switch" aria-checked="false"
            title="지역 탭 나열 순서 전환">
      <span class="tot-track"><span class="tot-thumb"></span></span>
      <span class="tot-label">🔥 랭킹순</span>
    </button>
    <!-- 배너 색상 변경: 누르면 아래 색상 점 팔레트 줄이 펼쳐진다 -->
    <button class="hero-theme-btn" onclick="toggleHeroThemePop()" title="배너 색상 변경" aria-label="배너 색상 변경">🎨</button>
  </div>
  <!-- 배너 색상 팔레트: 점 하나가 테마 하나. 선택은 localStorage에 저장되어 재방문 시 유지.
       점의 배경은 각 테마의 대표색(그라데이션 축약판) -->
  <div class="hero-theme-pop" id="hero-theme-pop">
    <button class="ht-dot" data-ht="" style="background:linear-gradient(45deg,#f43f5e,#fb923c)" title="로즈 (기본)" aria-label="로즈 (기본)"></button>
    <button class="ht-dot" data-ht="ht-v2" style="background:linear-gradient(45deg,#1e1b4b,#7c3aed)" title="미드나잇" aria-label="미드나잇"></button>
    <button class="ht-dot" data-ht="ht-v3" style="background:linear-gradient(45deg,#064e3b,#34d399)" title="에메랄드" aria-label="에메랄드"></button>
    <button class="ht-dot" data-ht="ht-v4" style="background:linear-gradient(45deg,#7c2d92,#f59e0b)" title="선셋 퍼플" aria-label="선셋 퍼플"></button>
    <button class="ht-dot" data-ht="ht-v5" style="background:linear-gradient(45deg,#0c4a6e,#22d3ee)" title="청량 블루" aria-label="청량 블루"></button>
    <button class="ht-dot" data-ht="ht-v6" style="background:linear-gradient(45deg,#1c1c1e 55%,#ff6b35)" title="차콜 네온" aria-label="차콜 네온"></button>
    <button class="ht-dot" data-ht="ht-v7" style="background:radial-gradient(at 30% 30%,#fb923c,#ec4899 60%,#8b5cf6)" title="메쉬" aria-label="메쉬"></button>
    <button class="ht-dot" data-ht="ht-v8" style="background:#fff" title="미니멀 라이트" aria-label="미니멀 라이트"></button>
    <button class="ht-dot" data-ht="ht-v9" style="background:linear-gradient(45deg,#0f0f0f 55%,#d4af37)" title="골드 블랙" aria-label="골드 블랙"></button>
  </div>
  <!-- 탭 영역: 메인 줄(전체/지역랭킹/서울토글/비서울 지역) + 서울 접이식 서브탭 줄.
       스크롤 고정(sticky)은 두 줄을 함께 감싸는 이 래퍼가 담당한다 -->
  <div class="tabs-area">
    <div class="tabs">
      {tab_buttons_html}
    </div>
    <div class="tabs seoul-subtabs" id="seoul-subtabs">
      {seoul_subtab_html}
    </div>
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
      <!-- 우상단 즐겨찾기 하트: 뽑힌 식당을 카드 하트와 같은 저장소에 추가/해제 -->
      <button class="modal-fav-btn" id="pick-modal-fav" onclick="toggleModalFav(this)" aria-label="즐겨찾기 추가/해제">♡</button>
      <div class="pick-modal-label">오늘 당신의 픽은</div>
      <div class="pick-modal-name" id="pick-modal-name">-</div>
      <div class="pick-modal-sub" id="pick-modal-sub"></div>
      <div class="pick-modal-buttons">
        <a id="pick-modal-map" href="#" target="_blank" rel="noopener" class="pick-modal-map-btn">지도에서 보기</a>
        <button class="pick-modal-close-btn" onclick="sharePick(this)">🎰 공유</button>
        <button class="pick-modal-close-btn" onclick="closePickModal()">닫기</button>
      </div>
    </div>
  </div>
  <!-- 맛집 월드컵 모달: 화면에 보이는 카드들로 8강/4강 토너먼트를 진행하고,
       결승이 끝나면 같은 상자 안에서 우승 결과 화면으로 전환된다 -->
  <div id="wc-modal" class="pick-modal-overlay" onclick="if(event.target===this) closeWorldcup()">
    <div class="pick-modal-box">
      <div class="pick-modal-label" id="wc-round-label">-</div>
      <div id="wc-match" style="margin-top:14px;">
        <button class="wc-choice" id="wc-choice-a" onclick="wcPick(0)">-</button>
        <div class="wc-vs">VS</div>
        <button class="wc-choice" id="wc-choice-b" onclick="wcPick(1)">-</button>
      </div>
      <div id="wc-winner" style="display:none;">
        <!-- 우상단 즐겨찾기 하트: 우승 화면에서만 보이도록 이 컨테이너 안에 둔다
             (absolute 기준점은 바깥 .pick-modal-box) -->
        <button class="modal-fav-btn" id="wc-winner-fav" onclick="toggleModalFav(this)" aria-label="즐겨찾기 추가/해제">♡</button>
        <div class="pick-modal-name" id="wc-winner-name">-</div>
        <div class="pick-modal-sub" id="wc-winner-sub"></div>
        <div class="pick-modal-buttons">
          <a id="wc-winner-map" href="#" target="_blank" rel="noopener" class="pick-modal-map-btn">지도에서 보기</a>
          <button class="pick-modal-close-btn" onclick="shareWcWinner(this)">🏆 공유</button>
          <button class="pick-modal-close-btn" onclick="closeWorldcup()">닫기</button>
        </div>
      </div>
    </div>
  </div>
  <!-- 결제할 사람 뽑기 모달: 인원수 -> 실명 입력칸 동적 생성 -> 당첨 인원 설정 ->
       Math.random 추첨 -> 결과/공유. 서버 통신 없이 100% 브라우저 안에서 동작한다.
       (참고: 정적 페이지 특성상 <form> 없이 input + 버튼 onclick만으로 구동) -->
  <div id="pay-modal" class="pick-modal-overlay" onclick="if(event.target===this) closePayModal()">
    <div class="pick-modal-box">
      <div class="pick-modal-label">💵 오늘 결제할 사람 뽑기</div>
      <div id="pay-setup" style="margin-top:6px;">
        <div class="pay-field">
          <span>모임 총 인원</span>
          <input type="number" id="pay-count" min="2" max="12" value="4" inputmode="numeric" oninput="renderPayInputs()" onblur="this.value = Math.max(2, Math.min(12, parseInt(this.value, 10) || 4)); renderPayInputs();">
        </div>
        <div id="pay-names"></div>
        <div class="pay-field">
          <span>결제 당첨 인원</span>
          <input type="number" id="pay-winners" min="1" max="12" value="1" inputmode="numeric">
        </div>
        <button class="pay-draw-btn" onclick="drawPayers()">🎰 추첨하기</button>
        <div class="pick-modal-buttons">
          <button class="pick-modal-close-btn" onclick="closePayModal()">닫기</button>
        </div>
      </div>
      <div id="pay-result" style="display:none;">
        <div class="pick-modal-name" id="pay-result-names" style="font-size:20px;">-</div>
        <div class="pick-modal-buttons">
          <button class="pick-modal-close-btn" onclick="sharePayResult(this)">🎰 결과 카톡 공유</button>
          <button class="pick-modal-close-btn" onclick="resetPayModal()">다시 뽑기</button>
          <button class="pick-modal-close-btn" onclick="closePayModal()">닫기</button>
        </div>
      </div>
    </div>
  </div>
  <!-- 탭 전환 기능: 버튼 클릭 시 모든 탭/버튼의 active를 지우고,
       클릭된 것에만 다시 active를 붙여서 그 내용만 보이게 만든다 -->
  <script>
    // 데스크톱: 칩 바(지역 탭/카테고리/정렬) 위에서 마우스 세로 휠을 가로
    // 스크롤로 변환한다. 스크롤바 드래그 없이도 휠만 굴려서 화면 밖 지역
    // 탭에 접근할 수 있게 하는 장치 (모바일 터치 스와이프에는 영향 없음).
    document.querySelectorAll('.tabs, .cat-filter, .sort-bar').forEach(function(bar) {{
      bar.addEventListener('wheel', function(e) {{
        if (bar.scrollWidth <= bar.clientWidth) return;       // 안 넘치면 관여 안 함
        if (Math.abs(e.deltaX) >= Math.abs(e.deltaY)) return; // 트랙패드 가로 제스처는 기본 동작 유지
        bar.scrollLeft += e.deltaY;
        e.preventDefault();  // 페이지 세로 스크롤로 새는 것 방지
      }}, {{ passive: false }});
    }});

    // --- 지역 탭 정렬 토글: 랭킹순(서버가 심은 data-rank 순, 기본) <-> 가나다순.
    // 전체/지역랭킹 탭은 data-region-tab이 없어 재배열 대상에서 제외되고
    // 항상 맨 앞에 남는다. 선택은 localStorage에 저장되어 재방문 시 유지 ---
    function applyTabOrder(mode) {{
      // 메인 줄(비서울 지역)과 서울 서브탭 줄, 각 줄 안에서 따로 재배열한다.
      // 고정 요소(전체/지역랭킹/서울버튼, 서울 전체)는 data-region-tab이 없어
      // 대상에서 빠지고 항상 각 줄의 맨 앞에 남는다.
      document.querySelectorAll('.tabs').forEach(function(bar) {{
        var btns = Array.prototype.slice.call(bar.querySelectorAll('.tab-btn[data-region-tab]'));
        if (!btns.length) return;
        btns.sort(mode === 'abc'
          ? function(a, b) {{ return a.textContent.localeCompare(b.textContent, 'ko'); }}
          : function(a, b) {{ return Number(a.dataset.rank) - Number(b.dataset.rank); }});
        btns.forEach(function(b) {{ bar.appendChild(b); }});  // 고정 탭들 뒤로 순서대로 재부착
      }});
      var toggle = document.querySelector('.tab-order-btn');
      if (toggle) {{
        toggle.classList.toggle('on', mode === 'abc');  // 스위치 썸 이동 + 트랙 색
        toggle.setAttribute('aria-checked', mode === 'abc' ? 'true' : 'false');
        var lbl = toggle.querySelector('.tot-label');
        if (lbl) lbl.textContent = mode === 'abc' ? '🔤 가나다순' : '🔥 랭킹순';
      }}
    }}

    function toggleTabOrder() {{
      var cur = 'rank';
      try {{ cur = localStorage.getItem('naver_trend_tab_order') || 'rank'; }} catch (e) {{ }}
      var next = cur === 'rank' ? 'abc' : 'rank';
      try {{ localStorage.setItem('naver_trend_tab_order', next); }} catch (e) {{ }}
      applyTabOrder(next);
    }}

    // 페이지 로드 시 저장된 정렬 복원 (기본은 서버 렌더 순서 = 랭킹순이라
    // 가나다순으로 저장된 사용자만 재배열이 일어난다)
    try {{
      if (localStorage.getItem('naver_trend_tab_order') === 'abc') applyTabOrder('abc');
    }} catch (e) {{ }}

    // --- 서울 접이식 탭: 버튼을 누르면 서브탭 줄(서울 전체 + 상위 권역들)이
    // 펼쳐지고, 처음 펼칠 때(서울 패널이 활성 아닐 때) "서울 전체"로 이동한다.
    // 다시 누르면 접힌다 (보고 있던 패널은 그대로 유지) ---
    function toggleSeoulTabs(btn) {{
      var sub = document.getElementById('seoul-subtabs');
      if (!sub) return;
      var opening = !sub.classList.contains('open');
      sub.classList.toggle('open', opening);
      btn.classList.toggle('open', opening);
      btn.textContent = opening ? '🏙️ 서울 ▴' : '🏙️ 서울 ▾';
      if (opening) {{
        var activeInSeoul = sub.querySelector('.tab-btn.active');
        if (!activeInSeoul) {{
          var first = sub.querySelector('.tab-btn');  // = "서울 전체"
          if (first) first.click();
        }}
      }}
    }}

    document.querySelectorAll('.tab-btn').forEach(function(btn) {{
      btn.addEventListener('click', function() {{
        document.querySelectorAll('.tab-btn').forEach(function(b) {{ b.classList.remove('active'); }});
        document.querySelectorAll('.tab-panel').forEach(function(p) {{ p.classList.remove('active'); }});
        btn.classList.add('active');
        document.getElementById(btn.dataset.tab).classList.add('active');
        // 서울 소속 패널을 보고 있으면 메인 줄의 서울 버튼도 파란 활성으로 표시
        var seoulBtn = document.querySelector('.seoul-btn');
        if (seoulBtn) {{
          seoulBtn.classList.toggle('seoul-active', !!btn.closest('.seoul-subtabs'));
        }}
        // 리스트를 깊이 스크롤한 상태에서 탭을 바꾸면 새 탭의 중간 지점부터
        // 보이는 문제가 있었다 (탭바가 sticky라 화면 위쪽에 떠 있는 상태).
        // 탭 위치보다 아래로 내려가 있을 때만 탭바 바로 위로 스크롤을 되돌린다.
        // (탭이 원래 위치에 그대로 보이는 상태에서는 아무것도 하지 않음)
        var tabsEl = document.querySelector('.tabs-area');
        if (tabsEl && window.scrollY > tabsEl.offsetTop) {{
          window.scrollTo(0, tabsEl.offsetTop);
        }}
      }});
    }});

    // "오늘 메뉴 랜덤 추천" 버튼 클릭 시 실행되는 슬롯머신 애니메이션.
    // 버튼이 속한 탭(panel) 안의 식당 카드들 중, 카테고리 필터로 숨겨지지 않은
    // (지금 화면에 실제로 보이는) 카드만 대상으로 무작위 하나를 고른다.
    function runRandomPick(btn) {{
      var panel = btn.closest('.tab-panel');
      var cards = Array.prototype.slice.call(panel.querySelectorAll('.card:not(.cat-hidden):not(.lg-hidden)'));
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
      var modal = document.getElementById('pick-modal');
      var panel = card.closest('.tab-panel');
      // 공유 버튼(sharePick)이 쓸 정보를 모달에 저장해둔다
      modal.dataset.name = name;
      modal.dataset.map = card.href;
      modal.dataset.tab = (panel && panel.dataset.tabname) || '';
      document.getElementById('pick-modal-name').textContent = name;
      // 지역 · 카테고리 서브라인: 카드에 이미 심어둔 data 속성에서 읽으므로
      // 추가 연산/통신 없음 (월드컵 대진 버튼의 메타 표기와 같은 형식)
      var pickRegion = card.dataset.region || '';
      var pickCategory = card.dataset.category || '';
      var pickMeta = pickRegion
        ? pickRegion + (pickCategory ? ' · ' + pickCategory : '')
        : pickCategory;
      var pickSub = document.getElementById('pick-modal-sub');
      pickSub.textContent = pickMeta;
      pickSub.style.display = pickMeta ? '' : 'none';
      document.getElementById('pick-modal-map').href = card.href;
      // 우상단 하트: 뽑힌 카드의 즐겨찾기 키를 물려받아 현재 상태(♥/♡)를 반영
      var pickFav = document.getElementById('pick-modal-fav');
      var pickSrcBtn = card.querySelector('.fav-btn');
      pickFav.dataset.key = pickSrcBtn ? pickSrcBtn.dataset.key : '';
      syncModalFavHeart(pickFav);
      modal.classList.add('active');
    }}

    function closePickModal() {{
      document.getElementById('pick-modal').classList.remove('active');
      document.querySelectorAll('.card.picking').forEach(function(c) {{ c.classList.remove('picking'); }});
    }}

    // --- 순위 배지(①②③...) 재계산: 카드 안의 "N" 숫자는 처음 만들어질 때 값으로
    // 고정되어 있어서, 정렬이나 카테고리 필터로 화면 순서가 바뀌어도 숫자는 그대로였다.
    // 정렬/필터를 적용할 때마다 이 함수를 호출해서, 지금 실제로 보이는 순서 그대로
    // 1번부터 다시 매겨준다 (공유 버튼이 계산하는 순위와도 항상 일치하게 됨).
    function renumberVisibleRanks(panel) {{
      var visible = Array.prototype.slice.call(panel.querySelectorAll('.card-list .card:not(.cat-hidden):not(.lg-hidden)'));
      visible.forEach(function(card, idx) {{
        // .rank 전체의 textContent를 덮어쓰면 안에 있는 순위변동 화살표(▲▼)까지
        // 지워지므로, 숫자만 담고 있는 .rank-num 부분만 갱신한다
        var rankEl = card.querySelector('.rank-num') || card.querySelector('.rank');
        if (rankEl) rankEl.textContent = idx + 1;
      }});
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

      renumberVisibleRanks(panel);
      updateWorldcupButton(panel);  // 보이는 카드 수가 바뀌면 월드컵 버튼 노출 조건도 갱신
    }}

    // --- 정렬 미니탭: 급상승순/언급많은순/진짜후기순 - 서버 재호출 없이 이미 그려진
    // 카드들을 data-growth/data-thisweek/data-genuine 값 기준으로 다시 배열한다 ---
    function sortByMetric(btn) {{
      var bar = btn.closest('.sort-bar');
      var panel = btn.closest('.tab-panel');
      var metric = btn.dataset.sort; // 'rankmetric' | 'thisweek' | 'genuine' | 'rate'

      bar.querySelectorAll('.sort-btn').forEach(function(b) {{ b.classList.remove('active'); }});
      btn.classList.add('active');

      var list = Array.prototype.slice.call(panel.querySelectorAll('.card-list .card'));
      list.sort(function(a, b) {{
        var av = parseFloat(a.dataset[metric]) || 0;
        var bv = parseFloat(b.dataset[metric]) || 0;
        if (bv !== av) return bv - av; // 1차: 선택한 지표 내림차순 (큰 값이 위로)
        // 2차(동점자 처리): 백엔드(build_ranking)가 쓰는 것과 같은 기준인
        // "이번 주 언급량"으로 순서를 확정한다. 이게 없으면 정렬 버튼을 왔다 갔다
        // 할 때 동점 카드들의 순서가 그때그때 달라질 수 있다.
        var atw = parseFloat(a.dataset.thisweek) || 0;
        var btw = parseFloat(b.dataset.thisweek) || 0;
        return btw - atw;
      }});
      var container = panel.querySelector('.card-list');
      if (container) {{
        list.forEach(function(card) {{ container.appendChild(card); }});
      }}

      renumberVisibleRanks(panel);
    }}

    // --- 공유 버튼: "{{탭 이름}} {{정렬기준}}순 {{순위}}위 - {{이름}}\\n{{지도링크}}" 형태로 클립보드에 복사.
    // 순위/정렬기준은 정적으로 미리 박아두지 않고, 클릭하는 시점에 화면에 보이는 상태
    // (카테고리 필터로 숨겨졌는지, 어떤 정렬 버튼이 활성 상태인지)를 그대로 반영해서 계산한다.
    var SORT_LABELS = {{ rankmetric: '급상승순', thisweek: '언급많은순', genuine: '진짜후기순', rate: '급상승률순' }};

    function shareCard(btn) {{
      var card = btn.closest('.card');
      var panel = btn.closest('.tab-panel');
      var tabName = (panel && panel.dataset.tabname) || '';

      // 지금 활성화된 정렬 기준 (탭에 정렬바가 없으면 - 예: 즐겨찾기 탭 - 기본값 사용)
      var activeSortBtn = panel ? panel.querySelector('.sort-bar .sort-btn.active') : null;
      var sortLabel = activeSortBtn ? (SORT_LABELS[activeSortBtn.dataset.sort] || '급상승순') : '급상승순';

      // 지금 화면에 실제로 보이는(카테고리 필터로 숨겨지지 않은) 카드들 중 몇 번째인지 계산
      var visibleCards = panel
        ? Array.prototype.slice.call(panel.querySelectorAll('.card:not(.cat-hidden):not(.lg-hidden)'))
        : [card];
      var rank = visibleCards.indexOf(card) + 1;
      if (rank <= 0) rank = 1;

      var name = decodeURIComponent(btn.dataset.name);
      // 즐겨찾기 탭은 정렬바도 서버 순위 개념도 없는 "개인 목록"이라, 기본 조합을
      // 그대로 쓰면 "즐겨찾기 급상승순 1위 - ..."라는 어색한 문구가 나간다.
      // (이때의 순위 숫자는 단순 담은 순서일 뿐 의미가 없음) -> 전용 포맷으로 분기.
      var text = (tabName === '즐겨찾기')
        ? '💝 즐겨찾기 맛집 - ' + name + '\\n' + btn.dataset.map
        : tabName + ' ' + sortLabel + ' ' + rank + '위 - ' + name + '\\n' + btn.dataset.map;

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
      try {{
        localStorage.setItem('naver_trend_favorites', JSON.stringify(favs));
      }} catch (e) {{
        /* 사생활 보호 모드/일부 인앱 브라우저에서는 저장이 막혀 여기서 예외가 난다.
           저장은 못 해도(새로고침 시 초기화) 지금 세션의 하트 토글과 즐겨찾기 탭은
           그대로 동작하도록 삼킨다 - 다크모드 토글(toggleTheme)과 같은 방어 원칙 */
      }}

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

    // --- 모달 우상단 즐겨찾기 하트 (랜덤 추천 결과 / 월드컵 우승 공용) ---
    // 저장·카드 하트 동기화·즐겨찾기 탭 갱신은 기존 toggleFavorite를 그대로
    // 재사용한다 (이 버튼도 data-key를 갖고 있어 같은 함수가 동작함).
    // 모달 하트 자신은 .fav-btn 클래스가 아니어서 toggleFavorite의 일괄 갱신
    // 대상에 안 잡히므로, 여기서 직접 다시 그려준다.
    function syncModalFavHeart(el) {{
      if (!el) return;
      var key = el.dataset.key || '';
      var on = key && !!loadFavorites()[key];
      el.textContent = on ? '♥' : '♡';
      el.classList.toggle('active', !!on);
      el.style.display = key ? '' : 'none';  // 키를 못 찾은 예외 상황이면 숨김
    }}

    function toggleModalFav(el) {{
      if (!el.dataset.key) return;
      toggleFavorite(el);
      syncModalFavHeart(el);
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
          // 협찬성(lg) 카드를 협찬 포함 모드에서 즐겨찾기했다면, 유저가 직접
          // 고른 것이므로 토글 상태와 무관하게 즐겨찾기 탭에서는 항상 보여준다.
          // lg-card 클래스까지 벗겨야 토글 OFF 시 applySponsoredMode가 이 복제본을
          // 다시 숨기지 못한다 (경고 배지는 innerHTML에 있어 그대로 유지됨).
          clone.classList.remove('lg-hidden');
          clone.classList.remove('lg-card');
          cardsHtml += clone.outerHTML;
        }}
      }});

      if (cardsHtml) {{
        // 찜한 카드가 1개 이상일 때만 리스트 하단에 "통째로 공유" 버튼을 결합
        cardsHtml += '<button class="fav-share-btn" onclick="shareFavorites(this)">💝 즐겨찾기 목록 통째로 공유</button>';
      }}
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

    // --- 모바일에서 네이버 지도 "앱" 바로 열기 ---------------------------------
    // 카톡/인스타 인앱 브라우저에서 map.naver.com 웹 링크를 열면 로그인/앱설치
    // 유도 화면에 막히는 경우가 많다. 그래서 모바일 기기에서는 네이버 지도 앱을
    // 직접 실행하는 스키마(nmap://)를 먼저 시도하고, 일정 시간 안에 앱이 안 열리면
    // (앱 미설치 or 인앱 브라우저가 스키마 차단) 기존 웹 지도로 자동 폴백한다.
    // 데스크톱은 기존 동작(새 탭에서 웹 지도) 그대로 유지된다.
    function isMobileDevice() {{
      return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
    }}

    function openInNaverMapApp(card) {{
      var query = card.dataset.mapquery;
      var webUrl = card.href;
      if (!query) {{
        window.open(webUrl, '_blank');  // 검색어 정보가 없으면 그냥 웹 지도로
        return;
      }}
      // appname은 네이버 지도 앱 스키마 규격상 호출한 서비스 주소를 담는 파라미터 (iOS 필수)
      var scheme = 'nmap://search?query=' + query
        + '&appname=' + encodeURIComponent('{SITE_URL}');

      // 2.5초 안에 앱이 열리면 폴백을 취소하고, 아무 일도 안 일어나면
      // (앱 없음/스키마 차단) 웹 지도로 이동한다.
      // 주의: iOS 등에서 "지도에서 여시겠습니까?" 시스템 팝업이 뜬 동안에도
      // 타이머는 계속 흐르는데, 이 팝업은 페이지를 hidden으로 만들지 않는 경우가
      // 있어 visibilitychange만으로는 놓친다. 그래서 팝업/앱 전환 시 발생하는
      // blur(포커스 이탈)와 pagehide(페이지 이탈)까지 3중으로 감지해 취소하고,
      // 유저가 팝업을 보며 고민할 여유(2.5초)도 확보한다.
      var fallbackTimer = setTimeout(function() {{
        window.location.href = webUrl;
      }}, 2500);
      var cancelFallback = function(e) {{
        // visibilitychange는 실제로 화면이 숨겨졌을 때만 취소 (복귀 이벤트는 무시)
        if (e && e.type === 'visibilitychange' && !document.hidden) return;
        clearTimeout(fallbackTimer);
        document.removeEventListener('visibilitychange', cancelFallback);
        window.removeEventListener('blur', cancelFallback);
        window.removeEventListener('pagehide', cancelFallback);
      }};
      document.addEventListener('visibilitychange', cancelFallback);
      window.addEventListener('blur', cancelFallback);
      window.addEventListener('pagehide', cancelFallback);
      window.location.href = scheme;
    }}

    // 이벤트 위임: 즐겨찾기 탭의 복제 카드처럼 나중에 생기는 카드에서도 동작하도록
    // 개별 카드가 아니라 문서 전체에서 클릭을 받아 처리한다
    document.addEventListener('click', function(e) {{
      var card = e.target.closest ? e.target.closest('a.card') : null;
      if (!card || !card.href || card.href.indexOf('map.naver.com') === -1) return;
      if (!isMobileDevice()) return;  // 데스크톱은 기존 새 탭 동작 유지
      e.preventDefault();
      openInNaverMapApp(card);
    }});

    // ==================== 공유/복사 공통 유틸 ====================
    var SITE_URL = '{SITE_URL}';

    function copyTextWithFeedback(text, btn) {{
      function done() {{
        if (!btn) return;
        var original = btn.textContent;
        btn.textContent = '✅ 복사됨';
        setTimeout(function() {{ btn.textContent = original; }}, 1200);
      }}
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(text).then(done).catch(function() {{
          window.prompt('아래 내용을 복사하세요:', text);
        }});
      }} else {{
        window.prompt('아래 내용을 복사하세요:', text);
      }}
    }}

    // 모바일이면 네이티브 공유 시트(카톡 등 바로 선택), 아니면 클립보드 복사
    function shareOrCopy(text, btn) {{
      if (navigator.share) {{
        navigator.share({{ text: text }}).catch(function() {{ copyTextWithFeedback(text, btn); }});
      }} else {{
        copyTextWithFeedback(text, btn);
      }}
    }}

    function nowLabel() {{
      return new Date().toLocaleString('ko-KR', {{ month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }});
    }}

    // '전체'/'즐겨찾기'/'지역랭킹' 탭이면 지역명을 자연스럽게 생략하고,
    // 특정 지역 탭일 때만 "지역명 " 접두어를 붙인다 (공유 문구 분기 규칙)
    function tabRegionPrefix(tab) {{
      return (tab && tab !== '전체' && tab !== '즐겨찾기' && tab !== '지역랭킹') ? tab + ' ' : '';
    }}

    // ==================== 랜덤 픽 결과 공유 ====================
    function sharePick(btn) {{
      var m = document.getElementById('pick-modal');
      if (!m.dataset.name) return;
      var text = '🎰 오늘의 랜덤 픽!\\n'
        + '결정이 어렵다면 ' + nowLabel() + ' ' + tabRegionPrefix(m.dataset.tab)
        + '급상승 맛집으로 뽑은 곳은 바로 📢 [ ' + m.dataset.name + ' ] 입니다.\\n\\n'
        + '📍 지도 보기: ' + m.dataset.map + '\\n'
        + '⚡ 다른 동네 굴려보기: ' + SITE_URL;
      shareOrCopy(text, btn);
    }}

    // ==================== 즐겨찾기 목록 통째로 공유 ====================
    function shareFavorites(btn) {{
      var panel = document.getElementById('tab-favorites');
      var cards = panel.querySelectorAll('.card');
      if (cards.length === 0) return;
      var lines = ['💝 내 즐겨찾기 맛집 ' + cards.length + '곳'];
      cards.forEach(function(card, i) {{
        var name = card.querySelector('.name').childNodes[0].textContent.trim();
        lines.push((i + 1) + '위 ' + name + ' - ' + card.href);
      }});
      lines.push('');
      lines.push('⚡ 전체 랭킹 보기: ' + SITE_URL);
      shareOrCopy(lines.join('\\n'), btn);
    }}

    // ==================== 현재 순위 매장 항목 복사 ====================
    // 지금 화면 상태(카테고리 필터 + 정렬) 그대로, 보이는 상위 최대 5개의
    // "순수 이름"만 줄바꿈으로 이어 클립보드에 복사한다
    function copyVoteList(btn) {{
      var panel = btn.closest('.tab-panel');
      var visible = Array.prototype.slice.call(panel.querySelectorAll('.card-list .card:not(.cat-hidden):not(.lg-hidden)'));
      var names = visible.slice(0, 5).map(function(card) {{
        return card.querySelector('.name').childNodes[0].textContent.trim();
      }});
      if (names.length === 0) return;
      // 이름 목록 아래에 빈 줄을 두고 사이트 재유입 유도 꼬리표를 자동 결합한다
      // (카톡 투표에 붙여넣으면 목록 끝에 링크가 함께 공유되는 바이럴 구조)
      var text = names.join('\\n')
        + '\\n\\n더 많은 순위를 보고싶다면?\\n' + SITE_URL;
      copyTextWithFeedback(text, btn);
    }}

    // --- 즐겨찾기 열기: 예전의 탭 버튼을 대신하는 유틸리티 줄 버튼.
    // 지역 탭들의 active를 지우고 즐겨찾기 패널만 보여준다 (탭 줄에서 아무
    // 지역이나 누르면 자연스럽게 빠져나가진다) ---
    function openFavoritesTab() {{
      document.querySelectorAll('.tab-btn').forEach(function(b) {{ b.classList.remove('active'); }});
      document.querySelectorAll('.tab-panel').forEach(function(p) {{ p.classList.remove('active'); }});
      var panel = document.getElementById('tab-favorites');
      if (panel) panel.classList.add('active');
      // 서울 하위 탭을 보던 중 이동하면 메인 줄의 서울 버튼 파란 활성 표시가
      // 남는다 - 탭 버튼 핸들러에만 있던 해제 로직이 이 경로에 누락되어 있었음.
      // (접힘 상태(open)는 건드리지 않는다 - 탭 줄 자체가 계속 보이는 것과
      //  같은 원칙으로, 펼쳐둔 하위 탭 줄은 유저가 되돌아갈 통로로 유지)
      var seoulBtn = document.querySelector('.seoul-btn');
      if (seoulBtn) seoulBtn.classList.remove('seoul-active');
      var tabsEl = document.querySelector('.tabs-area');
      if (tabsEl && window.scrollY > tabsEl.offsetTop) {{
        window.scrollTo(0, tabsEl.offsetTop);
      }}
    }}

    // --- "협찬 포함" 토글: 내돈내산 지수 미달로 기본 숨김(lg-hidden)인 카드들을
    // 보이게/안 보이게 전환한다. 서버 재호출 없이 이미 구워진 카드의 클래스만
    // 바꾸는 방식. 보이는 카드 구성이 바뀌므로 각 패널을 현재 활성 정렬 기준으로
    // 재배열하고 순위 번호도 다시 매긴다. 상태는 다크모드처럼 localStorage에 저장 ---
    function applySponsoredMode(on) {{
      // 즐겨찾기 탭의 복제 카드는 대상에서 제외한다 (유저가 직접 고른 목록이라
      // 토글과 무관하게 항상 표시 - 복제 시 lg-card를 벗기지만 이중 방어)
      document.querySelectorAll('.tab-panel:not(#tab-favorites) .card.lg-card').forEach(function(c) {{
        c.classList.toggle('lg-hidden', !on);
      }});
      document.querySelectorAll('.lg-toggle-btn').forEach(function(b) {{
        b.classList.toggle('active', on);
      }});
      document.querySelectorAll('.tab-panel').forEach(function(panel) {{
        if (panel.id === 'tab-favorites') return;  // 즐겨찾기 탭은 개인 목록이라 제외
        var activeSort = panel.querySelector('.sort-btn.active');
        if (activeSort) {{
          sortByMetric(activeSort);  // 새로 보이는 카드까지 현재 기준으로 재정렬+번호 재계산
        }} else {{
          renumberVisibleRanks(panel);
        }}
        updateWorldcupButton(panel);  // 보이는 카드 수가 바뀌면 월드컵 버튼 조건도 갱신
      }});
    }}

    function toggleSponsored(btn) {{
      var on = !btn.classList.contains('active');
      try {{
        localStorage.setItem('naver_trend_show_sponsored', on ? '1' : '0');
      }} catch (e) {{ /* 프라이빗 모드 등: 저장만 실패, 이번 세션 토글은 동작 */ }}
      applySponsoredMode(on);
    }}

    // 페이지 로드 시 저장된 토글 상태 복원 (기본은 꺼짐 - 서버가 lg-hidden으로
    // 구워뒀으므로 켜져 있던 사용자만 여기서 한 번 벗겨준다)
    try {{
      if (localStorage.getItem('naver_trend_show_sponsored') === '1') {{
        applySponsoredMode(true);
      }}
    }} catch (e) {{ }}

    // ==================== 롤링 전광판 (순수 자동 순환) ====================
    // 슬라이드 문구는 백엔드가 미리 구워뒀고, 여기서는 7초마다 전환만 한다.
    // 전환 애니메이션: 현재 문구(active)는 위로 밀려 나가고(leaving),
    // 다음 문구는 아래 대기 위치에서 위로 올라온다(active).
    // 전광판 영역은 pointer-events:none이라 어떤 클릭/호버에도 반응하지 않는다.
    (function initTicker() {{
      var slides = document.querySelectorAll('.ticker-slide');
      if (slides.length < 2) return;  // 슬라이드가 1개뿐이면 순환 불필요
      var current = 0;
      window.advanceTicker = function() {{
        var cur = slides[current];
        var next = slides[(current + 1) % slides.length];
        cur.classList.remove('active');
        cur.classList.add('leaving');  // 현재 문구: 위로 퇴장
        // 다음 문구: 예전에 퇴장했던 슬라이드일 수 있으므로, 전환 효과 없이
        // 아래 대기 위치로 먼저 리셋한 뒤(강제 리플로우) 올라오는 애니메이션 시작
        next.classList.add('no-transition');
        next.classList.remove('leaving');
        void next.offsetHeight;  // 리셋 위치를 브라우저에 즉시 반영시키는 트릭
        next.classList.remove('no-transition');
        next.classList.add('active');
        current = (current + 1) % slides.length;
      }};
      setInterval(window.advanceTicker, 7000);
    }})();

    // ==================== 맛집 월드컵 토너먼트 ====================
    var wcState = null;

    // 보이는 카드 수에 따라 버튼 노출을 결정: 4개 미만이면 토너먼트 구성이
    // 불가능하므로 숨긴다 (숨으면 flex라 랜덤 버튼이 자동으로 100% 차지)
    function updateWorldcupButton(panel) {{
      var wcBtn = panel.querySelector('.wc-btn');
      if (!wcBtn) return;
      var visibleCount = panel.querySelectorAll('.card-list .card:not(.cat-hidden):not(.lg-hidden)').length;
      wcBtn.classList.toggle('hidden', visibleCount < 4);
    }}

    function startWorldcup(btn) {{
      var panel = btn.closest('.tab-panel');
      var visible = Array.prototype.slice.call(panel.querySelectorAll('.card-list .card:not(.cat-hidden):not(.lg-hidden)'));
      if (visible.length < 4) return;
      var size = visible.length >= 8 ? 8 : 4;  // 8개 이상=8강, 4~7개=4강
      var entrants = visible.slice(0, size).map(function(card) {{
        return {{
          name: card.querySelector('.name').childNodes[0].textContent.trim(),
          url: card.href,
          // 수직 스택형 선택 버튼의 상단 메타 정보(지역 · 카테고리)용.
          // 카드에 심어둔 data 속성에서 읽으므로 추가 연산/통신이 없다.
          region: card.dataset.region || '',
          category: card.dataset.category || '',
          // 우승 화면의 즐겨찾기 하트가 쓸 키 (카드 하트와 같은 저장소 공유)
          favkey: (function() {{
            var fb = card.querySelector('.fav-btn');
            return fb ? fb.dataset.key : '';
          }})()
        }};
      }});
      // 무작위 대진표 (Fisher-Yates 셔플)
      for (var i = entrants.length - 1; i > 0; i--) {{
        var j = Math.floor(Math.random() * (i + 1));
        var tmp = entrants[i]; entrants[i] = entrants[j]; entrants[j] = tmp;
      }}
      wcState = {{ round: entrants, next: [], idx: 0, tab: panel.dataset.tabname || '' }};
      document.getElementById('wc-winner').style.display = 'none';
      document.getElementById('wc-match').style.display = '';
      document.getElementById('wc-modal').classList.add('active');
      renderWcMatch();
    }}

    // 월드컵 선택 버튼 내부를 "메타(지역 · 카테고리) / 식당 이름" 수직 스택으로 채운다.
    // 이름은 textContent로 넣어 특수문자(&, < 등)가 있어도 안전하고,
    // CSS(ellipsis)가 긴 이름을 ...으로 잘라 좁은 화면에서도 안 깨진다.
    function fillWcChoice(el, entrant) {{
      el.innerHTML = '<span class="wc-choice-meta"></span><span class="wc-choice-name"></span>';
      var metaText = entrant.region
        ? entrant.region + (entrant.category ? ' · ' + entrant.category : '')
        : (entrant.category || '');
      el.querySelector('.wc-choice-meta').textContent = metaText || '\u00a0';
      el.querySelector('.wc-choice-name').textContent = entrant.name;
    }}

    function renderWcMatch() {{
      if (wcState.idx >= wcState.round.length) {{
        // 이번 라운드의 모든 매치 종료 -> 승자들로 다음 라운드 구성
        if (wcState.next.length === 1) {{ showWcWinner(wcState.next[0]); return; }}
        wcState.round = wcState.next;
        wcState.next = [];
        wcState.idx = 0;
      }}
      var roundName = wcState.round.length === 2 ? '결승' : wcState.round.length + '강';
      var matchNo = (wcState.idx / 2) + 1;
      var total = wcState.round.length / 2;
      document.getElementById('wc-round-label').textContent =
        '🏆 ' + roundName + ' ' + matchNo + '/' + total + ' - 어디로 갈까요?';
      fillWcChoice(document.getElementById('wc-choice-a'), wcState.round[wcState.idx]);
      fillWcChoice(document.getElementById('wc-choice-b'), wcState.round[wcState.idx + 1]);
    }}

    function wcPick(which) {{
      if (!wcState) return;
      wcState.next.push(wcState.round[wcState.idx + which]);
      wcState.idx += 2;
      renderWcMatch();
    }}

    function showWcWinner(winner) {{
      wcState.winner = winner;
      document.getElementById('wc-round-label').textContent = '🏆 이주의 핫플 월드컵 우승!';
      document.getElementById('wc-match').style.display = 'none';
      document.getElementById('wc-winner-name').textContent = winner.name;
      // 우승 화면에도 지역 · 카테고리 서브라인 표시 (대진 버튼과 같은 정보원)
      var wcMeta = winner.region
        ? winner.region + (winner.category ? ' · ' + winner.category : '')
        : (winner.category || '');
      var wcSub = document.getElementById('wc-winner-sub');
      wcSub.textContent = wcMeta;
      wcSub.style.display = wcMeta ? '' : 'none';
      document.getElementById('wc-winner-map').href = winner.url;
      // 우상단 하트: 우승 식당의 즐겨찾기 키를 물려받아 현재 상태(♥/♡)를 반영
      var wcFav = document.getElementById('wc-winner-fav');
      wcFav.dataset.key = winner.favkey || '';
      syncModalFavHeart(wcFav);
      document.getElementById('wc-winner').style.display = '';
    }}

    function shareWcWinner(btn) {{
      if (!wcState || !wcState.winner) return;
      var text = '🏆 이주의 핫플 월드컵 1위!\\n'
        + '내가 뽑은 ' + nowLabel() + ' ' + tabRegionPrefix(wcState.tab)
        + '1위 핫플은 👑 [ ' + wcState.winner.name + ' ] 입니다.\\n\\n'
        + '📍 지도 보기: ' + wcState.winner.url + '\\n'
        + '⚡ 다른 동네 굴려보기: ' + SITE_URL;
      shareOrCopy(text, btn);
    }}

    function closeWorldcup() {{
      document.getElementById('wc-modal').classList.remove('active');
      wcState = null;
    }}

    // ==================== 결제할 사람 뽑기 (실명 추첨 미니게임) ====================
    // 서버/저장소 없이 브라우저 안에서만 동작: 인원수(N) 입력 -> 실명 입력칸 N개
    // 동적 생성 -> 당첨 인원(M) 설정 -> Fisher-Yates 셔플로 중복 없이 M명 추첨.
    var payState = null;  // {{ winners: [...], all: [...] }} - 공유 문구 조립용

    function openPayModal() {{
      payState = null;
      document.getElementById('pay-result').style.display = 'none';
      document.getElementById('pay-setup').style.display = '';
      renderPayInputs();
      document.getElementById('pay-modal').classList.add('active');
    }}

    // 인원수(N) 입력값이 바뀔 때마다 실명 입력칸을 N개로 다시 맞춘다.
    // 이미 입력해둔 이름은 지우지 않고 그대로 보존한 채 개수만 늘리거나 줄인다.
    function renderPayInputs() {{
      var countInput = document.getElementById('pay-count');
      var n = parseInt(countInput.value, 10);
      if (isNaN(n)) return;  // 지우고 다시 입력하는 중이면 그대로 둔다
      n = Math.max(2, Math.min(12, n));
      var box = document.getElementById('pay-names');
      var existing = Array.prototype.slice.call(box.querySelectorAll('input'));
      // 부족한 만큼 추가
      for (var i = existing.length; i < n; i++) {{
        var input = document.createElement('input');
        input.type = 'text';
        input.placeholder = (i + 1) + '번째 멤버 이름';
        input.maxLength = 20;
        box.appendChild(input);
      }}
      // 넘치는 만큼 뒤에서부터 제거
      while (box.children.length > n) {{
        box.removeChild(box.lastChild);
      }}
      // 당첨 인원(M)의 상한도 N에 맞춰 보정 (M <= N 유지)
      var winnersInput = document.getElementById('pay-winners');
      winnersInput.max = n;
      if (parseInt(winnersInput.value, 10) > n) winnersInput.value = n;
    }}

    function drawPayers() {{
      var inputs = Array.prototype.slice.call(document.querySelectorAll('#pay-names input'));
      var names = inputs.map(function(input, i) {{
        var v = input.value.trim();
        return v || (i + 1) + '번 멤버';  // 빈 칸은 자동 이름으로 대체 (추첨 진행은 막지 않음)
      }});
      var n = names.length;
      if (n < 2) return;
      var winnersInput = document.getElementById('pay-winners');
      var m = parseInt(winnersInput.value, 10);
      if (isNaN(m) || m < 1) m = 1;
      if (m > n) m = n;  // M <= N 강제
      // 보정이 일어났으면(총원 초과 입력, 빈 값, 0 등) 화면의 입력창 숫자도 실제
      // 추첨에 쓰인 값으로 함께 동기화한다 - "인원 초과라 총원 기준으로 뽑혔구나"를
      // 유저가 눈으로 바로 인지할 수 있게. (m > n 분기만 고치면 빈 값/0 입력 케이스가
      // 여전히 어긋나므로, 클램핑 완료 후 무조건 한 번 반영하는 게 모든 분기를 커버)
      winnersInput.value = m;

      // Fisher-Yates 셔플 후 앞에서 M명 -> 중복 없는 공정한 추첨
      var pool = names.slice();
      for (var i = pool.length - 1; i > 0; i--) {{
        var j = Math.floor(Math.random() * (i + 1));
        var tmp = pool[i]; pool[i] = pool[j]; pool[j] = tmp;
      }}
      var winners = pool.slice(0, m);

      payState = {{ winners: winners, all: names }};
      document.getElementById('pay-result-names').textContent =
        '오늘 골든벨의 주인공은 💸 ' + winners.join(', ') + ' 입니다!';
      document.getElementById('pay-setup').style.display = 'none';
      document.getElementById('pay-result').style.display = '';
    }}

    function resetPayModal() {{
      // 입력했던 이름/인원은 그대로 둔 채 설정 화면으로 돌아가 다시 뽑을 수 있게
      document.getElementById('pay-result').style.display = 'none';
      document.getElementById('pay-setup').style.display = '';
    }}

    function sharePayResult(btn) {{
      if (!payState) return;
      var text = '💵 오늘 밥값 낼 사람은 누구?\\n'
        + nowLabel() + ' 급상승 맛집 모임에서 진행한 밥값 쏘기 내기 결과!\\n\\n'
        + '🎯 당첨자: [ ' + payState.winners.join(', ') + ' ] (축하합니다 👏)\\n\\n'
        + '👥 참여 멤버: ' + payState.all.join(', ') + '\\n\\n'
        + '⚡ 오늘 방문한 핫플 정보 보기: ' + SITE_URL;
      shareOrCopy(text, btn);
    }}

    function closePayModal() {{
      document.getElementById('pay-modal').classList.remove('active');
    }}

    // 페이지 로드 시 각 탭의 월드컵 버튼 노출 조건을 초기 계산
    (function initWorldcupButtons() {{
      document.querySelectorAll('.tab-panel').forEach(function(p) {{ updateWorldcupButton(p); }});
    }})();

    // --- 배너 색상 테마: 🎨 버튼 -> 색상 점 팔레트에서 선택. 선택은 localStorage에
    // 저장되어 재방문 시 유지 (다크모드와 같은 패턴). 서버는 관여하지 않고
    // .hero에 ht-v* 클래스만 붙였다 뗀다. 저장값이 목록에 없으면(옛 테마 삭제 등)
    // 기본 테마로 조용히 복귀 ---
    var HERO_THEMES = ['', 'ht-v2', 'ht-v3', 'ht-v4', 'ht-v5', 'ht-v6', 'ht-v7', 'ht-v8', 'ht-v9'];

    function applyHeroTheme(v) {{
      if (HERO_THEMES.indexOf(v) === -1) v = '';
      var hero = document.querySelector('.hero');
      if (!hero) return;
      HERO_THEMES.forEach(function(c) {{ if (c) hero.classList.remove(c); }});
      if (v) hero.classList.add(v);
      document.querySelectorAll('.ht-dot').forEach(function(d) {{
        d.classList.toggle('active', (d.getAttribute('data-ht') || '') === v);
      }});
    }}

    function toggleHeroThemePop() {{
      var pop = document.getElementById('hero-theme-pop');
      if (pop) pop.classList.toggle('open');
    }}

    (function initHeroTheme() {{
      var pop = document.getElementById('hero-theme-pop');
      if (pop) {{
        pop.addEventListener('click', function(e) {{
          var d = e.target;
          if (!d.classList || !d.classList.contains('ht-dot')) return;
          var v = d.getAttribute('data-ht') || '';
          try {{ localStorage.setItem('naver_trend_hero_theme', v); }} catch (e2) {{ /* 사생활 보호 모드 방어 */ }}
          applyHeroTheme(v);
        }});
      }}
      var saved = '';
      try {{ saved = localStorage.getItem('naver_trend_hero_theme') || ''; }} catch (e) {{ }}
      if (saved) applyHeroTheme(saved);
    }})();

    // --- 다크모드: 헤더의 🌙/☀️ 버튼으로 전환, 선택은 localStorage에 저장되어
    // 다음 방문 때도 유지된다. 저장된 선택이 없으면(첫 방문) 기기의 시스템 설정
    // (다크모드 사용 중인지)을 초기값으로 따라간다.
    function applyTheme(theme) {{
      var dark = theme === 'dark';
      document.body.classList.toggle('dark', dark);
      var btn = document.getElementById('theme-toggle');
      if (btn) btn.textContent = dark ? '☀️' : '🌙';  // 버튼엔 "누르면 바뀔 모드"의 반대 아이콘
    }}

    function toggleTheme() {{
      var next = document.body.classList.contains('dark') ? 'light' : 'dark';
      try {{
        localStorage.setItem('naver_trend_theme', next);
      }} catch (e) {{ /* 사생활 보호 모드 등에서 저장 실패해도 전환 자체는 되게 */ }}
      applyTheme(next);
    }}

    (function initTheme() {{
      var saved = null;
      try {{
        saved = localStorage.getItem('naver_trend_theme');
      }} catch (e) {{ saved = null; }}
      var theme = saved || (window.matchMedia
        && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
      applyTheme(theme);
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

      // --- 업데이트 지연 감지: 정상이라면 데이터 나이는 최대 2시간+빌드 15분
      // 안쪽이다. 3시간(180분)을 넘겼다는 건 예약 실행이 최소 한 회차 통째로
      // 빠졌다는 뜻(외부 스케줄러 장애/PAT 만료/API 전면 장애/품질 게이트 연속
      // 실패 등)이라, 배지를 경고 스타일로 바꿔 사이트를 여는 것만으로 파이프
      // 라인 이상을 알 수 있게 한다. 서버 호출 없이 시각 비교만으로 동작한다.
      var STALE_THRESHOLD_MIN = 180;
      var pillEl = relEl.parentElement;

      function render() {{
        var diffMin = Math.floor((Date.now() - generatedAt.getTime()) / 60000);
        var label;
        if (diffMin < 1) {{
          label = '방금 갱신됨';
        }} else if (diffMin < 60) {{
          label = diffMin + '분 전 갱신';
        }} else {{
          var diffHour = Math.floor(diffMin / 60);
          if (diffHour < 24) {{
            label = diffHour + '시간 전 갱신';
          }} else {{
            label = Math.floor(diffHour / 24) + '일 전 갱신';
          }}
        }}
        var isStale = diffMin >= STALE_THRESHOLD_MIN;
        if (isStale) {{
          label = '⚠️ ' + label + ' · 업데이트 지연';
        }}
        relEl.textContent = label;
        if (pillEl && pillEl.classList) {{
          pillEl.classList.toggle('stale', isStale);
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

    # 출력 경량화: 통짜 주석 줄/들여쓰기/빈 줄 제거 (소스 주석은 유지).
    # config의 MINIFY_OUTPUT = False로 끄면 디버깅용 원본 그대로 저장된다.
    if MINIFY_OUTPUT:
        before = len(html.encode("utf-8"))
        html = _slim_html(html)
        after = len(html.encode("utf-8"))
        print(f"[경량화] {before/1024:.0f}KB -> {after/1024:.0f}KB "
              f"({(1-after/before)*100:.0f}% 절감)")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n완료: {out_path} 생성됨 (탭 {len(tab_names)}개: {', '.join(tab_names)})")


if __name__ == "__main__":
    # 실제 실행 순서 (python fetch_and_build.py 했을 때 위에서 아래로 벌어지는 일):

    # 0) 품질 게이트 준비: 직전 실행의 식당 수를 미리 읽어둔다 (trend_history의
    #    prev_ranks가 직전 실행 결과 그 자체다). build_ranking()이 이 파일을
    #    덮어쓰기 전에 읽어야 하므로 반드시 맨 앞에서 캡처한다.
    _prev_result_count = 0
    try:
        with open(TREND_HISTORY_FILE, "r", encoding="utf-8") as _f:
            _prev_result_count = len(json.load(_f).get("prev_ranks", {}))
    except (OSError, json.JSONDecodeError):
        pass  # 첫 실행 등 - 게이트는 이전 데이터가 충분할 때만 동작

    # 1) 이번 실행에서 쓸 지역 목록을 확정한다.
    #    CANDIDATE_REGIONS를 설정 안 했으면 그냥 REGIONS를 그대로 쓰고,
    #    설정했으면 CORE_REGIONS + 화제성 상위 지역을 합쳐서 새로 만든다.
    REGIONS = resolve_active_regions()

    # 2) 확정된 지역들을 돌면서 식당 후보 수집 -> 언급 수 집계 -> 급상승 순위 계산
    all_results, total_filtered = build_ranking()

    # 2-0) 품질 게이트: 수집 결과가 직전 대비 급감했다면(API 부분 장애 등)
    #      반쪽짜리 페이지를 배포하는 대신 여기서 실패로 종료한다.
    #      -> 워크플로우의 이후 단계(기록 저장/배포)가 전부 중단되어
    #         라이브 사이트와 data 브랜치가 직전 정상 상태 그대로 유지된다.
    #      QUALITY_GUARD_MIN_RATIO = 0 으로 끌 수 있고, 이전 데이터가 20곳
    #      미만이면(초기 구축기) 게이트를 걸지 않는다.
    if (QUALITY_GUARD_MIN_RATIO > 0 and _prev_result_count >= 20
            and len(all_results) < _prev_result_count * QUALITY_GUARD_MIN_RATIO):
        print(f"\n❌ [품질 게이트] 수집 식당 {len(all_results)}곳 - 직전 실행 "
              f"{_prev_result_count}곳의 {QUALITY_GUARD_MIN_RATIO*100:.0f}% 미만으로 급감. "
              f"API 장애 가능성이 높아 배포를 중단합니다 (기존 페이지 유지).")
        sys.exit(1)

    # 2-1) (선택 기능, 기본 꺼짐) 상위 몇 개 식당만 데이터랩으로 실제 검색 관심도 추가 확인
    apply_search_trends(all_results, kst_today())

    # 3) 식당 랭킹과는 별개로, 지역 단위 랭킹도 따로 집계
    region_ranking = build_region_ranking(all_results)

    # 4) "전체" / "지역랭킹" / 지역별 탭 데이터를 하나의 구조로 정리
    tabs = build_tabs(all_results, region_ranking)

    # 5) 위 데이터를 실제 웹페이지(index.html)로 만들어서 저장
    render_html(tabs, total_filtered, all_results=all_results)

    # 원본 데이터도 별도로 저장 (검증/디버깅용 - 나중에 "왜 이 순위가 나왔지?" 확인할 때 유용)
    with open("top8_raw.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    # 검색로봇 접근 허용 안내 파일 생성 (네이버 서치어드바이저 robots.txt 경고 해소용)
    # Sitemap 줄을 함께 넣어두면 검색엔진이 사이트맵 위치를 바로 찾을 수 있다
    with open("robots.txt", "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL.rstrip('/')}/sitemap.xml\n")
    print("완료: robots.txt 생성됨")

    # sitemap.xml 생성 (SEO): 페이지가 index.html 하나뿐이라 항목도 하나지만,
    # lastmod(마지막 갱신 시각)를 매번 새로 적어주는 것이 핵심이다 - 검색엔진에게
    # "이 페이지는 계속 갱신되는 살아있는 페이지"라는 신호를 줘서 재수집을 유도한다.
    lastmod = kst_now().strftime("%Y-%m-%dT%H:%M:%S+09:00")
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url>\n    <loc>{SITE_URL}</loc>\n"
        f"    <lastmod>{lastmod}</lastmod>\n"
        "    <changefreq>hourly</changefreq>\n"
        "  </url>\n"
        "</urlset>\n"
    )
    with open("sitemap.xml", "w", encoding="utf-8") as f:
        f.write(sitemap)
    print("완료: sitemap.xml 생성됨")
