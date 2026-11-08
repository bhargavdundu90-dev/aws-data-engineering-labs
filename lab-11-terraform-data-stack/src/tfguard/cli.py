"""Check a plan before apply.

    terraform plan -var-file=../envs/prod.tfvars -out tfplan
    terraform show -json tfplan > plan.json
    python -m tfguard.cli plan.json --env prod [--allow-destroy ADDRESS ...] [--json report.json]

Exit code 1 when any error-level finding is present, so CI stops before apply.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .plan import load_plan
from .rules import run_all, summarize_actions


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Guardrail checks on a terraform plan JSON")
    p.add_argument("plan")
    p.add_argument("--env", required=True, choices=["dev", "prod"])
    p.add_argument("--allow-destroy", nargs="*", default=[], help="resource addresses allowed to be destroyed/replaced")
    p.add_argument("--json", dest="json_out", help="write findings to this file")
    a = p.parse_args(argv)

    plan = load_plan(a.plan)
    findings = run_all(plan, a.env, set(a.allow_destroy))
    counts = summarize_actions(plan)
    errors = [f for f in findings if f.severity == "error"]

    print(f"plan: {counts['create']} to create, {counts['update']} to update, "
          f"{counts['replace']} to replace, {counts['delete']} to destroy")
    for f in findings:
        print(f)
    print(f"{len(errors)} error(s), {len(findings) - len(errors)} warning(s) -> {'BLOCKED' if errors else 'OK to apply'}")

    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as fh:
            json.dump({"env": a.env, "actions": counts, "blocked": bool(errors),
                       "findings": [asdict(f) for f in findings]}, fh, indent=2)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
