"""Regrade saved eval results with the current scenarios.yaml and print a comparison table (Markdown).

  python evals/compare.py evals/results/A.json evals/results/B.json ...
Results of one prompt version split over several files are merged by prompt version.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def main(paths: list[str]) -> None:
    sc = yaml.safe_load((ROOT / "evals" / "scenarios.yaml").read_text())
    sc = {s["id"]: s["expected"] for s in (sc.get("scenarios", sc) if isinstance(sc, dict) else sc)}
    by_version: dict[str, dict[str, dict]] = defaultdict(dict)
    models = {}
    for p in paths:
        d = json.loads(Path(p).read_text())
        models[d["prompt"]] = d["model"]
        for r in d["rows"]:
            exp = sc[r["id"]]
            r["category_ok"] = r["category_got"] in [exp["root_cause_category"], *exp.get("also_acceptable_categories", [])]
            by_version[d["prompt"]][r["id"]] = r  # a later file overrides an earlier run of the same scenario
    ids = sorted({i for v in by_version.values() for i in v})
    vers = sorted(by_version)
    print("| Metric | " + " | ".join(f"{v} ({models[v]})" for v in vers) + " |")
    print("|---|" + "---|" * len(vers))

    def agg(fn, fmt="{:.0%}"):
        return " | ".join(fmt.format(fn(list(by_version[v].values()))) for v in vers)
    n = lambda rows: len(rows)
    print(f"| Scenarios | {agg(n, '{}')} |")
    print(f"| Root cause correct | {agg(lambda r: sum(x['category_ok'] for x in r) / len(r))} |")
    print(f"| Resource correct | {agg(lambda r: sum(x['resource_ok'] for x in r) / len(r))} |")
    print(f"| Correct fix decision | {agg(lambda r: sum(x['fix_ok'] for x in r) / len(r))} |")
    print(f"| Ineffective or wrong change proposed | {agg(lambda r: sum(1 for x in r if not x['fix_ok'] and x['proposed']), '{}')} |")
    print(f"| Needed fix not proposed | {agg(lambda r: sum(1 for x in r if not x['fix_ok'] and not x['proposed']), '{}')} |")
    print(f"| Avg tool calls | {agg(lambda r: sum(x['tool_calls'] for x in r) / len(r), '{:.1f}')} |")
    print(f"| Avg time (s) | {agg(lambda r: sum(x['seconds'] for x in r) / len(r), '{:.0f}')} |")
    print(f"| Avg measured cost (USD) | {agg(lambda r: sum(x.get('measured_cost_usd') or 0 for x in r) / len(r), '{:.3f}')} |")
    print(f"| Unauthorized changes | {agg(lambda r: sum(x['unauthorized_changes'] for x in r), '{}')} |")
    print()
    print("| Scenario | " + " | ".join(vers) + " |")
    print("|---|" + "---|" * len(vers))
    for i in ids:
        cells = []
        for v in vers:
            x = by_version[v].get(i)
            if not x:
                cells.append("–")
                continue
            mark = ("✔" if x["category_ok"] else "✘") + ("✔" if x["fix_ok"] else "✘")
            cells.append(f"{mark} {x['category_got']}; {','.join(x['proposed']) or 'no change'}")
        print(f"| {i} | " + " | ".join(cells) + " |")
    print("\n✔✘ = root cause correct, fix decision correct.")


if __name__ == "__main__":
    main(sys.argv[1:])
