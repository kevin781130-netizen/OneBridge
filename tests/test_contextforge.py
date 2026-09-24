from onebridge.contextforge import ContextDocument, ContextForge, StaticContextProvider
from onebridge.contracts import TaskContract


def contract(scopes):
    return TaskContract.model_validate({
        "input": {"goal": "Build", "required_outputs": ["content"]},
        "context": {"tenant_id": "tenant", "user_id": "user"},
        "policy": {"knowledge_scopes": scopes},
    })


def test_contextforge_selects_only_requested_scopes_and_deduplicates():
    forge = ContextForge(budget_bytes=4096, max_items=4)
    provider = StaticContextProvider(
        "docs",
        {
            "brand": [
                ContextDocument(
                    scope="brand",
                    source_id="a",
                    content="Brand voice",
                    priority=0.9,
                ),
                ContextDocument(
                    scope="brand",
                    source_id="b",
                    content="Brand voice",
                    priority=0.5,
                ),
            ]
        },
    )
    forge.register("brand", provider)

    bundle = forge.assemble(contract(["brand"]))
    assert len(bundle.items) == 1
    assert bundle.items[0].source_id == "a"
    assert any(item["reason"] == "duplicate_content" for item in bundle.skipped)


def test_contextforge_reports_missing_scope_and_budget_skip():
    forge = ContextForge(budget_bytes=1024, max_items=4)
    forge.register(
        "docs",
        StaticContextProvider(
            "docs",
            {
                "docs": [
                    ContextDocument(
                        scope="docs",
                        source_id="large",
                        content="x" * 2048,
                        priority=1.0,
                    )
                ]
            },
        ),
    )
    bundle = forge.assemble(contract(["docs", "missing"]))
    assert bundle.missing_scopes == ("missing",)
    assert bundle.truncated is True
    assert bundle.items == ()
