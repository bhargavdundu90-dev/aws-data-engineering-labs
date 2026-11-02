"""A small local interpreter for the subset of Amazon States Language this lab uses.

It lets the state machine's routing (Choice, Retry, Catch, ResultPath, Fail) be unit
tested with plain Python stubs in place of Lambda, Glue, Athena, S3 and SNS. It is not a
replacement for Step Functions Local or a real test run in AWS; it only covers:

  Task (Parameters, ResultSelector, ResultPath, Retry, Catch), Choice (BooleanEquals,
  StringEquals, NumericGreaterThan), Pass, Succeed, Fail, simple JSONPath ($.a.b) and the
  intrinsics States.Format and States.JsonToString.
"""
from __future__ import annotations

import copy
import json
import re


class TaskError(Exception):
    """Raised by a stub to simulate a service error, e.g. TaskError('Glue.ConcurrentRunsExceededException')."""

    def __init__(self, error: str, cause: str = ""):
        super().__init__(error)
        self.error, self.cause = error, cause


class ExecutionFailed(Exception):
    def __init__(self, error, cause, history):
        super().__init__(f"{error}: {cause}")
        self.error, self.cause, self.history = error, cause, history


def get_path(data, path: str):
    if path == "$":
        return data
    cur = data
    for part in path[2:].split("."):
        cur = cur[part]
    return cur


def set_path(data, path, value):
    if path is None:
        return data
    if path == "$":
        return value
    out = copy.deepcopy(data)
    cur = out
    parts = path[2:].split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value
    return out


def _split_args(s: str):
    args, buf, quoted, i = [], "", False, 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            buf += s[i + 1]
            i += 2
            continue
        if ch == "'":
            quoted = not quoted
            buf += ch
        elif ch == "," and not quoted:
            args.append(buf.strip())
            buf = ""
        else:
            buf += ch
        i += 1
    args.append(buf.strip())
    return args


def _arg_value(arg, data):
    if arg.startswith("'"):
        return arg[1:-1]
    return get_path(data, arg)


def evaluate(expr: str, data):
    m = re.fullmatch(r"(States\.\w+)\((.*)\)", expr, flags=re.S)
    if not m:
        return get_path(data, expr)
    fn, args = m.group(1), [_arg_value(a, data) for a in _split_args(m.group(2))]
    if fn == "States.Format":
        parts = args[0].split("{}")
        if len(parts) - 1 != len(args) - 1:
            raise ValueError(f"States.Format expects {len(parts) - 1} arguments")
        out = parts[0]
        for value, rest in zip(args[1:], parts[1:]):
            out += str(value) + rest
        return out
    if fn == "States.JsonToString":
        return json.dumps(args[0], sort_keys=True)
    raise NotImplementedError(fn)


def resolve(template, data):
    if isinstance(template, dict):
        out = {}
        for k, v in template.items():
            if k.endswith(".$"):
                out[k[:-2]] = evaluate(v, data)
            else:
                out[k] = resolve(v, data)
        return out
    if isinstance(template, list):
        return [resolve(v, data) for v in template]
    return template


def _matches(error, error_equals):
    if "States.ALL" in error_equals:
        return True
    if error in error_equals:
        return True
    # a service error reported by a .sync task is also a States.TaskFailed
    return "States.TaskFailed" in error_equals and not error.startswith("States.")


def _choice(state, data):
    for rule in state["Choices"]:
        value = get_path(data, rule["Variable"])
        if "BooleanEquals" in rule and value is rule["BooleanEquals"]:
            return rule["Next"]
        if "StringEquals" in rule and value == rule["StringEquals"]:
            return rule["Next"]
        if "NumericGreaterThan" in rule and value > rule["NumericGreaterThan"]:
            return rule["Next"]
    if "Default" not in state:
        raise ExecutionFailed("States.NoChoiceMatched", state.get("Comment", ""), [])
    return state["Default"]


def run(definition: dict, data: dict, resources: dict, max_steps: int = 100):
    """Execute and return (output, history). `resources` maps a Resource ARN to a stub
    callable(params) -> result. history is a list of (state_name, attempt) tuples."""
    history = []
    name = definition["StartAt"]
    for _ in range(max_steps):
        state = definition["States"][name]
        kind = state["Type"]
        if kind == "Succeed":
            history.append((name, 1))
            return data, history
        if kind == "Fail":
            history.append((name, 1))
            raise ExecutionFailed(state.get("Error", "States.Failed"), state.get("Cause", ""), history)
        if kind == "Choice":
            history.append((name, 1))
            name = _choice(state, data)
            continue
        if kind == "Pass":
            history.append((name, 1))
            data = set_path(data, state.get("ResultPath", "$"), resolve(state.get("Result", data), data))
        elif kind == "Task":
            params = resolve(state.get("Parameters", data), data)
            attempts, attempt, caught_next = {}, 0, None
            while True:
                attempt += 1
                history.append((name, attempt))
                try:
                    result = resources[state["Resource"]](params)
                    break
                except TaskError as exc:
                    retrier = next((r for r in state.get("Retry", []) if _matches(exc.error, r["ErrorEquals"])), None)
                    if retrier is not None and attempts.get(id(retrier), 0) < retrier.get("MaxAttempts", 3):
                        attempts[id(retrier)] = attempts.get(id(retrier), 0) + 1
                        continue
                    catcher = next((c for c in state.get("Catch", []) if _matches(exc.error, c["ErrorEquals"])), None)
                    if catcher is None:
                        raise ExecutionFailed(exc.error, exc.cause, history)
                    data = set_path(data, catcher.get("ResultPath", "$"), {"Error": exc.error, "Cause": exc.cause})
                    caught_next = catcher["Next"]
                    break
            if caught_next:
                name = caught_next
                continue
            if "ResultSelector" in state:
                result = resolve(state["ResultSelector"], result)
            data = set_path(data, state.get("ResultPath", "$"), result)
        else:
            raise NotImplementedError(kind)
        if state.get("End"):
            return data, history
        name = state["Next"]
    raise ExecutionFailed("States.Runtime", "max_steps exceeded", history)
