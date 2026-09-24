from onebridge.cli import main


def test_cli_qualify_rejects_mock_adapter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = main(["qualify", "--adapter", "flowise"])
    assert code == 3
