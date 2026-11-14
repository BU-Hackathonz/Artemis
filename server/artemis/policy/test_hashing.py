from artemis.policy.hashing import approval_hash, run_payload, write_payload


def h(files, workspace="hello-world"):
    return approval_hash("write_files", workspace, write_payload(files))


def test_format_and_determinism():
    a = h([("main.py", None, "print(1)\n")])
    assert a.startswith("sha256:") and len(a) == len("sha256:") + 64
    assert a == h([("main.py", None, "print(1)\n")])


def test_file_order_does_not_matter():
    one = ("a.py", None, "1")
    two = ("b.py", b"old", "2")
    assert h([one, two]) == h([two, one])


def test_changes_with_content_base_path_and_workspace():
    base = h([("main.py", b"old", "new")])
    assert base != h([("main.py", b"old", "new2")])
    assert base != h([("main.py", b"old2", "new")])
    assert base != h([("main2.py", b"old", "new")])
    assert base != h([("main.py", b"old", "new")], workspace="other")


def test_new_file_and_empty_file_hash_the_same_base():
    assert h([("main.py", None, "x")]) == h([("main.py", b"", "x")])


def test_run_hash_changes_with_entry_args_and_file():
    def r(entry="main.py", args=(), content=b"print(1)"):
        return approval_hash("run_program", "hello-world", run_payload(entry, list(args), content))

    assert r() == r()
    assert r() != r(entry="other.py")
    assert r() != r(args=["--fast"])
    assert r() != r(content=b"print(2)")
    assert r() != approval_hash("write_files", "hello-world", run_payload("main.py", [], b"print(1)"))
