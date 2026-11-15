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
    project_files,
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


def test_nested_folders(root):
    assert safe_resolve(root, "src/pkg/mod.py") == root.resolve() / "src" / "pkg" / "mod.py"


def test_nested_folders_backslash(root):
    # A separator on Linux too, so the policy is the same on both desktops.
    assert safe_resolve(root, "src\\pkg\\mod.py") == root.resolve() / "src" / "pkg" / "mod.py"
    assert safe_resolve(root, "src/pkg\\mod.py") == root.resolve() / "src" / "pkg" / "mod.py"


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
    ["../x.py", "..\\x.py", "a\\..\\..\\x.py", "a/../../x.py", "a/b/../../../x.py"],
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


def test_a_root_reached_through_a_link_still_works(root, tmp_path):
    # On Linux ~/ArtemisProjects may be a symlink to another drive (the demo laptop's is).
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(root), str(tmp_path / "alias"))
    else:
        os.symlink(root, tmp_path / "alias", target_is_directory=True)
    (root / "main.py").write_text("x")
    alias = tmp_path / "alias"
    assert safe_resolve(alias, "main.py") == root.resolve() / "main.py"
    assert "outside the project" in rejected(alias, "../x.py")
    assert project_files(alias) == ["main.py"]


def test_a_backslash_path_through_a_link_that_points_outside(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(outside), str(root / "link"))
    else:
        os.symlink(outside, root / "link", target_is_directory=True)
    assert "outside the project" in rejected(root, "link\\x.py")
    assert "outside the project" in rejected(root, "sub\\..\\link\\x.py")


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


def link_dir(target, link):
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def link_file(target, link):
    try:
        os.symlink(target, link)
    except OSError:  # Windows without Developer Mode can't make file symlinks
        pytest.skip("file symlinks need Developer Mode on Windows")


# Issue #158: a link inside the project must not reach .env or .git under another name.

def test_a_file_link_to_an_env_file_is_rejected(root):
    (root / ".env.local").write_text("SYNTHETIC_FIXTURE")
    link_file(root / ".env.local", root / "public.txt")
    assert ".env" in rejected(root, "public.txt")
    assert ".env" in rejected(root, "public.txt", for_write=True)  # nor overwritten through it


def test_a_folder_link_into_git_is_rejected(root):
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("SYNTHETIC_FIXTURE")
    link_dir(root / ".git", root / "docs")
    assert ".git" in rejected(root, "docs/config")
    assert ".git" in rejected(root, "docs\\config")


def test_a_link_reached_through_another_link_is_rejected(root):
    (root / "conf").mkdir()
    (root / "conf" / ".env").write_text("SYNTHETIC_FIXTURE")
    link_dir(root / "conf", root / "a")
    link_file(root / "a" / ".env", root / "conf" / "settings.txt")
    assert ".env" in rejected(root, "a/settings.txt")


def test_ordinary_links_inside_the_project_still_work(root):
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("x")
    link_dir(root / "src", root / "code")
    assert safe_resolve(root, "code/main.py") == root.resolve() / "src" / "main.py"


def test_project_files_leaves_out_links_to_protected_files(root):
    (root / "main.py").write_text("x")
    (root / ".env.local").write_text("SYNTHETIC_FIXTURE")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("SYNTHETIC_FIXTURE")
    link_file(root / ".env.local", root / "public.txt")
    link_file(root / ".git" / "config", root / "notes.txt")
    assert project_files(root) == ["main.py"]


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
