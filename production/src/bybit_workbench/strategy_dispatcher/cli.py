from __future__ import annotations

import argparse
import json
from pathlib import Path

from .profile_io import load_profile_file
from .replay import replay_jsonl
from .service import PassiveDispatcherService


def main() -> int:
    parser = argparse.ArgumentParser(description="Пассивный Диспетчер стратегий")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-profile")
    validate.add_argument("profile", type=Path)

    once = sub.add_parser("once")
    _service_args(once)

    serve = sub.add_parser("serve")
    _service_args(serve)
    serve.add_argument("--poll-seconds", type=float, default=1.0)

    replay = sub.add_parser("replay")
    replay.add_argument("--input", required=True, type=Path)
    replay.add_argument("--profile", required=True, type=Path)
    replay.add_argument("--output", type=Path)

    args = parser.parse_args()
    if args.command == "validate-profile":
        profile = load_profile_file(args.profile, require_enabled=False)
        assert profile is not None
        print(f"OK {profile.profile_id}@{profile.version}")
        return 0
    if args.command in {"once", "serve"}:
        service = PassiveDispatcherService(
            mayak_status_path=args.mayak_status,
            profile_dir=args.profile_dir,
            state_root=args.state_root,
        )
        if args.command == "once":
            print(json.dumps(service.run_once(), ensure_ascii=False, indent=2))
            return 0
        service.serve_forever(poll_seconds=args.poll_seconds)
        return 0
    summary = replay_jsonl(
        input_path=args.input,
        profile_path=args.profile,
        output_path=args.output,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _service_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mayak-status",
        type=Path,
        default=Path("/var/lib/cripta/mayak_v2/status.json"),
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=Path("/srv/cripta/config/strategy_dispatcher/profiles"),
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path("/var/lib/cripta/strategy_dispatcher"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
