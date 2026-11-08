"""Static checks on the .tf files that do not need terraform or AWS (terraform validate runs separately)."""
import re
from pathlib import Path

import pytest

TF = Path(__file__).resolve().parents[1] / "terraform"
MODULES = sorted(p for p in (TF / "modules").iterdir() if p.is_dir())
VAR_BLOCK = re.compile(r'variable\s+"(\w+)"\s*\{(.*?)\n\}', re.S)


def variables(path: Path) -> dict[str, str]:
    return dict(VAR_BLOCK.findall(path.read_text()))


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_module_has_standard_files(module):
    assert {"main.tf", "variables.tf", "outputs.tf"} <= {p.name for p in module.glob("*.tf")}


@pytest.mark.parametrize("tf", sorted(TF.rglob("variables.tf")), ids=lambda p: str(p.relative_to(TF)))
def test_every_variable_has_description_and_type(tf):
    for name, body in variables(tf).items():
        assert "description" in body, f"{tf}: {name} has no description"
        assert re.search(r"\btype\s*=", body), f"{tf}: {name} has no type"


@pytest.mark.parametrize("tf", sorted(TF.rglob("outputs.tf")), ids=lambda p: str(p.relative_to(TF)))
def test_every_output_has_description(tf):
    for name, body in re.findall(r'output\s+"(\w+)"\s*\{(.*?)\n\}', tf.read_text(), re.S):
        assert "description" in body, f"{tf}: output {name} has no description"


def test_tfvars_set_every_required_stack_variable():
    stack_vars = variables(TF / "stack" / "variables.tf")
    required = {n for n, body in stack_vars.items() if "default" not in body}
    for env in ("dev", "prod"):
        text = (TF / "envs" / f"{env}.tfvars").read_text()
        set_vars = set(re.findall(r"^(\w+)\s*=", text, re.M))
        assert required <= set_vars, f"{env}.tfvars misses {required - set_vars}"
        assert set_vars <= set(stack_vars), f"{env}.tfvars sets unknown {set_vars - set(stack_vars)}"
        assert re.search(rf'^environment\s*=\s*"{env}"', text, re.M)


def test_backends_are_separate_per_env():
    dev = (TF / "envs" / "dev.backend.hcl").read_text()
    prod = (TF / "envs" / "prod.backend.hcl").read_text()
    bucket = re.compile(r'bucket\s*=\s*"([^"]+)"')
    assert bucket.search(dev).group(1) != bucket.search(prod).group(1)
    assert "encrypt        = true" in prod


def test_no_hardcoded_account_ids_or_keys():
    for tf in TF.rglob("*.tf"):
        text = tf.read_text()
        assert not re.search(r"\b\d{12}\b", text), f"account id in {tf}"
        assert "AKIA" not in text, f"access key in {tf}"
