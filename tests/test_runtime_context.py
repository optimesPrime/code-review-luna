from runtime_context import RuntimeContext


def test_defaults():
    ctx = RuntimeContext()
    assert ctx.project_type == "auto"
    assert ctx.diff_scope == "working tree"
    assert ctx.backend_review_status == "skipped"
    assert ctx.elapsed_seconds == 0.0


def test_custom_values():
    ctx = RuntimeContext(
        project_name="my-app",
        project_type="frontend",
        diff_scope="staged",
        changed_files=3,
        changed_lines=42,
        backend_review_status="ran",
        elapsed_seconds=1.5,
    )
    assert ctx.project_name == "my-app"
    assert ctx.changed_files == 3
    assert ctx.elapsed_seconds == 1.5
