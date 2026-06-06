from pmfi.cli import main


def test_fixture_replay_runs(capsys):
    rc = main(["replay"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "replay" in captured.out.lower() or "fixture" in captured.out.lower()


def test_review_pass_prints_windows_path_without_control_chars(capsys):
    rc = main(["review-pass"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "python scripts\\verify.py" in captured.out
    assert "\x0b" not in captured.out


def test_review_pass_prints_windows_command(capsys):
    rc = main(["review-pass"])
    captured = capsys.readouterr()
    assert rc == 0
    assert r"python scripts\verify.py" in captured.out
    assert "\x0b" not in captured.out
