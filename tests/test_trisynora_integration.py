"""Verify evidence migration is additive and cannot mint release approval."""
from pathlib import Path
import json

from onebridge.cli import main
from onebridge.evidence_contracts.validator import ContractError, SCHEMAS, validate_bundle
from onebridge.evidence_contracts.ledger import EvidenceLedger


def test_migrated_contracts_available_from_installed_package():
    assert len(tuple(SCHEMAS.glob("*.schema.json"))) == 7
    assert (SCHEMAS / "ProjectSpec.schema.json").is_file()


def test_evidence_validation_does_not_approve():
    try:
        validate_bundle([])
    except ContractError:
        pass  # An invalid bundle is refused; it is not an approval.
    else:
        raise AssertionError("empty evidence unexpectedly validated")


def test_onebridge_evidence_cli_handles_missing_input(tmp_path: Path, capsys):
    exit_code = main(["evidence-validate", str(tmp_path / "missing.json")])
    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_existing_evidence_classifier_not_shadowed():
    from onebridge.evidence import classify_evidence, NEEDS_REVIEW
    assert classify_evidence() == NEEDS_REVIEW
