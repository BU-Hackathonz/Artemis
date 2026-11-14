"""Child processes get os.environ without the API key, workspace ID and launch token (audit findings 2 and 3)."""

import os
import threading

from artemis.childenv import child_environ, hidden_secret_values, is_secret_name, secrets_hidden_from_environ

SECRETS = {
    "ANTHROPIC_API_KEY": "sk-ant-api03-FAKEfake_1234-abcdEFGH",
    "ANTHROPIC_WORKSPACE_ID": "ws-fake-123456",
    "ARTEMIS_TOKEN": "launch-token-fake-123456",
    "GITHUB_TOKEN": "ghp-fake-123456",
    "APP_SECRET": "app-secret-fake-123",
    "lower_api_key": "lower-case-fake-123",
}
KEPT = {
    "PATH": "/usr/bin:/bin",
    "SYSTEMROOT": r"C:\Windows",
    "TEMP": r"C:\Users\x\AppData\Local\Temp",
    "TMP": r"C:\Users\x\AppData\Local\Temp",
    "USERPROFILE": r"C:\Users\x",
    "LOCALAPPDATA": r"C:\Users\x\AppData\Local",
    "APPDATA": r"C:\Users\x\AppData\Roaming",
    "HOME": "/home/x",
    "PLAYWRIGHT_BROWSERS_PATH": "/opt/pw-browsers",
    "ARTEMIS_RUN_PYTHON": "/usr/bin/python3.12",
    "ARTEMIS_AGENT": "claude",
}


def test_secret_names_are_recognised():
    for name in SECRETS:
        assert is_secret_name(name), name
    for name in KEPT:
        assert not is_secret_name(name), name


def test_child_environ_drops_secrets_and_keeps_what_children_need():
    env = child_environ({**SECRETS, **KEPT})
    assert set(env) == set(KEPT)
    assert all(value in env.values() for value in KEPT.values())


def test_child_environ_defaults_to_os_environ(monkeypatch):
    for name, value in {**SECRETS, **KEPT}.items():
        monkeypatch.setenv(name, value)
    env = child_environ()
    assert not (set(SECRETS) & set(env))
    assert env["PATH"] == KEPT["PATH"]


def test_child_environ_does_not_modify_os_environ(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRETS["ANTHROPIC_API_KEY"])
    child_environ()
    assert os.environ["ANTHROPIC_API_KEY"] == SECRETS["ANTHROPIC_API_KEY"]


def test_secrets_are_hidden_only_inside_the_block_and_restored(monkeypatch):
    for name, value in {**SECRETS, **KEPT}.items():
        monkeypatch.setenv(name, value)
    before = dict(os.environ)
    with secrets_hidden_from_environ():
        assert not (set(SECRETS) & set(os.environ))
        assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == KEPT["PLAYWRIGHT_BROWSERS_PATH"]
    assert dict(os.environ) == before


def test_secrets_are_restored_after_an_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRETS["ANTHROPIC_API_KEY"])
    try:
        with secrets_hidden_from_environ():
            raise RuntimeError("launch failed")
    except RuntimeError:
        pass
    assert os.environ["ANTHROPIC_API_KEY"] == SECRETS["ANTHROPIC_API_KEY"]


def test_nested_blocks_restore_only_when_the_outer_one_exits(monkeypatch):
    for name, value in {**SECRETS, **KEPT}.items():
        monkeypatch.setenv(name, value)
    before = dict(os.environ)
    with secrets_hidden_from_environ():
        with secrets_hidden_from_environ():
            assert not (set(SECRETS) & set(os.environ))
        # The inner block has exited, but the outer one is still running: nothing comes back yet.
        assert not (set(SECRETS) & set(os.environ))
    assert dict(os.environ) == before


def test_an_inner_exception_does_not_restore_early(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRETS["ANTHROPIC_API_KEY"])
    with secrets_hidden_from_environ():
        try:
            with secrets_hidden_from_environ():
                raise RuntimeError("inner launch failed")
        except RuntimeError:
            pass
        assert "ANTHROPIC_API_KEY" not in os.environ
    assert os.environ["ANTHROPIC_API_KEY"] == SECRETS["ANTHROPIC_API_KEY"]


def test_overlapping_threads_keep_secrets_hidden_until_the_last_one_leaves(monkeypatch):
    for name, value in {**SECRETS, **KEPT}.items():
        monkeypatch.setenv(name, value)
    before = dict(os.environ)
    first_in, second_in, first_out = threading.Event(), threading.Event(), threading.Event()
    errors = []

    def first():
        try:
            with secrets_hidden_from_environ():
                first_in.set()
                second_in.wait(5)
            first_out.set()  # the second thread is still inside its block
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    def second():
        try:
            first_in.wait(5)
            with secrets_hidden_from_environ():
                second_in.set()
                first_out.wait(5)
                # The first thread has left; the secrets must still be hidden for this one.
                assert not (set(SECRETS) & set(os.environ))
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors, errors
    assert dict(os.environ) == before


def test_hidden_values_are_visible_to_redaction_helpers_only_while_hidden(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRETS["ANTHROPIC_API_KEY"])
    assert SECRETS["ANTHROPIC_API_KEY"] not in hidden_secret_values()
    with secrets_hidden_from_environ():
        assert SECRETS["ANTHROPIC_API_KEY"] in hidden_secret_values()
    assert SECRETS["ANTHROPIC_API_KEY"] not in hidden_secret_values()
