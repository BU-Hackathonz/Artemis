"""One test per path rule in design D1, plus the happy paths."""

import os
import sys

import pytest

from artemis.policy.paths import (
    MAX_READ_BYTES,
    MAX_WRITE_BYTES,
    PathRejected,
    check_read_size,
    check_write_size,
    safe_resolve,
)


@pytest.fixture
def root(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    return project


def rejected(root, rel, **kwargs) -> str:
    with pytest.raises(PathRejected) as info:
        safe_resolve(root, rel, **kwargs)
    return info.value.reason


def test_happy_path(root):
    assert safe_resolve(root, "main.py") == root.resolve() / "main.py"


WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="a backslash is only a path separator on Windows"
)


def test_nested_folders(root):
    assert safe_resolve(root, "src/pkg/mod.py") == root.resolve() / "src" / "pkg" / "mod.py"


@WINDOWS_ONLY
def test_nested_folders_backslash(root):
    assert safe_resolve(root, "src\\pkg\\mod.py") == root.resolve() / "src" / "pkg" / "mod.py"


def test_dotdot_that_stays_inside_is_allowed(root):
    assert safe_resolve(root, "src/../main.py") == root.resolve() / "main.py"


def test_empty(root):
    assert "empty" in rejected(root, "")
    assert "empty" in rejected(root, "   ")


@pytest.mark.parametrize("rel", ["/etc/passwd", "\\Windows\\win.ini"])
def test_absolute(root, rel):
    assert "absolute" in rejected(root, rel)


@pytest.mark.parametrize("rel", ["C:\\Windows\\win.ini", "C:/x.py", "c:main.py"])
def test_drive_letter(root, rel):
    assert "drive letter" in rejected(root, rel)


@pytest.mark.parametrize("rel", ["\\\\server\\share\\x.py", "//server/share/x.py"])
def test_unc(root, rel):
    assert "network path" in rejected(root, rel)


@pytest.mark.parametrize("rel", ["\\\\?\\C:\\x.py", "//?/C:/x.py"])
def test_extended_prefix(root, rel):
    assert "prefix" in rejected(root, rel)


@pytest.mark.parametrize(
    "rel",
    ["../x.py", pytest.param("..\\x.py", marks=WINDOWS_ONLY), "a/../../x.py", "a/b/../../../x.py"],
)
def test_dotdot_escape(root, rel):
    assert "outside the project" in rejected(root, rel)


def test_sibling_with_same_prefix_is_outside(root):
    (root.parent / "proj2").mkdir()
    assert "outside the project" in rejected(root, "../proj2/x.py")


def test_case_differences_stay_inside(root):
    # Windows paths are case-insensitive; a differently cased root still matches.
    if sys.platform == "win32":
        shouted = type(root)(str(root).upper())
        assert safe_resolve(shouted, "main.py").name == "main.py"


def test_link_that_points_outside(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "link"
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(outside), str(link))
    else:
        os.symlink(outside, link, target_is_directory=True)
    assert "outside the project" in rejected(root, "link/x.py")


@pytest.mark.parametrize("rel", ["main.py:stream", "a/b.txt::$DATA"])
def test_alternate_data_stream(root, rel):
    assert "colon" in rejected(root, rel)


@pytest.mark.parametrize("rel", ["main.py.", "main.py ", "dir./x.py", "..."])
def test_trailing_dot_or_space(root, rel):
    assert "ends with a dot or a space" in rejected(root, rel)


@pytest.mark.parametrize(
    "rel", ["CON", "con.py", "NUL.txt", "aux", "PRN.tar.gz", "COM1.py", "lpt9.txt", "src/nul.py"]
)
def test_reserved_device_names(root, rel):
    assert "reserved" in rejected(root, rel)


def test_names_that_only_look_reserved_are_allowed(root):
    assert safe_resolve(root, "console.py").name == "console.py"
    assert safe_resolve(root, "com10.py").name == "com10.py"


@pytest.mark.parametrize("rel", [".git/config", ".GIT/hooks/pre-commit.py", "sub/.git/x.py"])
def test_git_folder(root, rel):
    assert ".git" in rejected(root, rel)


@pytest.mark.parametrize("rel", [".env", ".env.local", "config/.ENV.production"])
def test_env_files(root, rel):
    assert ".env" in rejected(root, rel)


@pytest.mark.parametrize(
    "rel", ["run.bat", "x.cmd", "x.ps1", "x.vbs", "x.exe", "x.dll", "x.lnk", "x.reg", "Makefile"]
)
def test_write_extension_allowlist(root, rel):
    assert "only write" in rejected(root, rel, for_write=True)
    # Reading such a file is allowed.
    assert safe_resolve(root, rel).name == rel


@pytest.mark.parametrize("rel", ["a.py", "b.TXT", "c.md", "d.json", "e.toml", "f.csv", "g.html", "h.css", "i.js"])
def test_write_extensions_allowed(root, rel):
    assert safe_resolve(root, rel, for_write=True).name == rel


def test_read_size_limit(root):
    big = root / "big.txt"
    big.write_bytes(b"x" * (MAX_READ_BYTES + 1))
    with pytest.raises(PathRejected):
        check_read_size(big, "big.txt")
    ok = root / "ok.txt"
    ok.write_bytes(b"x" * MAX_READ_BYTES)
    check_read_size(ok, "ok.txt")


def test_write_size_limit():
    check_write_size("x" * MAX_WRITE_BYTES, "a.py")
    with pytest.raises(PathRejected):
        check_write_size("x" * (MAX_WRITE_BYTES + 1), "a.py")


def test_project_files_skips_links_caches_and_env(root, tmp_path):
    from artemis.policy.paths import project_files

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x")
    for rel in ["main.py", "src/b.py", "src/a.py", ".git/config", ".venv/lib/x.py", ".env.local"]:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")
    link = root / "link"
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(outside), str(link))
    else:
        os.symlink(outside, link, target_is_directory=True)
    assert project_files(root) == ["main.py", "src/a.py", "src/b.py"]
    assert project_files(root, root / "src") == ["src/a.py", "src/b.py"]
