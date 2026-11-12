#!/usr/bin/env python3
"""WatchBefore CLI compatibility wrapper.

The product-facing name is WatchBefore. The stable internal pipeline still
lives in scripts/cli.py while the rename is rolled out safely.
"""

from __future__ import annotations

try:
    from scripts.cli import main
    from scripts.video_pipeline import PipelineError
except ImportError:
    from cli import main  # type: ignore
    from video_pipeline import PipelineError  # type: ignore


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PipelineError as exc:
        print(f"pipeline_failed stage={exc.stage} reason={exc.reason_code} message={exc.message}")
        raise SystemExit(2)
    except Exception as exc:
        print(f"pipeline_failed stage=pipeline reason=unexpected message={exc}")
        raise SystemExit(2)
