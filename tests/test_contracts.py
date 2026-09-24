import pytest
from pydantic import ValidationError

from onebridge.contracts import TaskContract


def test_contract_defaults_ids():
    contract = TaskContract.model_validate({
        "input": {"goal": "ship a page", "required_outputs": ["content", "design"]},
        "context": {"tenant_id": "t1", "user_id": "u1"},
    })
    assert contract.task_id.startswith("task_")
    assert contract.project_id.startswith("prj_")
    assert contract.contract_version == "1.0"


def test_required_outputs_must_be_unique():
    with pytest.raises(ValidationError):
        TaskContract.model_validate({
            "input": {"goal": "x", "required_outputs": ["content", "content"]},
            "context": {"tenant_id": "t1", "user_id": "u1"},
        })
