#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .review_queue import ReviewQueue
except ImportError:
    from review_queue import ReviewQueue  # type: ignore


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Review-first Open Dictate glossary queue")
    p.add_argument("--queue", type=Path, default=None)
    p.add_argument("--glossary", type=Path, default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("candidates")
    add = sub.add_parser("add")
    add.add_argument("wrong")
    add.add_argument("candidate")
    add.add_argument("--source", default="manual")
    add.add_argument("--reason", default="")
    acc = sub.add_parser("accept")
    acc.add_argument("id")
    acc.add_argument("--right", default=None)
    rej = sub.add_parser("reject")
    rej.add_argument("id")
    rej.add_argument("--reason", default="")
    undo = sub.add_parser("undo")
    undo.add_argument("id")
    args = p.parse_args(argv)
    kwargs = {}
    if args.queue:
        kwargs["queue_path"] = args.queue
    if args.glossary:
        kwargs["glossary_path"] = args.glossary
    q = ReviewQueue(**kwargs)
    if args.cmd == "candidates":
        result = q.list()
    elif args.cmd == "add":
        result = q.add(args.wrong, args.candidate, source=args.source, reason=args.reason)
    elif args.cmd == "accept":
        result = q.accept(args.id, right=args.right)
    elif args.cmd == "reject":
        result = q.reject(args.id, reason=args.reason)
    elif args.cmd == "undo":
        result = q.undo(args.id)
    else:
        raise AssertionError(args.cmd)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
