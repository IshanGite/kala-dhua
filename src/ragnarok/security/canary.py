"""Canaries (ARCHITECTURE 5.2 item 10): decoy files and tokens no legitimate action ever touches. Any touch means panic.

* decoy files in canary/ (passwords.txt, .env, id_rsa) with unique content;
* a decoy API key stored as a memory fact and excluded from retrieval;
* check_text(): did a decoy token appear in a draft, a model output or a skill result?
* check_files(): was a decoy file read? (best effort: access time, plus sandbox violation reports in Phase 6)
"""

from __future__ import annotations

import os
from pathlib import Path

from ragnarok.core.ids import new_id, sha256_hex
from ragnarok.gateway_scan.scan import contains_any
from ragnarok.memory.ledger import Ledger
from ragnarok.security import panic

DECOYS = {
    "passwords.txt": "bank: {t}\nemail: {t}\n",
    ".env": "API_KEY={t}\nSECRET={t}\n",
    "id_rsa": "-----BEGIN FAKE KEY-----\n{t}\n-----END FAKE KEY-----\n",
}


class CanaryManager:
    def __init__(self, ledger: Ledger, data_dir: Path) -> None:
        self._l, self.dir = ledger, data_dir / "canary"
        self.tokens: list[str] = []
        self._files: dict[Path, int] = {}

    def plant(self) -> list[str]:
        self.dir.mkdir(parents=True, exist_ok=True)
        for name, template in DECOYS.items():
            token = f"CANARY-{new_id()[:16]}"
            path = self.dir / name
            path.write_text(template.format(t=token), encoding="utf-8", newline="\n")
            os.utime(path, (1_000_000_000, os.stat(path).st_mtime))  # old access time: any later read moves it
            self._files[path] = int(os.stat(path).st_atime)
            self.tokens.append(token)
            self._l.add_canary("file", sha256_hex(token))
        return list(self.tokens)

    def check_text(self, text: str, run_dir: Path, task_id: str | None = None) -> bool:
        """True (and panic) if a decoy token shows up where it never should."""
        if contains_any(text, self.tokens):
            self._l.record_incident("canary_token_seen", ["CANARY_TOKEN_IN_TEXT"], task_id)
            panic.trigger(run_dir, "CANARY_TOKEN_IN_TEXT")
            return True
        return False

    def check_files(self, run_dir: Path) -> bool:
        for path, atime0 in self._files.items():
            try:
                if int(os.stat(path).st_atime) > atime0:
                    self._l.record_incident("canary_file_touched", ["CANARY_FILE_TOUCHED"])
                    panic.trigger(run_dir, "CANARY_FILE_TOUCHED")
                    return True
            except OSError:
                continue
        return False
