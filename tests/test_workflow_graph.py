import pytest

from onebridge.workflow_graph import WorkflowStep, default_pipeline, execution_waves


def test_default_pipeline_is_dependency_ordered():
    steps = default_pipeline(["content", "design", "code", "test_report"])
    assert [step.output_kind for step in steps] == ["content", "design", "code", "test_report"]
    assert [[step.index for step in wave] for wave in execution_waves(steps)] == [[0], [1], [2], [3]]


def test_parallel_wave_when_dependencies_allow():
    steps = [
        WorkflowStep(0, "a", "flowise", "content"),
        WorkflowStep(1, "b", "open_design", "design"),
        WorkflowStep(2, "c", "hermes", "code", (0, 1)),
    ]
    waves = execution_waves(steps)
    assert [[step.index for step in wave] for wave in waves] == [[0, 1], [2]]


def test_cycle_is_rejected():
    steps = [
        WorkflowStep(0, "a", "x", "content", (1,)),
        WorkflowStep(1, "b", "y", "design", (0,)),
    ]
    with pytest.raises(ValueError):
        execution_waves(steps)
