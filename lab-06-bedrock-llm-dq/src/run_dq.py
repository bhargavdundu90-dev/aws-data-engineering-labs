"""Run rule + LLM data-quality checks over a KYC applications CSV.

    python src/run_dq.py --input sample_data/kyc_applications.csv --dry-run
    python src/run_dq.py --input sample_data/kyc_applications.csv --out output/dq_results.jsonl \\
        --region us-east-1 --model-id anthropic.claude-3-haiku-20240307-v1:0

--dry-run applies the rules and prints the masked prompt that would be sent, without calling
Bedrock, which is the quickest way to check exactly what leaves the account.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter
from dataclasses import asdict
from datetime import date
from pathlib import Path

from llm_dq import DEFAULT_MODEL, JsonCache, build_prompt, review
from rules import llm_view, rule_issues


def read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def main(argv=None, client=None) -> int:
    p = argparse.ArgumentParser(description="Rule + LLM data-quality checks on KYC applications")
    p.add_argument("--input", required=True)
    p.add_argument("--out", default="output/dq_results.jsonl")
    p.add_argument("--model-id", default=DEFAULT_MODEL)
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--batch-size", type=int, default=5)
    p.add_argument("--cache", default="output/llm_cache.json")
    p.add_argument("--price-in", type=float, default=0.00025, help="USD per 1K input tokens")
    p.add_argument("--price-out", type=float, default=0.00125, help="USD per 1K output tokens")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)

    records = read_csv(a.input)
    today = date.today()

    if a.dry_run:
        to_send = [llm_view(r) for r in records
                   if not any(i["severity"] == "high" for i in rule_issues(r, today))]
        print(f"{len(records)} records, {len(records) - len(to_send)} rejected by rules, "
              f"{len(to_send)} would go to the model\n")
        print(build_prompt(to_send[:a.batch_size]))
        return 0

    if client is None:
        import boto3
        client = boto3.client("bedrock-runtime", region_name=a.region)

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    cache_path = Path(a.cache)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    results, usage = review(records, client, a.model_id, a.batch_size, JsonCache(cache_path), today)
    elapsed = time.perf_counter() - t0

    with open(a.out, "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(asdict(r)) + "\n")

    counts = Counter(r.status for r in results)
    print(f"status: {dict(counts)}")
    print(f"bedrock calls: {usage.calls}, cache hits: {usage.cache_hits}, "
          f"tokens in/out: {usage.input_tokens}/{usage.output_tokens}, "
          f"est. cost: ${usage.cost_usd(a.price_in, a.price_out)}, took {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
