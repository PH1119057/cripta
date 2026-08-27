import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
assert manifest["reused_day_caches"] == int(sys.argv[2])
print("CACHE_REUSE_OK")
