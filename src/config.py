from dataclasses import dataclass


@dataclass(frozen=True)
class SheetConfig:
    sheet_a_name: str = "A_AI_정리"
    sheet_b_name: str = "B_누적_raw"
    output_headers: tuple[str, ...] = (
        "원문",
        "KO 번역",
        "날짜",
        "메인 카테고리",
        "서브 카테고리",
        "긍정/부정/중립",
        "SNS",
        "URL",
        "비고",
    )


SNS_DOMAIN_MAP = {
    "ptt.cc": "PTT",
    "dcard.tw": "DCard",
    "threads.com": "Threads",
    "threads.net": "Threads",
    "instagram.com": "Instagram",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "facebook.com": "Facebook",
    "mobile01.com": "Mobile01",
    "play.google.com": "Google Play Store Review (Sensor Tower)",
    "apps.apple.com": "Apple App Store Review (Sensor Tower)",
}
