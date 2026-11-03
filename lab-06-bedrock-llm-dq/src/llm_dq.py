"""Semantic data-quality checks with an LLM on Amazon Bedrock (Converse API).

Flow per batch of records:
  rules (free)  ->  rows already rejected by a high-severity rule are not sent
  mask PII      ->  cache lookup by content hash  ->  one Converse call per batch
  parse + validate the JSON answer  ->  retry once  ->  otherwise mark NEEDS_REVIEW

The model's answer is treated like any other untrusted input: it must be valid JSON in
the agreed shape, every id we sent must come back, ids we did not send are dropped, and
only known fields and severities are accepted.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from rules import FREE_TEXT, llm_view, rule_issues

PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / "kyc_semantic_checks.txt"
SEVERITIES = {"low", "medium", "high"}
DEFAULT_MODEL = "anthropic.claude-3-haiku-20240307-v1:0"


class LLMResponseError(ValueError):
    """The model answered, but not in the shape we asked for."""


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hits: int = 0

    def cost_usd(self, in_per_1k: float, out_per_1k: float) -> float:
        return round(self.input_tokens / 1000 * in_per_1k + self.output_tokens / 1000 * out_per_1k, 6)


@dataclass
class RecordResult:
    application_id: str
    status: str  # PASS | REVIEW | REJECT | NEEDS_REVIEW
    rule_issues: list = field(default_factory=list)
    llm_issues: list = field(default_factory=list)
    llm_checked: bool = False


def build_prompt(views: list[dict], template: str | None = None) -> str:
    template = template or PROMPT_FILE.read_text(encoding="utf-8")
    # str.replace, not str.format: the template contains literal JSON braces
    return template.replace("{records}", json.dumps(views, ensure_ascii=False, indent=1))


def _extract_json(text: str) -> dict:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise LLMResponseError("no JSON object in response")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise LLMResponseError(f"invalid JSON: {exc}") from exc


def parse_response(text: str, expected_ids: list[str]) -> dict[str, list[dict]]:
    """Validate the model's JSON and return {id: [issues]} for exactly the ids we sent."""
    payload = _extract_json(text)
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise LLMResponseError("'results' list missing")

    allowed_fields = set(FREE_TEXT)
    out: dict[str, list[dict]] = {}
    for item in results:
        rid = str(item.get("id", "")) if isinstance(item, dict) else ""
        if rid not in expected_ids:
            continue  # the model invented or mangled an id; never trust it
        clean = []
        for iss in item.get("issues") or []:
            if not isinstance(iss, dict):
                continue
            sev = str(iss.get("severity", "")).lower()
            fld = str(iss.get("field", ""))
            if sev in SEVERITIES and fld in allowed_fields and iss.get("issue"):
                clean.append({"field": fld, "issue": str(iss["issue"])[:200], "severity": sev})
        out[rid] = clean

    missing = [i for i in expected_ids if i not in out]
    if missing:
        raise LLMResponseError(f"ids missing from response: {missing}")
    return out


def converse(client, model_id: str, prompt: str, max_tokens: int = 1500) -> tuple[str, dict]:
    resp = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0.0},
    )
    text = "".join(part.get("text", "") for part in resp["output"]["message"]["content"])
    return text, resp.get("usage", {})


class JsonCache:
    """Content-hash cache so re-running a file does not pay for the same records twice."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self.data = json.loads(path.read_text()) if path and path.exists() else {}

    @staticmethod
    def key(view: dict, model_id: str, prompt_version: str) -> str:
        body = {k: v for k, v in view.items() if k != "id"}
        raw = json.dumps([model_id, prompt_version, body], sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key):
        return self.data.get(key)

    def put(self, key, issues):
        self.data[key] = issues

    def save(self):
        if self.path:
            self.path.write_text(json.dumps(self.data, indent=1))


def _status(rule_iss: list, llm_iss: list, llm_failed: bool) -> str:
    sev = {i["severity"] for i in rule_iss + llm_iss}
    if "high" in sev:
        return "REJECT"
    if llm_failed:
        return "NEEDS_REVIEW"
    if "medium" in sev:
        return "REVIEW"
    return "PASS"


def review(records: list[dict], client, model_id: str = DEFAULT_MODEL, batch_size: int = 5,
           cache: JsonCache | None = None, today: date | None = None,
           max_attempts: int = 2) -> tuple[list[RecordResult], Usage]:
    today = today or date.today()
    cache = cache or JsonCache()
    template = PROMPT_FILE.read_text(encoding="utf-8")
    prompt_version = hashlib.sha256(template.encode()).hexdigest()[:12]
    usage = Usage()

    results = {r["application_id"]: RecordResult(r["application_id"], "PASS", rule_issues(r, today))
               for r in records}

    # only rows not already rejected by rules go to the model
    pending = []
    for r in records:
        res = results[r["application_id"]]
        if any(i["severity"] == "high" for i in res.rule_issues):
            continue
        view = llm_view(r)
        key = JsonCache.key(view, model_id, prompt_version)
        hit = cache.get(key)
        if hit is not None:
            usage.cache_hits += 1
            res.llm_issues, res.llm_checked = hit, True
        else:
            pending.append((view, key))

    failed: set[str] = set()
    for i in range(0, len(pending), batch_size):
        batch = pending[i:i + batch_size]
        ids = [v["id"] for v, _ in batch]
        prompt = build_prompt([v for v, _ in batch], template)
        parsed = None
        for _ in range(max_attempts):
            text, u = converse(client, model_id, prompt)
            usage.calls += 1
            usage.input_tokens += u.get("inputTokens", 0)
            usage.output_tokens += u.get("outputTokens", 0)
            try:
                parsed = parse_response(text, ids)
                break
            except LLMResponseError:
                continue
        if parsed is None:
            failed.update(ids)
            continue
        for view, key in batch:
            res = results[view["id"]]
            res.llm_issues, res.llm_checked = parsed[view["id"]], True
            cache.put(key, parsed[view["id"]])

    for rid, res in results.items():
        res.status = _status(res.rule_issues, res.llm_issues, rid in failed)
    cache.save()
    return list(results.values()), usage
