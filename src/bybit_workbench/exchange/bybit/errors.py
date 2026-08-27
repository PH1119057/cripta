from enum import StrEnum


class BybitReadError(RuntimeError):
    pass


class BybitProtocolError(BybitReadError):
    pass


class BybitModeMismatch(BybitReadError):
    pass


class BybitClockSkewError(BybitModeMismatch):
    def __init__(self, offset_ms: int, max_offset_ms: int) -> None:
        self.offset_ms = offset_ms
        self.max_offset_ms = max_offset_ms
        super().__init__(
            f"Bybit clock differs by {offset_ms} ms; "
            f"maximum allowed offset is {max_offset_ms} ms"
        )


class BybitErrorCategory(StrEnum):
    AUTHENTICATION = "authentication"
    CLOCK_SKEW = "clock_skew"
    RATE_LIMIT = "rate_limit"
    SYMBOL_UNAVAILABLE = "symbol_unavailable"
    INSUFFICIENT_MARGIN = "insufficient_margin"
    TRANSIENT = "transient"
    REQUEST = "request"
    UNKNOWN = "unknown"


class BybitApiError(BybitProtocolError):
    def __init__(self, endpoint: str, ret_code: object, message: object) -> None:
        self.endpoint = endpoint
        self.ret_code = ret_code
        self.ret_message = str(message or "")
        self.category = classify_bybit_error(ret_code)
        self.retryable = self.category in {
            BybitErrorCategory.RATE_LIMIT,
            BybitErrorCategory.TRANSIENT,
        }
        super().__init__(
            f"Bybit {endpoint} failed: retCode={ret_code!r} retMsg={message!r} "
            f"category={self.category.value}"
        )


def classify_bybit_error(ret_code: object) -> BybitErrorCategory:
    try:
        code = int(str(ret_code))
    except (TypeError, ValueError):
        return BybitErrorCategory.UNKNOWN
    if code in {-1, 10002}:
        return BybitErrorCategory.CLOCK_SKEW
    if code in {-2015, 33004, 10003, 10004, 10005, 10007, 10010}:
        return BybitErrorCategory.AUTHENTICATION
    if code in {429, 10006, 170005}:
        return BybitErrorCategory.RATE_LIMIT
    if code in {10000, 10016, 170001, 170007, 170032, 170146, 170147}:
        return BybitErrorCategory.TRANSIENT
    if code in {10029, 110023, 110042, 170121, 170151, 170360, 30228}:
        return BybitErrorCategory.SYMBOL_UNAVAILABLE
    if code in {
        110004,
        110006,
        110007,
        110012,
        110044,
        110045,
        110051,
        110052,
        110053,
        170033,
        170131,
    }:
        return BybitErrorCategory.INSUFFICIENT_MARGIN
    if code in {10001, 110003, 110017, 110032}:
        return BybitErrorCategory.REQUEST
    return BybitErrorCategory.UNKNOWN
