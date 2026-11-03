import csv
import json
from datetime import date
from pathlib import Path

import boto3
import pytest
from botocore.stub import Stubber

from llm_dq import (JsonCache, LLMResponseError, build_prompt, converse, parse_response, review)

TODAY = date(2026, 11, 3)
SAMPLE = Path(__file__).resolve().parents[1] / "sample_data" / "kyc_applications.csv"


def load():
    with open(SAMPLE, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def answer(results):
    return json.dumps({"results": results})


class FakeBedrock:
    """Stands in for the bedrock-runtime client; answers from a scripted function."""

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def converse(self, **kwargs):
        prompt = kwargs["messages"][0]["content"][0]["text"]
        self.prompts.append(prompt)
        text = self.reply(prompt, len(self.prompts))
        return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}},
                "usage": {"inputTokens": len(prompt) // 4, "outputTokens": len(text) // 4, "totalTokens": 0},
                "stopReason": "end_turn"}


def ids_in(prompt):
    return [r["id"] for r in json.loads(prompt[prompt.index("Records:") + len("Records:"):])]


def flag(rules):
    """Reply with a fixed issue for some ids and a clean result for the rest."""
    def reply(prompt, _n):
        return answer([{"id": i, "issues": rules.get(i, [])} for i in ids_in(prompt)])
    return reply


# ---------- parsing ----------

def test_parse_accepts_fenced_json_and_filters_bad_items():
    text = "Here you go:\n```json\n" + answer([
        {"id": "A", "issues": [{"field": "occupation", "issue": "placeholder", "severity": "HIGH"},
                               {"field": "pan", "issue": "not ours to judge", "severity": "high"},
                               {"field": "address", "issue": "x", "severity": "critical"}]},
        {"id": "B", "issues": []},
        {"id": "ZZZ", "issues": [{"field": "occupation", "issue": "invented id", "severity": "low"}]},
    ]) + "\n```"
    out = parse_response(text, ["A", "B"])
    assert out == {"A": [{"field": "occupation", "issue": "placeholder", "severity": "high"}], "B": []}


@pytest.mark.parametrize("text", ["sorry, I can't help", "{\"results\": [", "{\"rows\": []}"])
def test_parse_rejects_malformed(text):
    with pytest.raises(LLMResponseError):
        parse_response(text, ["A"])


def test_parse_rejects_missing_ids():
    with pytest.raises(LLMResponseError, match="B"):
        parse_response(answer([{"id": "A", "issues": []}]), ["A", "B"])


def test_prompt_keeps_literal_braces_and_embeds_records():
    prompt = build_prompt([{"id": "A", "occupation": "Teacher"}])
    assert '{"results": [{"id": "<record id>"' in prompt
    assert ids_in(prompt) == ["A"]


# ---------- review flow ----------

def test_rule_rejects_are_not_sent_and_pii_never_leaves():
    client = FakeBedrock(flag({}))
    results, usage = review(load(), client, today=TODAY, batch_size=5)
    sent = [i for p in client.prompts for i in ids_in(p)]
    # minor, PII in free text, bad PAN are rejected by rules before the model
    for rid in ["APP-1003", "APP-1005", "APP-1006", "APP-1009"]:
        assert rid not in sent
    assert len(sent) == 6 and usage.calls == 2
    joined = "\n".join(client.prompts)
    for rec in load():
        assert rec["pan"] not in joined and rec["customer_name"] not in joined


def test_llm_issues_set_status():
    client = FakeBedrock(flag({
        "APP-1004": [{"field": "branch_remarks", "issue": "income from third party", "severity": "medium"}],
        "APP-1008": [{"field": "annual_income", "issue": "implausible for farmer", "severity": "high"}],
        "APP-1010": [{"field": "employer", "issue": "repeats occupation", "severity": "low"}],
    }))
    status = {r.application_id: r.status for r in review(load(), client, today=TODAY)[0]}
    assert status["APP-1001"] == "PASS"
    assert status["APP-1004"] == "REVIEW"
    assert status["APP-1008"] == "REJECT"
    assert status["APP-1010"] == "PASS"     # low severity is informational
    assert status["APP-1007"] == "REVIEW"   # medium rule issue (short mobile), model found nothing


def test_bad_answer_is_retried_once_then_marked_for_review():
    calls = {"n": 0}

    def reply(prompt, n):
        calls["n"] = n
        return "not json" if n == 1 else answer([{"id": i, "issues": []} for i in ids_in(prompt)])

    results, usage = review(load(), FakeBedrock(reply), today=TODAY, batch_size=10)
    assert usage.calls == 2 and all(r.llm_checked for r in results if r.status != "REJECT")

    results, usage = review(load(), FakeBedrock(lambda p, n: "still not json"), today=TODAY, batch_size=10)
    assert usage.calls == 2
    assert {r.status for r in results if r.application_id in ("APP-1001", "APP-1002")} == {"NEEDS_REVIEW"}


def test_cache_skips_the_model_on_rerun(tmp_path):
    cache_file = tmp_path / "cache.json"
    first = FakeBedrock(flag({}))
    review(load(), first, today=TODAY, cache=JsonCache(cache_file))
    assert first.prompts

    second = FakeBedrock(flag({}))
    _, usage = review(load(), second, today=TODAY, cache=JsonCache(cache_file))
    assert second.prompts == [] and usage.cache_hits == 6


def test_cost_estimate():
    _, usage = review(load(), FakeBedrock(flag({})), today=TODAY)
    assert usage.input_tokens > 0
    assert usage.cost_usd(0.00025, 0.00125) == pytest.approx(
        usage.input_tokens / 1000 * 0.00025 + usage.output_tokens / 1000 * 0.00125, abs=1e-6)


# ---------- the real boto3 client, stubbed ----------

def test_converse_request_shape_with_real_client():
    client = boto3.client("bedrock-runtime", region_name="us-east-1")
    with Stubber(client) as stub:
        stub.add_response(
            "converse",
            {"output": {"message": {"role": "assistant", "content": [{"text": answer([])}]}},
             "stopReason": "end_turn", "metrics": {"latencyMs": 120},
             "usage": {"inputTokens": 50, "outputTokens": 8, "totalTokens": 58}},
            {"modelId": "anthropic.claude-3-haiku-20240307-v1:0",
             "messages": [{"role": "user", "content": [{"text": "hi"}]}],
             "inferenceConfig": {"maxTokens": 1500, "temperature": 0.0}},
        )
        text, usage = converse(client, "anthropic.claude-3-haiku-20240307-v1:0", "hi")
    assert json.loads(text) == {"results": []}
    assert usage["inputTokens"] == 50
