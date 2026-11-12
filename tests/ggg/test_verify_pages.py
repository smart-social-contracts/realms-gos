"""The publication of the verify page (docs/verify/index.html): the workflow that serves it at
https://smart-social-contracts.github.io/realms-gos/verify/ (the address on every journal sheet's QR code), and what the page
promises about itself, that it is one file that loads nothing from elsewhere."""

import pathlib
import re
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "verify-pages.yml"
PAGE = ROOT / "docs" / "verify" / "index.html"
ADDRESS = "https://smart-social-contracts.github.io/realms-gos/verify/"


def assembly():
    """The shell lines of the workflow's step that assembles the site."""
    step = re.search(r"- name: Assemble the site[^\n]*\n\s+run: \|\n((?:[ ]{10}\S[^\n]*\n)+)", WORKFLOW.read_text())
    return [line.strip() for line in step.group(1).splitlines()]


def test_the_site_holds_the_page_alone_under_verify_so_that_it_is_served_where_the_qr_codes_point():
    workflow = WORKFLOW.read_text()
    with tempfile.TemporaryDirectory() as work:
        shutil.copytree(ROOT / "docs" / "verify", pathlib.Path(work) / "docs" / "verify")
        subprocess.run("; ".join(assembly()), shell=True, cwd=work, check=True)
        site = sorted(str(p.relative_to(pathlib.Path(work) / "_site")) for p in (pathlib.Path(work) / "_site").rglob("*") if p.is_file())
        assert site == ["verify/index.html"]  # nothing else of the repository is published, README included
        assert (pathlib.Path(work) / "_site" / "verify" / "index.html").read_bytes() == PAGE.read_bytes()
    assert re.search(r"upload-pages-artifact@v\d+\n\s+with:\n\s+path: _site\n", workflow)
    assert "actions/deploy-pages@" in workflow and "pages: write" in workflow and "id-token: write" in workflow
    assert "name: github-pages" in workflow


def test_the_workflow_runs_on_main_when_the_page_changes_and_by_hand_and_deploys_one_at_a_time():
    workflow = WORKFLOW.read_text()
    assert re.search(r"push:\n\s+branches: \[main\]\n\s+paths:\n\s+- docs/verify/index.html\n", workflow)
    assert "workflow_dispatch:" in workflow
    assert re.search(r"concurrency:\n\s+group: pages\n\s+cancel-in-progress: false", workflow)


def test_the_address_is_the_one_the_docs_and_the_workflow_name():
    for doc in (ROOT / "docs" / "verify" / "README.md", ROOT / "docs" / "codices" / "journal-signature.md"):
        assert ADDRESS in doc.read_text(), doc.name
    assert ADDRESS.removesuffix("verify/") in WORKFLOW.read_text()


def test_the_page_is_one_file_that_loads_no_script_style_frame_or_import_from_elsewhere():
    html = PAGE.read_text()
    assert not re.search(r"<script[^>]*\bsrc\s*=", html)
    assert not re.search(r"<(link|iframe|object|embed|img|video|audio|source)\b", html, re.I)
    assert "@import" not in html and "import(" not in html and "importScripts" not in html
    assert len(re.findall(r"\bfetch\(", html)) == 1  # the one request of the page: a proof, from a replica the reader chose
