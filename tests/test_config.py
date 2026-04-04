from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from iirs.config import load_local_env_files


class ConfigTests(unittest.TestCase):
    def test_load_local_env_files_reads_unset_values_without_overriding_existing_ones(self) -> None:
        previous = {
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
            "IIRS_AGENT_MODEL": os.environ.get("IIRS_AGENT_MODEL"),
            "IIRS_EMBEDDING_MODEL": os.environ.get("IIRS_EMBEDDING_MODEL"),
        }

        try:
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ["IIRS_AGENT_MODEL"] = "existing-model"
            os.environ.pop("IIRS_EMBEDDING_MODEL", None)

            with tempfile.TemporaryDirectory() as tmp_dir:
                env_path = Path(tmp_dir) / ".env.local"
                env_path.write_text(
                    "\n".join(
                        [
                            "# comment",
                            "OPENAI_API_KEY=sk-local-test",
                            "IIRS_AGENT_MODEL=gpt-5-mini",
                            "export IIRS_EMBEDDING_MODEL='text-embedding-3-small'",
                        ]
                    ),
                    encoding="utf-8",
                )

                load_local_env_files([env_path])

            self.assertEqual(os.environ.get("OPENAI_API_KEY"), "sk-local-test")
            self.assertEqual(os.environ.get("IIRS_AGENT_MODEL"), "existing-model")
            self.assertEqual(os.environ.get("IIRS_EMBEDDING_MODEL"), "text-embedding-3-small")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
