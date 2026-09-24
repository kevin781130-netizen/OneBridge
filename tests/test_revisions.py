from onebridge.revisions import RevisionWorkspace


def test_revision_snapshot_and_rollback():
    workspace = RevisionWorkspace({"value": 1})
    first = workspace.revision_id
    updated = workspace.replace({"value": 2}, label="change")
    assert updated.digest != workspace.get(first).digest
    rolled = workspace.rollback(first)
    assert rolled.label == f"rollback:{first}"
    assert workspace.current == {"value": 1}
