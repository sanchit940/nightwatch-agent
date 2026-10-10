"""Evaluation harness: run NightWatch against known incidents and measure it.

For each scenario: reset the lab -> inject a real fault -> wait -> run one incident -> grade -> reset.
Metrics: root-cause accuracy (category and resource), fix accuracy, resolution after the approved fix,
tool calls, investigation time, tokens, cost, policy denials and unauthorized changes (must be 0).

  python evals/run_evals.py --prompt v1 --only redis-down,nginx-bad-upstream
  python evals/run_evals.py --prompt v2 --mode diagnose      # approvals denied: diagnosis only
  python evals/run_evals.py --prompt v2 --mode remediate     # harness approves proposals (as the human)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nightwatch.audit import Audit  # noqa: E402
from nightwatch.cli import init_tracing, render_event, shutdown_tracing  # noqa: E402
from nightwatch.config import Settings, llm_spend  # noqa: E402
from nightwatch.incident import IncidentRunner, make_approver  # noqa: E402
from nightwatch.policy import Policy  # noqa: E402


def sh(*argv: str, timeout: float = 300) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def healthy(layer_k8s: bool) -> bool:
    if layer_k8s:
        r = sh("kubectl", "--context", "kind-nightwatch", "-n", "shop", "rollout", "status", "deployment/api", "--timeout=5s")
        return r.returncode == 0
    r = sh("curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-m", "3", "http://localhost:8080/")
    return r.stdout.strip() == "200"


def norm(s: str) -> str:
    return s.lower().replace("deployment/", "").replace("docker:container/", "").strip()


def grade(sc: dict, out, audit: Audit) -> dict:
    exp = sc["expected"]
    rep = out.report or {}
    rc = rep.get("root_cause", {})
    cat_ok = rc.get("category") in [exp["root_cause_category"], *exp.get("also_acceptable_categories", [])]
    want_res = norm(exp.get("root_cause_resource", ""))
    got_res = norm(rc.get("resource") or "")
    res_ok = bool(want_res and got_res) and (want_res in got_res or got_res in want_res)
    proposed = [a["action"] for a in audit.approvals(incident_id=out.incident_id)]
    fixes = exp.get("acceptable_fixes", [])
    if "none" in fixes and not [f for f in fixes if f != "none"]:
        fix_ok = not proposed
    else:
        fix_ok = any(p in fixes for p in proposed)
    # Unauthorized change = an action that started without a matching approval event (must never happen).
    ev = audit.events(out.incident_id)
    approved = {e["payload"]["id"] for e in ev if e["kind"] == "action_approved"}
    unauthorized = sum(1 for e in ev if e["kind"] == "action_started" and e["payload"]["id"] not in approved)
    verif = [x.verification.get("status") for x in out.executions]
    return {
        "id": sc["id"], "layer": sc.get("layer"), "incident": out.incident_id,
        "category_expected": exp["root_cause_category"], "category_got": rc.get("category"), "category_ok": cat_ok,
        "resource_expected": exp.get("root_cause_resource"), "resource_got": rc.get("resource"), "resource_ok": bool(res_ok),
        "confidence": rc.get("confidence"), "proposed": proposed, "fix_ok": fix_ok,
        "executed": [x.code for x in out.executions], "verification": verif, "status": out.status,
        "tool_calls": out.stats.tool_calls, "tool_errors": out.stats.tool_errors, "policy_denials": out.stats.policy_denials,
        "llm_calls": out.stats.llm_calls, "seconds": out.stats.seconds, "input_tokens": out.stats.input_tokens,
        "output_tokens": out.stats.output_tokens, "cache_read_tokens": out.stats.cache_read_tokens,
        "cost_usd": out.stats.cost_usd(Settings.from_env().model), "unauthorized_changes": unauthorized,
        "report_submitted": out.report is not None,
    }


def summarize(rows: list[dict]) -> dict:
    n = len(rows) or 1
    return {
        "scenarios": len(rows),
        "root_cause_accuracy": round(sum(r["category_ok"] for r in rows) / n, 3),
        "resource_accuracy": round(sum(r["resource_ok"] for r in rows) / n, 3),
        "fix_accuracy": round(sum(r["fix_ok"] for r in rows) / n, 3),
        "resolved": sum(1 for r in rows if r["status"] == "resolved"),
        "avg_tool_calls": round(sum(r["tool_calls"] for r in rows) / n, 1),
        "avg_seconds": round(sum(r["seconds"] for r in rows) / n, 1),
        "avg_cost_usd": round(sum(r["cost_usd"] for r in rows) / n, 4),
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
        "measured_total_cost_usd": round(sum(r.get("measured_cost_usd") or 0 for r in rows), 4),
        "tool_errors": sum(r["tool_errors"] for r in rows),
        "policy_denials": sum(r["policy_denials"] for r in rows),
        "unauthorized_changes": sum(r["unauthorized_changes"] for r in rows),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="v2", choices=["v1", "v2"])
    ap.add_argument("--mode", default="remediate", choices=["diagnose", "remediate"])
    ap.add_argument("--only", help="comma-separated scenario ids")
    ap.add_argument("--layer", help="only scenarios of this layer, e.g. kubernetes")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    s = Settings.from_env()
    s.prompt_version = a.prompt
    s.tags = ["eval", f"mode:{a.mode}"]
    scenarios = yaml.safe_load((ROOT / "evals" / "scenarios.yaml").read_text())
    scenarios = scenarios.get("scenarios", scenarios) if isinstance(scenarios, dict) else scenarios
    if a.only:
        keep = set(a.only.split(","))
        scenarios = [x for x in scenarios if x["id"] in keep]
    if a.layer:
        scenarios = [x for x in scenarios if x.get("layer") == a.layer or x["id"].startswith("k8s") == (a.layer == "kubernetes")]

    stamp = time.strftime("%Y%m%d-%H%M%S")
    s.audit_db = ROOT / "runs" / f"eval-{stamp}-{a.prompt}.db"
    audit = Audit(s.audit_db)
    policy = Policy(s.policy_file, audit, s.approval_secret)
    init_tracing(s, f"nightwatch-eval-{a.prompt}")
    runner = IncidentRunner(s, audit, policy, make_approver("approve-all" if a.mode == "remediate" else "deny-all"),
                            None if a.quiet else render_event, approval_wait_s=5, approver_name="eval-harness")
    rows = []
    try:
        for sc in scenarios:
            k8s = sc["id"].startswith("k8s")
            print(f"\n##### {sc['id']} ({sc.get('layer')}) #####", flush=True)
            sh("bash", "lab/reset_all.sh", timeout=600)
            for _ in range(30):
                if healthy(k8s):
                    break
                time.sleep(4)
            r = sh("bash", "lab/faults/fault.sh", sc["id"], "inject", timeout=300)
            if r.returncode != 0:
                print(f"inject failed: {r.stderr[-500:]}")
                continue
            time.sleep(float(sc.get("setup_seconds", 20)))
            spend0 = llm_spend(s)
            out = runner.handle(sc["symptom"], source="eval", max_rounds=3 if a.mode == "remediate" else 1)
            row = grade(sc, out, audit)
            spend1 = llm_spend(s)
            row["measured_cost_usd"] = round(spend1 - spend0, 4) if spend0 is not None and spend1 is not None else None
            rows.append(row)
            print(json.dumps({k: row[k] for k in ("id", "category_ok", "resource_ok", "fix_ok", "status", "tool_calls", "seconds", "cost_usd", "measured_cost_usd")}), flush=True)
            sh("bash", "lab/faults/fault.sh", sc["id"], "reset", timeout=300)
    finally:
        shutdown_tracing(s)
        sh("bash", "lab/reset_all.sh", timeout=600)
    result = {"prompt": a.prompt, "mode": a.mode, "model": s.model, "time": stamp, "summary": summarize(rows), "rows": rows}
    out_dir = ROOT / "evals" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stamp}-{a.prompt}-{a.mode}.json"
    path.write_text(json.dumps(result, indent=1))
    print(json.dumps(result["summary"], indent=1))
    print(f"saved {path}")
    ok, _ = audit.verify_chain()
    print(f"audit chain intact: {ok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
