"""baton tests: python3 -m unittest discover tests

Every test runs in throwaway home folders with a local bare repo as the remote and a fake usage server, so
nothing touches the real ~/.claude or the network.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "baton")
_loader = importlib.machinery.SourceFileLoader("baton", SCRIPT)
_spec = importlib.util.spec_from_loader("baton", _loader)
baton = importlib.util.module_from_spec(_spec)
_loader.exec_module(baton)

USAGE = {}  # token -> (five_hour %, seven_day %) or None for 401


SEEN_ELSEWHERE = []  # Authorization headers that reached the second server


class Elsewhere(BaseHTTPRequestHandler):
    def do_GET(self):
        SEEN_ELSEWHERE.append(self.headers.get("Authorization"))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


class Usage(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.elsewhere_port}/x")
            self.end_headers()
            return
        tok = self.headers.get("Authorization", "").replace("Bearer ", "")
        if USAGE.get(tok) is None:
            self.send_response(401)
            self.end_headers()
            return
        five, week = USAGE[tok]
        body = json.dumps({"five_hour": {"utilization": five, "resets_at": "2030-01-01T10:00:00.123456789Z"},
                           "seven_day": {"utilization": week, "resets_at": "2030-01-05T10:00:00+00:00"}}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Usage)
        cls.elsewhere = HTTPServer(("127.0.0.1", 0), Elsewhere)
        cls.server.elsewhere_port = cls.elsewhere.server_port
        for srv in (cls.server, cls.elsewhere):
            threading.Thread(target=srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.elsewhere.shutdown()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="baton-test-")
        self.remote = os.path.join(self.tmp, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.remote], check=True)
        USAGE.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def machine(self, name, claude_md=None):
        home = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(home, ".claude"))
        if claude_md:
            with open(os.path.join(home, ".claude", "CLAUDE.md"), "w") as f:
                f.write(claude_md)
        self.login(home, ".claude", f"{name}-claude")
        return home

    def login(self, home, folder, tok):
        os.makedirs(os.path.join(home, folder), exist_ok=True)
        with open(os.path.join(home, folder, ".credentials.json"), "w") as f:
            json.dump({"claudeAiOauth": {"accessToken": tok}}, f)

    def env(self, home, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "BATON", "GIT_", "TMUX"))}
        env.update(HOME=home, XDG_CACHE_HOME=os.path.join(home, ".cache"), GIT_CONFIG_NOSYSTEM="1",
                   BATON_USAGE_URL=f"http://127.0.0.1:{self.server.server_port}/usage",
                   PATH=os.path.join(home, ".local", "bin") + os.pathsep + os.environ["PATH"])
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        env.update(extra)
        return env

    def run_baton(self, home, *args, stdin="", script=SCRIPT, cwd=None, **extra):
        r = subprocess.run([sys.executable, script, *args], env=self.env(home, **extra), cwd=cwd or home,
                           input=stdin, capture_output=True, text=True, timeout=60)
        return r

    def ok(self, home, *args, **kw):
        r = self.run_baton(home, *args, **kw)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def hook(self, home, name, event="PostToolUse", **extra):
        stdin = json.dumps({"session_id": extra.pop("session", "s1"), "hook_event_name": event, "cwd": home})
        out = self.ok(home, "hook", name, stdin=stdin, **extra).strip()
        return json.loads(out) if out else None

    def remote_files(self):
        r = subprocess.run(["git", "--git-dir", self.remote, "ls-tree", "-r", "--name-only", "main"],
                           capture_output=True, text=True)
        return r.stdout.split()

    def remote_show(self, path):
        return subprocess.run(["git", "--git-dir", self.remote, "show", f"main:{path}"],
                              capture_output=True, text=True).stdout


class Setup(Base):
    def test_init_adopts_rules_and_folders(self):
        home = self.machine("a", claude_md="Always use tabs.\n")
        os.makedirs(os.path.join(home, ".claude", "agents"))
        with open(os.path.join(home, ".claude", "agents", "reviewer.md"), "w") as f:
            f.write("a reviewer")
        self.ok(home, "init", "--remote", self.remote)

        b, c = os.path.join(home, ".baton"), os.path.join(home, ".claude")
        self.assertTrue(os.path.islink(os.path.join(c, "CLAUDE.md")))
        shared = open(os.path.join(b, "CLAUDE.md")).read()
        self.assertIn("Always use tabs.", shared)
        self.assertIn("## baton", shared)
        self.assertEqual(open(os.path.join(b, "agents", "reviewer.md")).read(), "a reviewer")
        self.assertTrue(os.path.islink(os.path.join(c, "agents")))
        self.assertTrue(os.path.exists(os.path.join(c, "baton-backup", "CLAUDE.md")))
        s = json.load(open(os.path.join(c, "settings.json")))
        self.assertEqual(s["autoMemoryDirectory"], "~/.baton/memory")
        self.assertEqual(sorted(s["hooks"]), ["PostToolUse", "SessionStart", "Stop", "UserPromptSubmit"])
        self.assertIn("statusline", s["statusLine"]["command"])
        self.assertIn("memory/MEMORY.md", self.remote_files())
        self.assertIn(".baton/baton", self.remote_files())

    def test_add_and_rerun_are_idempotent(self):
        home = self.machine("a")
        self.ok(home, "init", "--remote", self.remote)
        self.ok(home, "init")
        self.ok(home, "add")       # the next free letter: b
        self.ok(home, "add", "b")  # again by name: no change
        for folder in (".claude", ".claude-b"):
            s = json.load(open(os.path.join(home, folder, "settings.json")))
            for event, groups in s["hooks"].items():
                self.assertEqual(len(groups), 1, (folder, event))
        launcher = open(os.path.join(home, ".local", "bin", "claudeb")).read()
        self.assertIn('CLAUDE_CONFIG_DIR="$HOME/.claude-b"', launcher)
        self.assertEqual(list(json.load(open(os.path.join(home, ".baton", "local.json")))["accounts"]),
                         ["claude", "claudeb"])

    def test_keeps_other_hooks_and_statusline(self):
        home = self.machine("a")
        with open(os.path.join(home, ".claude", "settings.json"), "w") as f:
            json.dump({"statusLine": {"type": "command", "command": "mine"},
                       "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}}, f)
        self.ok(home, "init")
        s = json.load(open(os.path.join(home, ".claude", "settings.json")))
        self.assertEqual(s["statusLine"]["command"], "mine")
        self.assertEqual([h["command"] for g in s["hooks"]["Stop"] for h in g["hooks"]][0], "say done")
        self.assertEqual(len(s["hooks"]["Stop"]), 2)

    def test_leave_restores(self):
        home = self.machine("a")
        self.ok(home, "init")
        self.ok(home, "add")
        self.ok(home, "leave", os.path.join(home, ".claude-b"))
        d = os.path.join(home, ".claude-b")
        self.assertFalse(os.path.islink(os.path.join(d, "CLAUDE.md")))
        self.assertIn("## baton", open(os.path.join(d, "CLAUDE.md")).read())
        self.assertNotIn("hooks", json.load(open(os.path.join(d, "settings.json"))))
        self.assertFalse(os.path.exists(os.path.join(home, ".local", "bin", "claudeb")))
        self.assertEqual(list(json.load(open(os.path.join(home, ".baton", "local.json")))["accounts"]), ["claude"])


class Handoff(Base):
    def three(self):
        home = self.machine("a")
        self.ok(home, "init")
        for x in "bc":
            self.ok(home, "add", x)
            self.login(home, f".claude-{x}", f"a-claude-{x}")
        return home

    def test_quiet_below_threshold(self):
        home = self.three()
        USAGE.update({"a-claude": (50, 10)})
        self.assertIsNone(self.hook(home, "usage"))

    def test_hands_off_to_roomiest(self):
        home = self.three()
        USAGE.update({"a-claude": (96, 20), "a-claude-b": (40, 10), "a-claude-c": (12, 30)})
        out = self.hook(home, "usage")
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("HANDOFF.md", ctx)
        self.assertIn("next --window", ctx)
        self.assertIn("claudec", out["systemMessage"])
        self.assertIsNone(self.hook(home, "usage"))  # reminded once per 10 minutes per session
        self.assertIsNotNone(self.hook(home, "usage", session="s2"))

    def test_weekly_limit_counts(self):
        home = self.three()
        USAGE.update({"a-claude": (10, 97), "a-claude-b": (5, 99), "a-claude-c": (70, 20)})
        out = self.hook(home, "usage", event="UserPromptSubmit")
        self.assertIn("weekly", out["hookSpecificOutput"]["additionalContext"])
        self.assertIn("claudec", out["systemMessage"])
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")

    def test_every_account_full(self):
        home = self.three()
        USAGE.update({"a-claude": (99, 0), "a-claude-b": (98, 0), "a-claude-c": (97, 0)})
        out = self.hook(home, "usage")
        self.assertIn("every account is near its limit", out["hookSpecificOutput"]["additionalContext"])
        r = self.run_baton(home, "next", "--print")
        self.assertEqual(r.returncode, 1)

    def test_unreadable_account_comes_last(self):
        home = self.three()
        USAGE.update({"a-claude": (96, 0), "a-claude-b": None, "a-claude-c": (60, 0)})
        self.assertIn("claudec", self.hook(home, "usage")["systemMessage"])
        USAGE.update({"a-claude-c": (99, 0)})
        shutil.rmtree(os.path.join(home, ".cache"))
        self.assertIn("claudeb", self.hook(home, "usage", session="s3")["systemMessage"])

    def test_next_print_and_status(self):
        home = self.three()
        USAGE.update({"a-claude": (96, 20), "a-claude-b": (40, 10), "a-claude-c": (12, 30)})
        line = self.ok(home, "next", "--print", cwd=os.path.join(home, ".baton")).strip()
        self.assertTrue(line.startswith("cd "), line)
        self.assertIn("&& claudec 'Read HANDOFF.md and continue.'", line)
        status = self.ok(home, "status")
        self.assertIn("96%", status)
        self.assertIn("claudec", status)
        bar = self.ok(home, "statusline", CLAUDE_CONFIG_DIR=os.path.join(home, ".claude-b"))
        self.assertIn("claude 96% resets", bar)
        self.assertIn("\033[1mclaudeb 40%\033[0m", bar)

    @unittest.skipIf(sys.platform == "darwin", "macOS always has Terminal")
    def test_window_without_display_prints_command(self):
        home = self.three()
        USAGE.update({"a-claude": (96, 20), "a-claude-b": (40, 10), "a-claude-c": (12, 30)})
        bindir = os.path.join(home, ".local", "bin")
        with open(os.path.join(bindir, "claude"), "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(os.path.join(bindir, "claude"), 0o755)
        r = self.run_baton(home, "next", "--window")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("claudec 'Read HANDOFF.md and continue.'", r.stdout)

    def test_hot_path_is_fast(self):
        home = self.three()
        USAGE.update({"a-claude": (20, 10)})
        self.hook(home, "usage")  # fills the cache
        s = json.load(open(os.path.join(home, ".claude", "settings.json")))
        cmd = s["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        env = self.env(home)
        start = time.time()
        for _ in range(20):
            r = subprocess.run(["sh", "-c", cmd], env=env, input="{}", capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (0, ""))
        per_call = (time.time() - start) / 20
        print(f"\n  usage hook after a tool call: {per_call * 1000:.1f} ms", file=sys.stderr)
        no_python = cmd.replace(os.path.realpath(SCRIPT), "/nonexistent/baton")  # the fast path never reaches it
        self.assertNotEqual(no_python, cmd)
        self.assertEqual(subprocess.run(["sh", "-c", no_python], env=env, input=b"{}").returncode, 0)
        USAGE.update({"a-claude": (97, 10)})
        os.remove(baton_cache(home, "usage"))  # next reading is over the limit: the ok file goes
        os.utime(baton_cache(home, "ok"), (0, 0))
        r = subprocess.run(["sh", "-c", cmd], env=env, input=json.dumps({"session_id": "z"}), capture_output=True, text=True)
        self.assertIn("USAGE HANDOFF", r.stdout)
        self.assertFalse(os.path.exists(baton_cache(home, "ok")))


class Sync(Base):
    def two(self):
        a = self.machine("a", claude_md="Rule from A.\n")
        self.ok(a, "init", "--remote", self.remote)
        b = self.machine("b", claude_md="Rule from B.\n")
        self.ok(b, "init", "--remote", self.remote)
        self.hook(a, "start", event="SessionStart")
        return a, b

    def edit(self, home, rel, text):
        p = os.path.join(home, ".baton", rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)

    def test_second_machine_joins_and_shares(self):
        a, b = self.two()
        shared = open(os.path.join(a, ".baton", "CLAUDE.md")).read()
        self.assertIn("Rule from A.", shared)
        self.assertIn("Rule from B.", shared)

    def test_stop_pushes_start_reports(self):
        a, b = self.two()
        self.edit(a, "memory/editor.md", "Uses vim.\n")
        self.edit(a, "memory/MEMORY.md", "# Memory\n\n- [Editor](editor.md) — vim\n")
        self.hook(a, "stop", event="Stop")
        self.assertIn("memory/editor.md", self.remote_files())
        out = self.hook(b, "start", event="SessionStart")
        self.assertIn("memory/editor.md", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(open(os.path.join(b, ".baton", "memory", "editor.md")).read(), "Uses vim.\n")
        self.assertIsNone(self.hook(b, "start", event="SessionStart"))  # nothing new: silent

    def test_text_clash_keeps_both_sides(self):
        a, b = self.two()
        self.hook(b, "start", event="SessionStart")
        self.edit(a, "memory/MEMORY.md", "# Memory\n\n- [Tabs](tabs.md) — tabs\n")
        self.edit(a, "memory/tabs.md", "tabs\n")
        self.edit(b, "memory/MEMORY.md", "# Memory\n\n- [Coffee](coffee.md) — black\n")
        self.edit(b, "memory/coffee.md", "black\n")
        self.hook(a, "stop", event="Stop")
        self.hook(b, "stop", event="Stop")
        index = self.remote_show("memory/MEMORY.md")
        self.assertIn("tabs.md", index)
        self.assertIn("coffee.md", index)
        self.assertNotIn("<<<<<<<", index)
        out = self.hook(b, "start", event="SessionStart")
        self.assertIn("keeping both sides", out["hookSpecificOutput"]["additionalContext"])

    def test_json_clash_saves_other_side(self):
        a, b = self.two()
        self.edit(a, "agents/conf.json", '{"model": "x"}\n')
        self.hook(a, "stop", event="Stop")
        self.hook(b, "start", event="SessionStart")
        self.edit(a, "agents/conf.json", '{"model": "a"}\n')
        self.edit(b, "agents/conf.json", '{"model": "b"}\n')
        self.hook(a, "stop", event="Stop")
        self.hook(b, "stop", event="Stop")
        files = self.remote_files()
        self.assertIn("agents/conf.other.json", files)
        sides = {self.remote_show("agents/conf.json").strip(), self.remote_show("agents/conf.other.json").strip()}
        self.assertEqual(sides, {'{"model": "a"}', '{"model": "b"}'})
        out = self.hook(b, "start", event="SessionStart")
        self.assertIn("conf.other.json", out["hookSpecificOutput"]["additionalContext"])

    def test_index_checks(self):
        a, _ = self.two()
        self.edit(a, "memory/stray.md", "x\n")
        self.edit(a, "memory/MEMORY.md", "# Memory\n" + "- line\n" * 205)
        out = self.hook(a, "start", event="SessionStart")
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("stray.md", ctx)
        self.assertIn("first 200", ctx)

    def test_cloud_session(self):
        a, _ = self.two()
        self.edit(a, "memory/MEMORY.md", "# Memory\n\n- [Lang](lang.md) — Python\n")
        self.edit(a, "memory/lang.md", "Python\n")
        self.hook(a, "stop", event="Stop")
        cloud = os.path.join(self.tmp, "cloud")
        repo = os.path.join(cloud, "repo")
        subprocess.run(["git", "clone", "-q", self.remote, repo], check=True)
        subprocess.run(["git", "-C", repo, "checkout", "-q", "-b", "claude/session-1"], check=True)
        cloud_home = os.path.join(cloud, "home")
        os.makedirs(cloud_home)
        vendored = os.path.join(repo, ".baton", "baton")
        stdin = json.dumps({"hook_event_name": "SessionStart"})
        r = self.run_baton(cloud_home, "hook", "cloud-start", script=vendored, stdin=stdin, cwd=repo,
                           CLAUDE_CODE_REMOTE="true", CLAUDE_PROJECT_DIR=repo)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("lang.md", ctx)
        self.assertTrue(same_dir(os.path.join(cloud_home, ".baton"), repo))
        with open(os.path.join(repo, "memory", "cloud.md"), "w") as f:
            f.write("from the cloud\n")
        self.run_baton(cloud_home, "hook", "cloud-stop", script=vendored, cwd=repo,
                       CLAUDE_CODE_REMOTE="true", CLAUDE_PROJECT_DIR=repo)
        self.assertIn("memory/cloud.md", self.remote_files())  # pushed to main, not the session branch
        # the laptop's own hooks do nothing in the cloud, and the cloud hooks nothing on the laptop
        self.assertEqual(self.run_baton(a, "hook", "cloud-start").stdout, "")


class Choices(Base):
    def test_scan_finds_accounts_and_changes_nothing(self):
        home = self.machine("a", claude_md="Be brief.\n")
        self.login(home, ".claude-x", "a-x")
        self.login(home, "work-claude", "a-work")
        with open(os.path.join(home, ".bashrc"), "w") as f:
            f.write('alias cx="CLAUDE_CONFIG_DIR=$HOME/.claude-x claude"\n')
        with open(os.path.join(home, ".zshrc"), "w") as f:
            f.write('cw() { CLAUDE_CONFIG_DIR=~/work-claude claude "$@"; }\n')
        os.makedirs(os.path.join(home, ".claude", "projects", "p1", "memory"))
        with open(os.path.join(home, ".claude", "projects", "p1", "memory", "fact.md"), "w") as f:
            f.write("x")
        os.makedirs(os.path.join(home, ".claude-old"))  # not an account: no settings, sign-in or projects
        USAGE.update({"a-claude": (12, 3), "a-x": (40, 50)})
        before = sorted(os.walk(home))
        out = self.ok(home, "scan")
        self.assertEqual(sorted(os.walk(home)), before)
        self.assertIn("accounts  3", out)
        self.assertIn("`cx` in ~/.bashrc", out)
        self.assertIn("`cw` in ~/.zshrc", out)
        self.assertNotIn("5-hour", out)  # sends nothing unless asked
        out = self.ok(home, "scan", "--usage")
        self.assertIn("5-hour 12%", out)
        self.assertIn("signed in, but sign-in expired", out)  # a-work isn't known to the usage server
        self.assertIn("CLAUDE.md 1 lines", out)
        self.assertIn("per-project memory: 1 files in 1 projects", out)
        self.assertIn("baton     not set up", out)
        self.assertNotIn("a-claude", out)  # no tokens

    def test_pieces_can_be_left_out_and_added_later(self):
        home = self.machine("a")
        self.ok(home, "init", "--no-handoff", "--no-statusline")
        self.ok(home, "add")
        for folder in (".claude", ".claude-b"):
            s = json.load(open(os.path.join(home, folder, "settings.json")))
            self.assertEqual(sorted(s["hooks"]), ["SessionStart", "Stop"])
            self.assertNotIn("statusLine", s)
        self.ok(home, "init", "--handoff")
        for folder in (".claude", ".claude-b"):
            s = json.load(open(os.path.join(home, folder, "settings.json")))
            self.assertEqual(sorted(s["hooks"]), ["PostToolUse", "SessionStart", "Stop", "UserPromptSubmit"])
            self.assertNotIn("statusLine", s)
        self.assertIn("ok    claudeb: rules", self.run_baton(home, "doctor").stdout)

    def test_add_existing_folder(self):
        home = self.machine("a")
        self.login(home, "work-claude", "a-work")
        with open(os.path.join(home, "work-claude", "CLAUDE.md"), "w") as f:
            f.write("Work rule.\n")
        self.ok(home, "init")
        self.ok(home, "add", "work", "--dir", os.path.join(home, "work-claude"))
        self.assertTrue(os.path.islink(os.path.join(home, "work-claude", "CLAUDE.md")))
        self.assertIn("Work rule.", open(os.path.join(home, ".baton", "CLAUDE.md")).read())
        launcher = open(os.path.join(home, ".local", "bin", "claudework")).read()
        self.assertIn('CLAUDE_CONFIG_DIR="$HOME/work-claude"', launcher)
        self.assertFalse(os.path.exists(os.path.join(home, ".claude-work")))
        self.assertIn("joined", self.ok(home, "scan"))


def baton_cache(home, kind):
    return os.path.join(home, ".cache", "baton", kind + os.path.realpath(os.path.join(home, ".claude")).replace(os.sep, "%"))


def same_dir(x, y):
    return os.path.realpath(x) == os.path.realpath(y)


class Protect(Base):
    def test_secrets_never_reach_the_remote(self):
        home = self.machine("a")
        skill = os.path.join(home, ".claude", "skills", "deploy")
        os.makedirs(skill)
        with open(os.path.join(skill, ".env"), "w") as f:
            f.write("X=1\n")
        with open(os.path.join(skill, "run.sh"), "w") as f:
            f.write("curl -H 'Authorization: token ghp_" + "a" * 36 + "' https://api.github.com\n")
        with open(os.path.join(skill, "SKILL.md"), "w") as f:
            f.write("Deploys the site.\n")
        self.ok(home, "init", "--remote", self.remote)
        files = self.remote_files()
        self.assertIn("skills/deploy/SKILL.md", files)
        self.assertNotIn("skills/deploy/.env", files)
        self.assertNotIn("skills/deploy/run.sh", files)
        self.assertTrue(os.path.exists(os.path.join(home, ".claude", "skills", "deploy", "run.sh")))  # still works here
        out = self.hook(home, "start", event="SessionStart")
        self.assertIn("skills/deploy/run.sh", out["hookSpecificOutput"]["additionalContext"])
        with open(os.path.join(skill, "run.sh"), "a") as f:
            f.write("echo more\n")
        self.hook(home, "stop", event="Stop")
        self.assertNotIn("skills/deploy/run.sh", self.remote_files())  # stays out on later syncs

    def test_secret_added_to_a_synced_file_never_deletes_it_elsewhere(self):
        a = self.machine("a")
        self.ok(a, "init", "--remote", self.remote)
        b = self.machine("b")
        self.ok(b, "init", "--remote", self.remote)
        notes = os.path.join(a, ".baton", "memory", "notes.md")
        with open(notes, "w") as f:
            f.write("Deploys go through CI.\n")
        self.hook(a, "stop", event="Stop")
        self.hook(b, "start", event="SessionStart")
        with open(notes, "a") as f:
            f.write("api_key = " + "k" * 30 + "\n")
        self.hook(a, "stop", event="Stop")
        self.assertEqual(self.remote_show("memory/notes.md"), "Deploys go through CI.\n")  # clean version stays
        self.hook(b, "start", event="SessionStart")
        self.assertEqual(open(os.path.join(b, ".baton", "memory", "notes.md")).read(), "Deploys go through CI.\n")
        self.assertIn("api_key", open(notes).read())  # A keeps its edit
        with open(os.path.join(b, ".baton", "memory", "other.md"), "w") as f:
            f.write("from b\n")
        self.hook(b, "stop", event="Stop")
        out = self.hook(a, "start", event="SessionStart")  # syncs with the held edit in the way
        self.assertTrue(os.path.exists(os.path.join(a, ".baton", "memory", "other.md")))
        self.assertIn("api_key", open(notes).read())
        self.assertIn("synced before", out["hookSpecificOutput"]["additionalContext"])

    def test_stuck_rebase_never_loses_edits(self):
        a = self.machine("a")
        self.ok(a, "init", "--remote", self.remote)
        b = self.machine("b")
        self.ok(b, "init", "--remote", self.remote)
        repo = os.path.join(a, ".baton")
        for home, data in ((a, b"\x00a"), (b, b"\x00b")):  # a binary clash stops a plain rebase
            with open(os.path.join(home, ".baton", "agents", "blob.bin"), "wb") as f:
                f.write(data)
        self.hook(b, "stop", event="Stop")
        env = self.env(a)
        subprocess.run(["git", "add", "-A"], cwd=repo, env=env)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "a"], cwd=repo, env=env)
        subprocess.run(["git", "fetch", "-q", "origin"], cwd=repo, env=env)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "rebase", "origin/main"], cwd=repo,
                       env=env, capture_output=True)  # stops half done, as if git were killed
        self.assertTrue(os.path.exists(os.path.join(repo, ".git", "rebase-merge")))
        with open(os.path.join(repo, "memory", "fresh.md"), "w") as f:
            f.write("saved during the stuck rebase\n")
        with open(os.path.join(repo, "CLAUDE.md"), "a") as f:  # tracked files too
            f.write("A rule added while stuck.\n")
        self.assertIn("in progress", self.ok(a, "sync"))  # commits nothing on a half-done rebase
        old = time.time() - 3600
        os.utime(os.path.join(repo, ".git", "rebase-merge"), (old, old))
        self.ok(a, "sync")
        self.assertFalse(os.path.exists(os.path.join(repo, ".git", "rebase-merge")))
        self.assertEqual(open(os.path.join(repo, "memory", "fresh.md")).read(), "saved during the stuck rebase\n")
        self.assertIn("memory/fresh.md", self.remote_files())
        self.assertIn("A rule added while stuck.", open(os.path.join(repo, "CLAUDE.md")).read())
        self.assertIn("A rule added while stuck.", self.remote_show("CLAUDE.md"))

    def test_settings_link_and_own_memory_folder_survive(self):
        home = self.machine("a")
        dot = os.path.join(home, "dotfiles", "claude-settings.json")
        os.makedirs(os.path.dirname(dot))
        with open(dot, "w") as f:
            json.dump({"autoMemoryDirectory": "~/notes", "model": "x"}, f)
        os.chmod(dot, 0o600)
        p = os.path.join(home, ".claude", "settings.json")
        os.symlink(dot, p)
        out = self.ok(home, "init")
        self.assertIn("~/notes is no longer loaded", out)
        self.assertTrue(os.path.islink(p))
        self.assertEqual(os.stat(dot).st_mode & 0o777, 0o600)
        self.assertEqual(json.load(open(dot))["autoMemoryDirectory"], "~/.baton/memory")
        self.ok(home, "leave", os.path.join(home, ".claude"))
        restored = json.load(open(dot))
        self.assertEqual(restored, {"autoMemoryDirectory": "~/notes", "model": "x"})
        self.assertTrue(os.path.islink(p))

    def test_rename_with_a_secret_is_still_caught(self):
        home = self.machine("a")
        self.ok(home, "init", "--remote", self.remote)
        plan = os.path.join(home, ".baton", "memory", "plan.md")
        with open(plan, "w") as f:
            f.write("".join(f"step {i}\n" for i in range(30)))
        self.ok(home, "sync")
        moved = os.path.join(home, ".baton", "memory", "plan2.md")
        os.rename(plan, moved)
        with open(moved, "a") as f:
            f.write("api_key = sk-ant-api03-" + "x" * 40 + "\n")
        self.ok(home, "sync")
        self.assertNotIn("sk-ant", self.remote_show("memory/plan2.md"))

    def test_clash_commit_is_filtered_too(self):
        a, b = self.machine("a"), self.machine("b")
        for h in (a, b):
            self.ok(h, "init", "--remote", self.remote)
        repo = os.path.join(a, ".baton")
        for h, data in ((a, '{"v": "a"}\n'), (b, '{"v": "b"}\n')):  # a JSON clash: rebase fails, merge saves it
            with open(os.path.join(h, ".baton", "commands", "c.json"), "w") as f:
                f.write(data)
        self.hook(b, "stop", event="Stop")
        env = self.env(a)
        subprocess.run(["git", "add", "-A"], cwd=repo, env=env)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "a"], cwd=repo, env=env)
        subprocess.run(["git", "fetch", "-q", "origin"], cwd=repo, env=env)
        with open(os.path.join(repo, "memory", "leak.md"), "w") as f:  # appears mid-sync, after the first commit
            f.write("api_key = " + "z" * 30 + "\n")
        code = ("import importlib.machinery as m, importlib.util as u, sys;"
                f"l=m.SourceFileLoader('b', {SCRIPT!r}); s=u.spec_from_loader('b', l); b=u.module_from_spec(s);"
                "l.exec_module(b); sys.exit(0 if b.integrate('origin/main') else 1)")
        r = subprocess.run([sys.executable, "-c", code], cwd=repo, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        committed = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=repo, env=env,
                                   capture_output=True, text=True).stdout
        self.assertIn("commands/c.other.json", committed)
        self.assertNotIn("leak.md", committed)

    def test_account_folder_cant_be_inside_the_shared_repo(self):
        home = self.machine("a")
        self.ok(home, "init")
        r = self.run_baton(home, "add", "x", "--dir", os.path.join(home, ".baton", "x"))
        self.assertNotEqual(r.returncode, 0)

    def test_leave_needs_a_real_account(self):
        home = self.machine("a")
        self.ok(home, "init")
        r = self.run_baton(home, "leave", os.path.join(home, "typo"))
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(home, "typo")))

    def test_token_never_follows_a_redirect(self):
        home = self.machine("a")
        self.ok(home, "init")
        url = f"http://127.0.0.1:{self.server.server_port}/redirect"
        self.assertIn("HTTP 302", self.ok(home, "status", BATON_USAGE_URL=url))
        self.assertEqual(SEEN_ELSEWHERE, [])

    def test_stuck_merge_keeps_hand_fixed_files(self):
        a = self.machine("a")
        self.ok(a, "init", "--remote", self.remote)
        b = self.machine("b")
        self.ok(b, "init", "--remote", self.remote)
        repo = os.path.join(a, ".baton")
        for home, data in ((a, '{"a": 1}\n'), (b, '{"a": 2}\n')):  # a JSON clash the driver can't mix
            with open(os.path.join(home, ".baton", "commands", "cfg.json"), "w") as f:
                f.write(data)
        self.hook(b, "stop", event="Stop")
        env = self.env(a)
        subprocess.run(["git", "add", "-A"], cwd=repo, env=env)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "a"], cwd=repo, env=env)
        subprocess.run(["git", "fetch", "-q", "origin"], cwd=repo, env=env)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "rebase", "origin/main"], cwd=repo,
                       env=env, capture_output=True)
        with open(os.path.join(repo, "commands", "cfg.json"), "w") as f:
            f.write('{"a": 1, "b": 2}\n')  # fixed by hand, then left
        old = time.time() - 3600
        os.utime(os.path.join(repo, ".git", "rebase-merge"), (old, old))
        out = self.ok(a, "sync")
        self.assertIn("half-merged copies are kept", out)
        kept = [os.path.join(top, f) for top, _, files in os.walk(os.path.join(repo, ".git"))
                if "baton-undone-" in top for f in files if f == "cfg.json"]
        self.assertEqual([open(k).read() for k in kept], ['{"a": 1, "b": 2}\n'])

    def test_cloud_copy_only_moves_forward(self):
        home = self.machine("a")
        self.ok(home, "init")
        copy = os.path.join(home, ".baton", ".baton", "baton")
        newer = open(copy).read().replace(f'VERSION = "{baton.VERSION}"', 'VERSION = "99.0.0"')
        with open(copy, "w") as f:
            f.write(newer)
        self.ok(home, "sync")
        self.assertIn('VERSION = "99.0.0"', open(copy).read())  # an older baton never downgrades it
        tmp = os.path.join(self.tmp, "m")
        os.makedirs(tmp)
        paths = {}
        for side, text in (("base", "VERSION = \"0.1.0\"\n"), ("ours", "VERSION = \"0.1.0\"\nold\n"),
                           ("theirs", "VERSION = \"0.2.0\"\nnew\n")):
            paths[side] = os.path.join(tmp, side)
            with open(paths[side], "w") as f:
                f.write(text)
        self.assertEqual(baton.cmd_merge([paths["base"], paths["ours"], paths["theirs"], ".baton/baton"]), 0)
        self.assertEqual(open(paths["ours"]).read(), "VERSION = \"0.2.0\"\nnew\n")  # newer wins whole, no mix


class Safety(unittest.TestCase):
    def test_secret_patterns(self):
        import re
        for text in ("ghp_" + "a" * 36, "sk-ant-api03-" + "x" * 40, "API_KEY=abcd1234efgh5678ijkl",
                     '"token": "abcdefghijklmnop1234"', "-----BEGIN OPENSSH PRIVATE KEY-----"):
            self.assertTrue(re.search(baton.SECRET_TEXT, text), text)
        for text in ("password: use-a-password-manager", "Token: never-print-the-value", "secret = in-1Password-vault",
                     "- [Keys](notes/keys.md) — where we keep secrets", "token budget is 200 lines"):
            self.assertFalse(re.search(baton.SECRET_TEXT, text), text)

    def test_redacts_url_secrets(self):
        self.assertEqual(baton.redact("https://me:ghp_x@github.com/me/r.git"), "https://***@github.com/me/r.git")
        self.assertEqual(baton.redact("git@github.com:me/r.git"), "git@github.com:me/r.git")


class Merge(unittest.TestCase):
    def merge(self, base, ours, theirs):
        L = baton.lines
        return "".join(baton.union(L(base), L(ours), L(theirs)))

    def test_separate_edits(self):
        base = "a\nb\nc\nd\ne\n"
        self.assertEqual(self.merge(base, "A\nb\nc\nd\ne\n", "a\nb\nc\nd\nE\n"), "A\nb\nc\nd\nE\n")

    def test_same_spot_keeps_both(self):
        self.assertEqual(self.merge("x\n", "x\nours\n", "x\ntheirs\n"), "x\nours\ntheirs\n")

    def test_nearby_clashes_dont_double(self):
        base = "1\n2\n3\n4\n5\n"
        out = self.merge(base, "1x\n2\n3\n4y\n5\n", "1z\n2\n3\n4w\n5\n")
        self.assertEqual(out.count("2\n"), 1)
        for line in ("1x", "1z", "4y", "4w"):
            self.assertIn(line, out)


if __name__ == "__main__":
    unittest.main()
