# GitHub 저장소용 config.py
# CLIENT_ID / CLIENT_SECRET은 일부러 비워둡니다 - GitHub Secrets에 등록한 값이
# 자동으로 여기 대신 사용되기 때문에, 이 파일에 진짜 키를 적을 필요가 없습니다.
# (그래서 이 파일은 공개 저장소에 올려도 안전합니다)
CLIENT_ID = ""
CLIENT_SECRET = ""

REGIONS = [
    "강남", "성수", "잠실" , "동탄", "회기" , "오산", "강릉", "춘천", 홍천"
]

DISPLAY_PER_REGION = 20
TOP_N = 8
TOP_N_PER_REGION = 5

OG_IMAGE_URL = "https://i.postimg.cc/gJhgW7Zz/seukeulinsyas-2026-07-11-131250.png"

EXTRA_BADGES = ["서원이가 참고할", "매주 월요일 9시 업데이트"]
