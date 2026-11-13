"""G4.1 release hardening: upload privacy notice, local Streamlit usage-statistics opt-out, pinned direct dependencies.

These tests guard configuration and wording only. They do not test (and cannot test) what a hosting provider does:
Streamlit Community Cloud overrides `browser.gatherUsageStats` to true, so the repository setting is local only."""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTICE = ("Privacy: When SpecForge is used on a hosted website, documents you upload are transmitted to the hosting "
          "server for processing. Do not upload confidential documents. To process documents on your own machine, "
          "run SpecForge locally.")


def _app():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(str(ROOT / "app" / "ui.py"), default_timeout=60)


def test_privacy_notice_is_shown_on_first_load():
    at = _app()
    at.run()
    assert not at.exception
    assert any(i.value == NOTICE for i in at.info), [i.value for i in at.info]


def test_privacy_notice_is_still_shown_with_a_result():
    at = _app()
    at.run()
    at.checkbox(key="use_example").check().run()
    next(b for b in at.button if b.label == "Run audit").click().run()
    assert not at.exception and "audit" in at.session_state
    assert any(i.value == NOTICE for i in at.info)


def test_privacy_notice_comes_before_the_upload_widgets():
    src = (ROOT / "app" / "ui.py").read_text(encoding="utf-8")
    assert 0 < src.index("PRIVACY_NOTICE)") < src.index("st.file_uploader(")


def test_ui_docstring_no_longer_claims_no_network_unconditionally():
    first = (ROOT / "app" / "ui.py").read_text(encoding="utf-8").split('"""')[1]
    assert "No network use." not in first
    assert "hosted" in first.lower()


def test_local_streamlit_usage_statistics_are_disabled():
    cfg = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert cfg == {"browser": {"gatherUsageStats": False}}


def test_direct_dependencies_are_pinned_exactly():
    lines = [l.strip() for l in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.lstrip().startswith("#")]
    names = set()
    for line in lines:
        m = re.fullmatch(r"([A-Za-z0-9_.-]+)==([0-9][0-9A-Za-z.]*)(\s*;\s*python_version\s*(<|>=)\s*\"3\.12\")?", line)
        assert m, f"not an exact pin: {line!r}"
        names.add(m.group(1).lower())
    assert names == {"fastapi", "uvicorn", "pint", "pydantic", "streamlit", "pypdf", "mcp", "hypothesis", "pytest", "httpx"}
    assert "streamlit==1.67.0" in lines


def test_readme_separates_local_and_hosted_operation():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "### Local operation" in text and "### Hosted operation (planned, not yet deployed)" in text
    assert "gatherUsageStats = true" in text                     # the Community Cloud override is disclosed
    assert "uses no network" not in text                        # the old unconditional claim is gone
    assert "Use the example documents" not in text              # matches the real checkbox label
