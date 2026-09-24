from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .base import AdapterOutput


@dataclass(slots=True)
class OutputValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


def validate_adapter_outputs(
    outputs: list[AdapterOutput],
    *,
    expected_kind: str,
    max_total_bytes: int = 20 * 1024 * 1024,
) -> OutputValidationReport:
    """Deterministic validate-before-accept gate generalized from FlowCraft/MiniMax."""
    report = OutputValidationReport()
    if not outputs:
        report.errors.append("adapter returned no outputs")
        return report

    total = 0
    matching = 0
    filenames: set[str] = set()

    for index, output in enumerate(outputs):
        if not output.kind:
            report.errors.append(f"output {index} has no kind")
        if not output.media_type or "/" not in output.media_type:
            report.errors.append(f"output {index} has invalid media_type")

        filename = Path(output.filename).name
        if (
            not filename
            or filename != output.filename
            or filename in {".", ".."}
            or "/" in output.filename
            or "\\" in output.filename
        ):
            report.errors.append(f"output {index} has unsafe filename")
        elif filename in filenames:
            report.errors.append(f"duplicate output filename: {filename}")
        else:
            filenames.add(filename)

        total += len(output.content)
        if output.kind == expected_kind:
            matching += 1

        if output.media_type == "application/json":
            try:
                json.loads(output.content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                report.errors.append(
                    f"output {index} declares application/json but is invalid JSON"
                )

        if len(output.content) == 0:
            report.errors.append(f"output {index} is empty")

    if total > max_total_bytes:
        report.errors.append("adapter outputs exceed total byte budget")
    if matching == 0:
        report.errors.append(f"missing required output kind: {expected_kind}")
    if matching > 1:
        report.warnings.append(
            f"adapter returned multiple outputs for required kind: {expected_kind}"
        )

    return report
