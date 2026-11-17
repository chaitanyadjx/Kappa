"""Integration step for the Kappa pipeline (SPEC.md: Integration)."""

from __future__ import annotations

import difflib
from pathlib import Path


def integrate(original_path, new_source: str, out_path=None) -> dict:
    original = Path(original_path)
    original_text = original.read_text(encoding="utf-8") if original.is_file() else ""
    target = Path(out_path) if out_path is not None else original
    changed = new_source != original_text
    if changed:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_source, encoding="utf-8")
    diff = "".join(
        difflib.unified_diff(
            original_text.splitlines(keepends=True),
            new_source.splitlines(keepends=True),
            fromfile=str(original),
            tofile=str(target),
        )
    )
    return {"written": str(target), "changed": changed, "diff": diff}


if __name__ == "__main__":
    import tempfile

    demo = Path(__file__).resolve().parent / "examples" / "demo_violations.py"
    source = demo.read_text(encoding="utf-8")
    patched = source.replace("sum_value(v)", "sum_values(v)")
    with tempfile.TemporaryDirectory() as tmp_dir:
        out = Path(tmp_dir) / "demo_repaired.py"
        info = integrate(demo, patched, out)
        assert info["changed"] is True, "changed must be True for modified source"
        assert info["written"] == str(out), "written must name the output path"
        assert out.is_file(), "output file was not written"
        assert out.read_text(encoding="utf-8") == patched, "written content mismatch"
        assert info["diff"].strip(), "diff must be non-empty for modified source"
        print(info["diff"], flush=True)

        unchanged_path = Path(tmp_dir) / "unchanged.py"
        unchanged = integrate(demo, source, unchanged_path)
        assert unchanged["changed"] is False, "unchanged source must not be rewritten"
        assert not unchanged_path.exists(), "unchanged source must not be written"
        assert unchanged["diff"] == "", "unchanged source must produce an empty diff"
    print("[kappa] Integrater smoke test passed", flush=True)
