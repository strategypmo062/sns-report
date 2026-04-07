from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from contracts import StructuredRecord

ALLOWED_MAIN = {"전반", "비용", "기능", "희망 기능", "미적용 아쉬움", "-"}
ALLOWED_SUB = {
    "Unsend",
    "Message Backup",
    "Album",
    "Font",
    "Sub Profile",
    "LINE family 서비스 혜택",
    "전반",
    "비용",
    "희망 기능",
    "미적용 아쉬움",
    "-",
}
FEATURE_SUB = {
    "Unsend",
    "Message Backup",
    "Album",
    "Font",
    "Sub Profile",
    "LINE family 서비스 혜택",
}
ALLOWED_SENTIMENT = {"긍정", "부정", "중립", "-"}
ALLOWED_SNS = {
    "DCard",
    "PTT",
    "Threads",
    "Instagram",
    "YouTube",
    "Facebook",
    "Mobile01",
    "Google Play Store Review (Sensor Tower)",
    "Apple App Store Review (Sensor Tower)",
}


@dataclass
class ValidationError:
    index: int
    reason: str


def validate_record(idx: int, r: StructuredRecord) -> list[ValidationError]:
    errors: list[ValidationError] = []

    if r.main_category not in ALLOWED_MAIN:
        errors.append(ValidationError(idx, f"invalid main_category: {r.main_category}"))
    if r.sub_category not in ALLOWED_SUB:
        errors.append(ValidationError(idx, f"invalid sub_category: {r.sub_category}"))
    if r.sentiment not in ALLOWED_SENTIMENT:
        errors.append(ValidationError(idx, f"invalid sentiment: {r.sentiment}"))
    if r.sns not in ALLOWED_SNS:
        errors.append(ValidationError(idx, f"invalid sns: {r.sns}"))

    try:
        datetime.strptime(r.date, "%Y-%m-%d")
    except ValueError:
        errors.append(ValidationError(idx, f"invalid date format: {r.date}"))

    # Non-functional rows must mirror main in sub unless '-'
    if r.main_category not in {"기능", "-"} and r.sub_category != r.main_category:
        errors.append(ValidationError(idx, "non-functional row must use sub_category == main_category"))
    if r.main_category == "기능" and r.sub_category not in FEATURE_SUB:
        errors.append(
            ValidationError(
                idx,
                "functional row must use detailed sub_category "
                "(Unsend|Message Backup|Album|Font|Sub Profile|LINE family 서비스 혜택)",
            )
        )

    return errors
