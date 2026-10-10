"""Recovery demo: an action fails, and the agent takes the next step.

  python evals/recovery_demo.py --case stale   # approval goes stale: the target changed after the proposal
  python evals/recovery_demo.py --case oom     # an approved mitigation does not hold; the agent must escalate

Uses the real lab, the real policy service and the real worker. The harness plays the human approver.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nightwatch.audit import Audit  # noqa: E402
from nightwatch.cli import console, init_tracing, render_event, shutdown_tracing  # noqa: E402
from nightwatch.config import Settings, llm_spend  # noqa: E402
from nightwatch.incident import IncidentRunner  # noqa: E402
from nightwatch.policy import Policy  # noqa: E402


def sh(*argv: str) -> None:
    subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=600)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", choices=["stale", "oom"], required=True)
    ap.add_argument("--prompt", default="v5")
    a = ap.parse_args()
    s = Settings.from_env()
    s.prompt_version = a.prompt
    s.tags = ["recovery", f"case:{a.case}"]
    s.audit_db = ROOT / "runs" / f"recovery-{a.case}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    audit = Audit(s.audit_db)
    policy = Policy(s.policy_file, audit, s.approval_secret)

    if a.case == "stale":
        fault, symptom = "redis-down", "Users get 503 errors on http://localhost:8080."

        def approver(rec: dict) -> bool:
            # A colleague fixes the problem by hand while the approval request waits.
            console.print("[bold yellow]>>> Simulated colleague runs 'docker start nw-redis' by hand, then the approver clicks approve.[/]")
            sh("docker", "start", "nw-redis")
            time.sleep(3)
            return True
    else:
        fault, symptom = "api-oom", "http://localhost:8080 is flaky: some requests fail with 502 or 504."

        def approver(rec: dict) -> bool:
            return True

    sh("bash", "lab/reset_all.sh")
    sh("bash", "lab/faults/fault.sh", fault, "inject")
    time.sleep(25)
    init_tracing(s, f"nightwatch-recovery-{a.case}")
    runner = IncidentRunner(s, audit, policy, approver, render_event, approval_wait_s=5, approver_name="human:demo")
    spend0 = llm_spend(s)
    try:
        out = runner.handle(symptom, source="demo", max_rounds=3)
    finally:
        shutdown_tracing(s)
        sh("bash", "lab/faults/fault.sh", fault, "reset")
    spend1 = llm_spend(s)
    console.rule("Recovery summary")
    for x in out.executions:
        console.print(f"approval {x.approval_id}: executed={x.executed} ok={x.ok} code={x.code} verification={x.verification.get('status')}")
    console.print(f"incident {out.incident_id} final status={out.status} rounds={out.rounds} "
                  f"cost=${(spend1 - spend0) if spend0 is not None and spend1 is not None else float('nan'):.4f}")
    console.print(f"audit: {s.audit_db}  chain intact: {audit.verify_chain()[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
