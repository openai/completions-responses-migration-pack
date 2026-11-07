"""Offline CLI integration tests. Run: python3 -B -m unittest discover -s tests -v.

Set MIGRATION_TEST_BASH to test another Bash. All fixtures, fake HOME/cache,
and command recordings stay under this checkout's .tmp/.cache.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/completions-to-responses-upgrade.sh"
BASH = os.environ.get("MIGRATION_TEST_BASH", "/bin/bash")
GIT = shutil.which("git")
CACHE = ROOT / ".tmp/.cache"


class DryRunTests(unittest.TestCase):
    def setUp(self):
        CACHE.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="migration-", dir=CACHE)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "target with spaces"
        self.bin = self.base / "bin"
        self.record = self.base / "record"
        self.tmp = self.base / "tmp"
        for path in (self.repo, self.bin, self.record, self.tmp, self.base / "home"):
            path.mkdir()
        # No inherited PATH, Git settings, credentials, or real service commands.
        self.env = {
            "PATH": str(self.bin), "HOME": str(self.base / "home"),
            "TMPDIR": str(self.tmp), "XDG_CACHE_HOME": str(self.base / "cache"),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C", "RECORD": str(self.record),
            "OPENAI_API_KEY": "synthetic-test-placeholder", "DANGEROUS_CONFIRM": "1",
        }
        # Allow only the local shell tools used by the bootstrap; no real
        # codex, package managers, network clients, or login executable in PATH.
        for name in ("bash", "git", "uname", "grep", "dirname", "date",
                     "mkdir", "cat", "cp", "paste", "rm", "find", "tr", "chmod"):
            executable = BASH if name == "bash" else shutil.which(name)
            self.assertIsNotNone(executable, name)
            (self.bin / name).symlink_to(executable)
        # macOS mktemp may use the OS user temp directory despite TMPDIR.
        # Pin kit creation to the fixture even when exercising the old script.
        mktemp = self.bin / "mktemp"
        mktemp.write_text("#!" + sys.executable + "\nimport os, tempfile\n"
                          "print(tempfile.mkdtemp(dir=os.environ['TMPDIR']))\n")
        mktemp.chmod(0o755)
        stub = "#!" + sys.executable + "\n" + '''
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
record = pathlib.Path(os.environ["RECORD"])
args = sys.argv[1:]
with (record / "calls.jsonl").open("a") as out:
    out.write(json.dumps([name, args]) + "\\n")
if name != "codex" or "exec" not in args:
    sys.exit(97)  # Fail closed for installs, auth, network, and publication.
(record / "args.json").write_text(json.dumps(args))
(record / "prompt.txt").write_text(args[-1])
agents = pathlib.Path("AGENTS.md")
(record / "agents.json").write_text(json.dumps(agents.read_text() if agents.exists() else None))
(record / "kit.txt").write_text(os.environ.get("CODEX_KIT_DIR", ""))
if os.environ.get("FAKE_EDIT") == "1":
    pathlib.Path("app.py").write_text("# synthetic migration\\n")
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
'''
        for name in ("codex", "gh", "curl", "npm", "brew", "tar", "ssh"):
            path = self.bin / name
            path.write_text(stub)
            path.chmod(0o755)
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", "/dev/null")
        self.git("config", "core.filemode", "false")
        (self.repo / "app.py").write_text("# legacy completions call site\n")
        (self.repo / ".gitignore").write_text("ignored/\n")
        (self.repo / "package.json").write_text('{"scripts":{"build":"echo build"}}\n')
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        (self.repo / "untracked.bak").write_text("preserve untracked backup\n")
        (self.repo / "ignored").mkdir()
        (self.repo / "ignored/keep.orig").write_text("preserve ignored backup\n")

    def git(self, *args):
        return subprocess.check_output([GIT, *args], cwd=self.repo, env=self.env).decode().strip()

    def guidance(self, kind):
        if kind == "absent":
            return
        (self.repo / "AGENTS.md").write_text("# Existing guidance\nPreserve this exactly.\n")
        if kind == "tracked":
            self.git("add", "AGENTS.md")
            self.git("commit", "-qm", "guidance")
        elif kind == "ignored":
            with (self.repo / ".git/info/exclude").open("a") as out:
                out.write("\nAGENTS.md\n")

    def snapshot(self):
        # Include all Git metadata (index, HEAD, refs, reflogs, objects), tracked,
        # untracked, ignored files, and mtimes to detect same-content rewrites.
        return {str(p.relative_to(self.repo)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.repo.rglob("*") if p.is_file()}

    def run_script(self, *flags, stdin=False):
        command = [BASH, "-s", "--"] if stdin else [BASH, str(SCRIPT)]
        return subprocess.run(command + ["--repo", str(self.repo), "--no-interactive", *flags],
                              input=SCRIPT.read_text() if stdin else None, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              cwd=self.repo, env=self.env, timeout=30)

    def assert_dry_run(self, kind="tracked", flags=("--dry-run",), stdin=False, failure=0):
        self.guidance(kind)
        before = self.snapshot()
        result = self.run_script(*flags, stdin=stdin)
        self.assertEqual(result.returncode, failure, result.stdout + result.stderr)
        # Check each independent contract even on the broken baseline.
        with self.subTest(contract="workspace unchanged"):
            self.assertEqual(self.snapshot(), before)
        with self.subTest(contract="no kit artifacts"):
            self.assertEqual(list(self.tmp.iterdir()), [])
        with self.subTest(contract="guidance unchanged during agent"):
            expected = None if kind == "absent" else "# Existing guidance\nPreserve this exactly.\n"
            self.assertEqual(json.loads((self.record / "agents.json").read_text()), expected)
        with self.subTest(contract="no auth, install, network, or PR"):
            calls = [json.loads(line) for line in (self.record / "calls.jsonl").read_text().splitlines()]
            self.assertEqual([name for name, _ in calls], ["codex"])
        args = json.loads((self.record / "args.json").read_text())
        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
        self.assertNotIn("--full-auto", args)
        self.assertNotIn("danger-full-access", args)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
        with self.subTest(contract="self-contained read-only prompt"):
            prompt = (self.record / "prompt.txt").read_text()
            for phrase in ("Completions", "Responses", "client.responses.create", "store=false",
                           "WITHOUT writing to disk", "suggest", "test", "build"):
                self.assertIn(phrase, prompt)
        if not failure:
            with self.subTest(contract="dry-run summary"):
                self.assertIn("Dry-run complete", result.stdout)
                self.assertNotIn("push the branch", result.stdout)
        return result

    def test_tracked_guidance(self):
        self.assert_dry_run()

    def test_untracked_guidance(self):
        self.assert_dry_run("untracked")

    def test_ignored_guidance(self):
        self.assert_dry_run("ignored")

    def test_no_guidance(self):
        self.assert_dry_run("absent")

    def test_failing_agent(self):
        self.env["FAKE_EXIT"] = "23"
        self.assert_dry_run(failure=23)

    def test_failing_agent_without_guidance(self):
        self.env["FAKE_EXIT"] = "23"
        self.assert_dry_run("absent", failure=23)

    def test_cli_conflicting_flags(self):
        self.assert_dry_run(flags=("--dry-run", "--full-auto", "--dangerous",
                                   "--auto-commit", "--open-pr", "--branch", "must-not-exist"))

    def test_environment_flags_and_inherited_cleanup(self):
        self.env.update(DRY_RUN="1", MODE_FULL_AUTO="1", DANGEROUS_MODE="1",
                        AUTO_COMMIT="1", OPEN_PR="1", AGENTS_CREATED_BY_SCRIPT="1",
                        CODEX_KIT_DIR="/nonexistent/inherited-kit", BRANCH_NAME="must-not-exist")
        self.assert_dry_run(flags=())

    def test_cli_overrides_environment_write(self):
        self.env["DRY_RUN"] = "0"
        self.assert_dry_run()

    def test_stdin_fallback(self):
        self.assert_dry_run(stdin=True)
        prompt = (self.record / "prompt.txt").read_text()
        self.assertNotIn(str(ROOT / "docs"), prompt)

    def test_local_source_docs(self):
        self.assert_dry_run()
        self.assertIn(str(ROOT / "scripts/../docs"), (self.record / "prompt.txt").read_text())

    def test_dirty_refusal(self):
        (self.repo / "app.py").write_text("dirty\n")
        before = self.snapshot()
        result = self.run_script("--dry-run", "--open-pr")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to proceed with dirty tree", result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.record / "calls.jsonl").exists())

    def test_staged_dirty_refusal(self):
        (self.repo / "app.py").write_text("staged\n")
        self.git("add", "app.py")
        before = self.snapshot()
        result = self.run_script("--dry-run")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to proceed with dirty tree", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_explicit_write_control(self):
        self.env.update(DRY_RUN="1", FAKE_EDIT="1")
        head = self.git("rev-parse", "HEAD")
        result = self.run_script("--write", "--no-auto-commit", "--branch", "write-control")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git("branch", "--show-current"), "write-control")
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual((self.repo / "app.py").read_text(), "# synthetic migration\n")
        self.assertFalse((self.repo / "AGENTS.md").exists())
        self.assertIn("Migration Task", json.loads((self.record / "agents.json").read_text()))
        kit = Path((self.record / "kit.txt").read_text())
        self.assertTrue(kit.is_relative_to(self.tmp))
        self.assertTrue((kit / "docs/responses-cheatsheet.md").is_file())
        args = json.loads((self.record / "args.json").read_text())
        self.assertEqual(args[args.index("--sandbox") + 1], "danger-full-access")
        self.assertIn("--full-auto", args)
        self.assertTrue((self.repo / "untracked.bak").exists())


if __name__ == "__main__":
    unittest.main()
