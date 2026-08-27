from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://api.bybit.kz"
STATUS = Path("/var/lib/cripta/connectivity/private_api.json")


def main() -> None:
    credential_dir = Path(os.environ["CREDENTIALS_DIRECTORY"])
    credentials = json.loads((credential_dir / "bybit-mainnet").read_text(encoding="utf-8"))
    key = credentials["api_key"]
    secret = credentials["api_secret"]
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    query = ""
    signature = hmac.new(secret.encode(), f"{timestamp}{key}{recv_window}{query}".encode(), hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        BASE_URL + "/v5/user/query-api",
        headers={
            "X-BAPI-API-KEY": key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN": signature,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.load(response)
        result = payload.get("result") or {}
        safe = {
            "state": "authenticated" if payload.get("retCode") == 0 else "rejected",
            "ret_code": payload.get("retCode"),
            "ret_msg": payload.get("retMsg"),
            "read_only": result.get("readOnly"),
            "permissions": result.get("permissions", {}),
            "ip_restriction_count": len(result.get("ips") or []),
            "server_endpoint": BASE_URL,
            "checked_at_epoch": int(time.time()),
        }
    except urllib.error.HTTPError as exc:
        safe = {"state": "http-error", "http_status": exc.code, "message": str(exc.reason), "server_endpoint": BASE_URL, "checked_at_epoch": int(time.time())}
    except Exception as exc:
        safe = {"state": "error", "message": f"{type(exc).__name__}: {exc}", "server_endpoint": BASE_URL, "checked_at_epoch": int(time.time())}
    temporary = STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(safe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATUS)


if __name__ == "__main__":
    main()
