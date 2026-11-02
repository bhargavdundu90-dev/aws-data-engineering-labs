"""Static checks on the ASL file: things that are easy to break in a JSON edit."""
import json
from pathlib import Path

import pytest

ASL = Path(__file__).resolve().parents[1] / "state_machine" / "loan_repayments_etl.asl.json"


@pytest.fixture(scope="module")
def definition():
    return json.loads(ASL.read_text())


def targets(state):
    out = [state.get("Next"), state.get("Default")]
    out += [c["Next"] for c in state.get("Choices", [])]
    out += [c["Next"] for c in state.get("Catch", [])]
    return [t for t in out if t]


def test_every_transition_points_to_a_real_state(definition):
    names = set(definition["States"])
    assert definition["StartAt"] in names
    for name, state in definition["States"].items():
        for t in targets(state):
            assert t in names, f"{name} -> {t} does not exist"


def test_every_state_is_reachable(definition):
    seen, todo = set(), [definition["StartAt"]]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo += targets(definition["States"][name])
    assert seen == set(definition["States"])


def test_non_terminal_states_have_a_next(definition):
    for name, state in definition["States"].items():
        if state["Type"] in ("Succeed", "Fail", "Choice"):
            continue
        assert state.get("Next") or state.get("End"), name


def test_work_tasks_catch_errors(definition):
    # every task except the alert publishers must route failures to NotifyFailure
    for name, state in definition["States"].items():
        if state["Type"] == "Task" and not name.startswith("Notify"):
            catch = state.get("Catch", [])
            assert any("States.ALL" in c["ErrorEquals"] and c["Next"] == "NotifyFailure" for c in catch), name


def test_glue_uses_sync_and_retries_on_concurrency(definition):
    glue = definition["States"]["RunGlueTransform"]
    assert glue["Resource"].endswith("glue:startJobRun.sync")
    retry = glue["Retry"][0]
    assert "Glue.ConcurrentRunsExceededException" in retry["ErrorEquals"]
    assert retry["BackoffRate"] > 1


def test_no_hardcoded_account_ids(definition):
    text = ASL.read_text()
    assert "arn:aws:lambda:" not in text and "arn:aws:sns:" not in text
