from __future__ import annotations

import json
import time
import urllib.error
from typing import Any

from bybit_workbench.research.mtf_entry_v3 import _http_request

_RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}


def read_json_with_retry(
    url: str,
    *,
    label: str,
    timeout: float = 30.0,
    attempts: int = 6,
    base_delay_seconds: float = 2.0,
    max_delay_seconds: float = 30.0,
) -> dict[str, Any]:
    if attempts <= 0:
        raise ValueError("attempts must be positive")

    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            with _http_request(url, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"{label} response must be a JSON object")
            return payload
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in _RETRYABLE_HTTP or attempt >= attempts:
                break
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc
            if attempt >= attempts:
                break

        delay = min(max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1)))
        print(
            f"[network retry] {label}: attempt={attempt}/{attempts} "
            f"error={type(last_error).__name__}: {last_error}; retry_in={delay:.0f}s",
            flush=True,
        )
        time.sleep(delay)

    raise RuntimeError(
        f"{label} failed after {attempts} attempts: "
        f"{type(last_error).__name__ if last_error else 'unknown'}: {last_error}"
    ) from last_error
