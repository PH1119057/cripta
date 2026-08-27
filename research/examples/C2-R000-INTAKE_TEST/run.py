from __future__ import annotations

import json


def main() -> None:
    print(json.dumps({"status": "ok", "purpose": "intake-test"}))


if __name__ == "__main__":
    main()
