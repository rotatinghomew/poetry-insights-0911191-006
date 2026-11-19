"""生成一套确定性的合成演示数据（仅供演示与测试，非真实数据）。

用法：python3 examples/make_synthetic.py --out examples/synthetic --seed 7

覆盖的情形：框外未覆盖层、低响应层、陪同者代答（区分代答他人与本人补答）、
部分作答（只答入口）、未到场/中途被阻断、分批回收、多环节措施与基本保障。
"""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

STAGES = ["入口", "中段", "后半程"]

# (school, slot, stratum, 接触数, 入样概率, 回应率)
CELLS = [
    ("莲池小学", "周六早", "交通支持", 40, 0.40, 0.70),
    ("莲池小学", "周六早", "常规", 60, 0.20, 0.65),
    ("松风小学", "周日午", "交通支持", 30, 0.50, 0.70),
    ("松风小学", "周日午", "常规", 50, 0.25, 0.60),
    ("临湖小学", "周日早", "交通支持", 16, 0.50, 0.30),  # 低响应层
    ("榆树小学", "周六午", "未覆盖区", 20, 0.0, 0.0),  # 框外
]

ISSUE_PROBS = {
    "交通支持": {"入口": [("坡道", 0.35), ("扶手", 0.10)],
                 "中段": [("接驳", 0.20)],
                 "后半程": [("休息座椅", 0.12)]},
    "常规": {"入口": [("坡道", 0.05)],
             "中段": [("字幕", 0.15)],
             "后半程": [("电梯", 0.08), ("休息座椅", 0.10)]},
}

VISITS = {
    ("莲池小学", "周六早", "交通支持"): 210,
    ("莲池小学", "周六早", "常规"): 540,
    ("松风小学", "周日午", "交通支持"): 180,
    ("松风小学", "周日午", "常规"): 460,
    ("临湖小学", "周日早", "交通支持"): 95,
    ("榆树小学", "周六午", "未覆盖区"): 120,
}

MEASURES = [
    # measure_id, site, issues, cost, applies_to, basic_guarantee
    ("M1", "西入口", "坡道;扶手", 60000, "交通支持", "false"),
    ("M2", "全馆", "字幕", 45000, "全部", "false"),
    ("M3", "东廊", "接驳", 50000, "交通支持", "false"),
    ("M4", "全馆", "电梯", 30000, "全部", "false"),
    ("M5", "后半程休息区", "休息座椅", 25000, "全部", "false"),
    ("M6", "服务台", "无障碍咨询", 20000, "全部", "true"),
]


def draw_issues(rng: random.Random, stratum: str, stage: str) -> list[str]:
    return [
        issue
        for issue, prob in ISSUE_PROBS.get(stratum, {}).get(stage, [])
        if rng.random() < prob
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="examples/synthetic")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    contacts: list[dict] = []
    responses: list[dict] = []
    contact_seq = 0
    response_seq = 0

    for school, slot, stratum, n_contacts, prob, response_rate in CELLS:
        for _ in range(n_contacts):
            contact_seq += 1
            cid = f"S{contact_seq:04d}"
            invited = prob > 0 and rng.random() < 0.9
            contacts.append({
                "contact_id": cid,
                "school": school,
                "slot": slot,
                "stratum": stratum,
                "selection_probability": f"{prob:.2f}",
                "invited": "true" if invited else "false",
            })
            if not invited or rng.random() >= response_rate:
                continue
            batch = "1" if rng.random() < 0.7 else "2"
            # 未到场（妨碍参加）：陪同者代答另一人
            if rng.random() < 0.06:
                response_seq += 1
                responses.append({
                    "response_id": f"R{response_seq:04d}",
                    "contact_id": cid,
                    "batch": batch,
                    "respondent_role": "陪同者",
                    "subject_id": f"{cid}-P",
                    "stage": "入口",
                    "reached": "否",
                    "access_issue": rng.choice(["坡道", "接驳"]),
                    "attended": "否",
                })
                continue
            # 本人作答：可能部分作答（只答入口）、可能中途被阻断
            partial = rng.random() < 0.25
            blocked_mid = (not partial) and rng.random() < 0.08
            for stage in STAGES:
                if partial and stage != "入口":
                    continue
                if blocked_mid and stage == "后半程":
                    continue
                issues = draw_issues(rng, stratum, stage)
                reached = "是"
                if blocked_mid and stage == "中段":
                    reached = "否"
                    issues = issues or [rng.choice(["接驳", "坡道"])]
                response_seq += 1
                responses.append({
                    "response_id": f"R{response_seq:04d}",
                    "contact_id": cid,
                    "batch": batch,
                    "respondent_role": "本人",
                    "subject_id": cid,
                    "stage": stage,
                    "reached": reached,
                    "access_issue": ";".join(issues) if issues else "无",
                    "attended": "是",
                })
            # 陪同者补答本人后半程（同一被评价人）
            if partial and rng.random() < 0.4:
                issues = draw_issues(rng, stratum, "后半程")
                response_seq += 1
                responses.append({
                    "response_id": f"R{response_seq:04d}",
                    "contact_id": cid,
                    "batch": "2",
                    "respondent_role": "陪同者",
                    "subject_id": cid,
                    "stage": "后半程",
                    "reached": "是",
                    "access_issue": ";".join(issues) if issues else "无",
                    "attended": "是",
                })
            # 陪同者代答另一同行者（独立被评价人）
            if rng.random() < 0.15:
                issues = draw_issues(rng, stratum, "入口")
                response_seq += 1
                responses.append({
                    "response_id": f"R{response_seq:04d}",
                    "contact_id": cid,
                    "batch": batch,
                    "respondent_role": "陪同者",
                    "subject_id": f"{cid}-C",
                    "stage": "入口",
                    "reached": "是",
                    "access_issue": ";".join(issues) if issues else "无",
                    "attended": "是",
                })

    invited_by_cell: dict[tuple[str, str, str], int] = {}
    for c in contacts:
        if c["invited"] == "true":
            key = (c["school"], c["slot"], c["stratum"])
            invited_by_cell[key] = invited_by_cell.get(key, 0) + 1

    with (out / "sampled_contacts.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(contacts[0]))
        writer.writeheader()
        writer.writerows(contacts)
    with (out / "responses.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(responses[0]))
        writer.writeheader()
        writer.writerows(responses)
    with (out / "venue_totals.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["school", "slot", "stratum", "visit_count", "invited_count"])
        for school, slot, stratum, *_ in CELLS:
            key = (school, slot, stratum)
            writer.writerow([school, slot, stratum, VISITS[key], invited_by_cell.get(key, 0)])
    with (out / "improvements.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["measure_id", "site", "issue", "cost_minor", "applies_to", "basic_guarantee"])
        writer.writerows(MEASURES)
    print(f"合成数据已写入 {out}（{len(contacts)} 接触，{len(responses)} 答卷行）")


if __name__ == "__main__":
    main()
