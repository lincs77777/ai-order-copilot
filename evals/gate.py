"""CI 门禁：python -m evals.gate（读取 report.json 校验阈值，不达标 exit 1）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from evals.metrics import check_gate


def main() -> None:
    report_path = Path(__file__).parent / "report.json"
    if not report_path.exists():
        print("report.json 不存在，请先运行 python -m evals.runner")
        sys.exit(2)
    metrics = json.loads(report_path.read_text(encoding="utf-8"))["metrics"]
    ok, violations = check_gate(metrics)
    print(json.dumps(metrics, ensure_ascii=False, indent=1))
    if ok:
        print("GATE PASSED")
        sys.exit(0)
    print("GATE FAILED:")
    for v in violations:
        print("  -", v)
    sys.exit(1)


if __name__ == "__main__":
    main()
