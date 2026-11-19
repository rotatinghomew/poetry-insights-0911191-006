"""命令行入口：

    python -m access_analysis run --data-dir data --budget 80000 --out-dir out
    python -m access_analysis compare --round1 out_round1 --round2 data_round2 \
        --budget1 80000 --budget2 80000 --out comparison.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import Params, load_data, run_analysis
from .provenance import compare_rounds, record_from_result
from .report import render_comparison, render_report


def _add_common_params(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ess-threshold", type=float, default=30.0, help="有效样本量阈值")
    parser.add_argument(
        "--missing-multiplier",
        type=float,
        default=None,
        help="缺测者受影响率上限 = 观测率 × 该倍率；缺省为完整 Manski 界",
    )
    parser.add_argument(
        "--protected-group",
        action="append",
        default=[],
        help="底线人群层（可多次给出），用于平均收益牺牲诊断",
    )
    parser.add_argument(
        "--min-benefit",
        action="append",
        default=[],
        help="形如 人群层=0.3 的硬约束：该层受益份额不得低于该值",
    )
    parser.add_argument("--bootstrap", type=int, default=400, help="自助法重复次数")
    parser.add_argument("--seed", type=int, default=20261118, help="随机种子")
    parser.add_argument(
        "--visits-per-person",
        type=float,
        default=None,
        help="人均到访次数假设；给出后才允许人次→人数换算",
    )


def _parse_min_benefit(items: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--min-benefit 格式应为 人群层=数值，收到：{item!r}")
        name, _, value = item.partition("=")
        out[name.strip()] = float(value)
    return out


def _params_from(args: argparse.Namespace, budget: int) -> Params:
    return Params(
        budget=budget,
        ess_threshold=args.ess_threshold,
        missing_multiplier=args.missing_multiplier,
        protected_groups=tuple(args.protected_group),
        min_group_benefit=_parse_min_benefit(args.min_benefit),
        bootstrap=args.bootstrap,
        seed=args.seed,
        visits_per_person=args.visits_per_person,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="access_analysis", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="执行一轮分析并输出报告与决策留痕")
    run_p.add_argument("--data-dir", required=True, help="数据目录（四个 CSV）")
    run_p.add_argument("--budget", type=int, required=True, help="预算（与 cost_minor 同单位）")
    run_p.add_argument("--out-dir", required=True, help="输出目录")
    _add_common_params(run_p)

    cmp_p = sub.add_parser("compare", help="比较两轮数据，归因建议变化来源")
    cmp_p.add_argument("--round1", required=True, help="第一轮数据目录")
    cmp_p.add_argument("--round2", required=True, help="第二轮数据目录")
    cmp_p.add_argument("--record1", default=None, help="第一轮决策留痕 JSON（可选）")
    cmp_p.add_argument("--budget1", type=int, required=True)
    cmp_p.add_argument("--budget2", type=int, required=True)
    cmp_p.add_argument("--out", required=True, help="比较报告输出路径")
    _add_common_params(cmp_p)

    args = parser.parse_args(argv)

    if args.command == "run":
        params = _params_from(args, args.budget)
        contacts, rows, totals, measures = load_data(args.data_dir)
        result = run_analysis(contacts, rows, totals, measures, params)
        record = record_from_result(result, params, args.data_dir)
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "report.md").write_text(
            render_report(result, params, record), encoding="utf-8"
        )
        record.save(out_dir / "decision_record.json")
        print(f"报告与留痕已写入 {out_dir}")
        return 0

    if args.command == "compare":
        params1 = _params_from(args, args.budget1)
        params2 = _params_from(args, args.budget2)
        report = compare_rounds(
            args.round1,
            args.round2,
            params1,
            params2,
            record1_path=args.record1,
        )
        Path(args.out).write_text(render_comparison(report), encoding="utf-8")
        print(f"比较报告已写入 {args.out}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
