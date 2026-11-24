import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

_BLOCK_RE = re.compile(r"<<<UNTRUSTED_EMAIL kind=(\w+) nonce=([0-9a-f]+)>>>\n(.*)\n<<<END_UNTRUSTED_EMAIL nonce=\2>>>\Z", re.S)


def unwrap(text: str) -> tuple[str, dict]:
    """Parse a wrapped tool result; fails the test if the envelope is malformed."""
    m = _BLOCK_RE.search(text)
    assert m, f"result is not a well-formed untrusted block:\n{text[:500]}"
    return m.group(1), json.loads(m.group(3))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point mabus-mailroom at a temp config dir so tests never touch a real one."""
    monkeypatch.setenv("MABUS_MAILROOM_HOME", str(tmp_path / "mm-home"))
    monkeypatch.delenv("MABUS_MAILROOM_PASSPHRASE", raising=False)
    monkeypatch.delenv("MABUS_MAILROOM_PASSPHRASE_CMD", raising=False)
    return tmp_path / "mm-home"
