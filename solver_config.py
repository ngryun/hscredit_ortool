"""Shared validation for CLI and web optimization settings."""

import math
from decimal import Decimal, InvalidOperation


def integer_setting(value, label: str, minimum: int = 0) -> int:
    """Accept whole numbers without silently truncating fractional input."""
    try:
        number = Decimal(str(value).strip())
        if isinstance(value, bool) or not number.is_finite() or number != number.to_integral_value():
            raise ValueError
        result = int(number)
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        raise ValueError(f"{label}: {minimum} 이상의 정수를 입력하세요.") from None
    if result < minimum:
        raise ValueError(f"{label}: {minimum} 이상의 정수를 입력하세요.")
    return result


def validate_solver_settings(slots, rooms, extra, cap, maxcap, extra_total=None):
    slots = integer_setting(slots, "시간대 수", 1)
    if slots > 26:
        raise ValueError("시간대 수는 26 이하여야 합니다.")
    rooms = integer_setting(rooms, "시간대별 교실 수")
    extra = integer_setting(extra, "추가 교실 수")
    cap = integer_setting(cap, "정원", 1)
    maxcap = integer_setting(maxcap, "최대 정원", 1)
    if maxcap < cap:
        raise ValueError("최대 정원은 정원 이상이어야 합니다.")
    if extra_total is not None:
        extra_total = integer_setting(extra_total, "전체 추가 교실 수")
    return slots, rooms, extra, cap, maxcap, extra_total


def validate_time_limit(value):
    if value is not None and (not math.isfinite(value) or value < 0):
        raise ValueError("시간 제한은 0 이상의 유한한 숫자여야 합니다. (0: 제한 없음)")
