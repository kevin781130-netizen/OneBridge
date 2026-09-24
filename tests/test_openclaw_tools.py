from onebridge.openclaw_tools import OPENCLAW_TOOLS, OpenClawToolDispatcher


class FakeShim:
    def __init__(self):
        self.calls = []

    def submit(self, contract):
        self.calls.append(("submit", contract))
        return {"task_id": contract.task_id, "status": "queued"}

    def status(self, task_id):
        self.calls.append(("status", task_id))
        return {"task_id": task_id, "status": "queued"}

    def artifacts(self, task_id):
        self.calls.append(("artifacts", task_id))
        return []

    def approve(self, task_id, request):
        self.calls.append(("approve", task_id, request))
        return {"task_id": task_id, "status": "succeeded"}

    def retry(self, task_id):
        self.calls.append(("retry", task_id))
        return {"task_id": task_id, "status": "queued"}

    def cancel(self, task_id):
        self.calls.append(("cancel", task_id))
        return {"task_id": task_id, "status": "cancelled"}


def test_openclaw_tool_definitions_are_unique():
    names = [item["name"] for item in OPENCLAW_TOOLS]
    assert len(names) == len(set(names))
    assert names == [
        "onebridge_submit",
        "onebridge_status",
        "onebridge_artifacts",
        "onebridge_approve",
        "onebridge_retry",
        "onebridge_cancel",
    ]


def test_openclaw_dispatcher_maps_submit_and_approve():
    shim = FakeShim()
    dispatcher = OpenClawToolDispatcher(shim)

    created = dispatcher.execute("onebridge_submit", {
        "goal": "Build landing page",
        "required_outputs": ["content", "design"],
        "tenant_id": "tenant",
        "user_id": "user",
    })
    assert created["status"] == "queued"
    contract = shim.calls[0][1]
    assert contract.context.channel == "openclaw"
    assert contract.input.required_outputs == ["content", "design"]

    approved = dispatcher.execute("onebridge_approve", {
        "task_id": "task_1",
        "artifact_ids": ["art_1"],
        "decision": "approve",
        "actor": "reviewer",
    })
    assert approved["status"] == "succeeded"
    assert shim.calls[-1][0] == "approve"
