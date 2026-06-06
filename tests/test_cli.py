from pmfi.cli import replay_fixtures, review_pass


def test_fixture_replay_runs(capsys):
    rc = replay_fixtures()
    captured = capsys.readouterr()
    assert rc == 0
    assert "fixture replay complete" in captured.out


def test_review_pass_prints_windows_path_without_control_chars(capsys):
    from pmfi.cli import review_pass

    rc = review_pass()
    captured = capsys.readouterr()
    assert rc == 0
    assert "python scripts\\verify.py" in captured.out
    assert "\x0b" not in captured.out



def test_review_pass_prints_windows_command(capsys):
    rc = review_pass()
    captured = capsys.readouterr()
    assert rc == 0
    assert r"python scripts\verify.py" in captured.out
    assert "\x0b" not in captured.out
