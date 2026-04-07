from dataclasses import dataclass


SHEET_HEADERS = [
    "원문",
    "KO 번역",
    "날짜",
    "메인 카테고리",
    "서브 카테고리",
    "긍정/부정/중립",
    "SNS",
    "URL",
    "비고",
]


@dataclass
class StructuredRecord:
    original_text: str
    ko_translation: str
    date: str
    main_category: str
    sub_category: str
    sentiment: str
    sns: str
    url: str

    def to_sheet_row(self) -> list[str]:
        return [
            self.original_text,
            self.ko_translation,
            self.date,
            self.main_category,
            self.sub_category,
            self.sentiment,
            self.sns,
            self.url,
            "",  # 비고: user editable
        ]
