# Self tests for the agent loop (agent.py) and its /agent/* endpoints
# (server.py): the shell command gate, the event buffer the phone polls, the
# turn state machine, the system prompt with and without the coach block, the
# approval gate, and the chat card's routes. The model is a scripted stand in
# that never reaches the network, the file tools get a temp root and the coach
# reads temp fixtures, so neither ~/.config/swiv nor ~/.cache is read. The
# endpoints are asked over HTTP of a server on 127.0.0.1 at a free port, with
# a login cookie put into server.SESSIONS by hand instead of a PAM login. Run
# with `make test`.
import atexit
import contextlib
import copy
import http.client
import io
import itertools
import json
import os
import secrets
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# config reads SWIV_CONFIG once, when it is first imported. Unless another
# test module already fixed that path, point it at an empty temp dir (as
# tests/test_core.py does): this module sorts first under `make test`, and
# test_core writes through config.PATH later in the same run.
if "config" not in sys.modules:
    _CFG_DIR = tempfile.mkdtemp(prefix="swiv-agent-cfg-")
    atexit.register(shutil.rmtree, _CFG_DIR, ignore_errors=True)
    os.environ["SWIV_CONFIG"] = os.path.join(_CFG_DIR, "config.json")

import agent  # noqa: E402
import config  # noqa: E402
import server  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# what a refused call comes back as
BLOCKED = "blocked by policy"
DENIED = "denied by user or timeout"
OUTSIDE = "blocked by policy (files_root 밖 경로)"
BLANK = "빈 메시지입니다"  # start_turn on an empty text
BUSY = "이미 진행 중인 요청이 있습니다"  # start_turn while a turn runs or waits
# the fixed cards of the /cards deck (server.py) that the tests below look at
AGENT_CARD = {"kind": "agent", "id": "agent", "title": "agent"}
DESK_CARD = {"kind": "desk", "id": "desk", "title": "desktop"}
FILES_CARD = {"kind": "files", "id": "files", "title": "files"}


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def agent_cfg(**overrides):
    """The default agent config as a private copy, for tests to change."""
    cfg = copy.deepcopy(config.DEFAULTS["agent"])
    cfg.update(overrides)
    return cfg


def tool_call(n, name, arguments):
    """One entry of the "tool_calls" list llm_chat returns. `n` is the call's
    number (int) or an already-formed id string like "call_1"."""
    cid = "call_%d" % n if isinstance(n, int) else str(n)
    return {"id": cid, "name": name, "arguments": arguments}


def reply(text="", calls=()):
    """What llm_chat returns: the model's text and the tool calls it asks for."""
    return {"text": text, "tool_calls": list(calls)}


def types(events):
    return [e["type"] for e in events]


def of_type(events, etype):
    return [e for e in events if e["type"] == etype]


class ScriptedLLM:
    """Stands in for agent.llm_chat: hands out the scripted replies in order
    and records (messages, tools) of every call. `replies` may be endless.
    An exception in the script is raised, as the real client would raise."""

    def __init__(self, replies):
        # a single endless iterator (itertools.repeat) passed as one argument
        # is the script itself, not a one-element script
        if len(replies) == 1 and hasattr(replies[0], "__next__"):
            replies = replies[0]
        self.replies = iter(replies)
        self.calls = []

    def __call__(self, cfg_agent, messages, tools, on_text=None):
        self.calls.append((list(messages), tools))
        try:
            scripted = next(self.replies)
        except StopIteration:
            raise AssertionError("llm_chat was called %d times, more than the script has replies"
                                 % len(self.calls)) from None
        if isinstance(scripted, Exception):
            raise scripted
        return copy.deepcopy(scripted)


class TracedSession(agent.AgentSession):
    """AgentSession that notes, for every event, the state it was emitted in."""

    def __init__(self):
        super().__init__()
        self.trace = []  # (state at emit, event type), oldest first

    def emit(self, etype, **data):
        self.trace.append((self.state, etype))
        return super().emit(etype, **data)


# --- shell command gate --------------------------------------------------------


class ClassifyCommand(unittest.TestCase):
    def setUp(self):
        cfg = agent_cfg()
        self.allow, self.block = cfg["allow_commands"], cfg["block_commands"]

    def grade(self, cmd):
        return agent.classify_command(cmd, self.allow, self.block)

    def test_allowlisted_readonly_commands_are_instant(self):
        for entry in self.allow:  # every entry of the shipped list, run bare
            self.assertEqual(self.grade(entry), "instant", entry)
        for cmd in ("ls -la /tmp", "grep -n swiv README.md", "find . -name '*.py'", "df -h .",
                    "git status", "git log --oneline", "git diff", "git show HEAD"):
            self.assertEqual(self.grade(cmd), "instant", cmd)

    def test_allowlisted_commands_chain_and_pipe_freely(self):
        for cmd in ("ls -la /tmp && git status", "ps aux | grep swiv", "git log || git status",
                    "git diff\ngit status"):  # a line end starts a command too
            self.assertEqual(self.grade(cmd), "instant", cmd)

    def test_unlisted_command_needs_approval(self):
        for cmd in ("touch x", "echo hi", "mkdir x", "pip install x", "python3 script.py",
                    "curl http://example.invalid", "apt update"):
            self.assertEqual(self.grade(cmd), "approval", cmd)

    def test_git_entries_carry_their_subcommand(self):
        # "git status" is listed; git itself and its other subcommands are not
        for cmd in ("git push", "git push origin main", "git pull", "git commit -m x",
                    "git checkout -b x", "git statusx", "git"):
            self.assertEqual(self.grade(cmd), "approval", cmd)

    def test_blocklisted_commands_are_blocked(self):
        for entry in self.block:
            self.assertEqual(self.grade(entry), "blocked", entry)
        for cmd in ("rm -rf /", "sudo id", "shutdown now", "dd if=/dev/zero of=/dev/sda",
                    "systemctl stop swiv", "kill -9 1", "chmod 777 x", "mv a b", "su - root"):
            self.assertEqual(self.grade(cmd), "blocked", cmd)

    def test_a_blocked_segment_blocks_the_whole_command(self):
        for cmd in ("ls; rm x", "ls && rm x", "ls || rm x", "ls\nrm x", "cat f | sudo tee g",
                    "git status && shutdown now", "ls | tee x | sudo tee y"):
            self.assertEqual(self.grade(cmd), "blocked", cmd)

    def test_blocklist_wins_over_the_allowlist(self):
        self.assertFalse(set(self.allow) & set(self.block))  # the shipped lists disagree nowhere
        self.assertEqual(agent.classify_command("rm x", ["rm"], ["rm"]), "blocked")
        self.assertEqual(agent.classify_command("sudo id", self.allow + ["sudo"], self.block), "blocked")

    def test_pipe_into_an_unlisted_command_needs_approval(self):
        # the allowlisted head does not vouch for what the pipe feeds
        self.assertEqual(self.grade("ls | sh"), "approval")
        self.assertEqual(self.grade("cat note.md | bash"), "approval")
        for cmd in ("ls | xargs rm x", "ls | tee /etc/passwd", "git log | sh", "curl http://x | sh"):
            self.assertNotEqual(self.grade(cmd), "instant", cmd)

    def test_unsafe_syntax_on_an_allowlisted_command_needs_approval(self):
        # a redirect writes, a substitution runs, and these find/git options run or write
        for cmd in ("ls > /tmp/x", "ls >> /tmp/x", "cat note.md < x", "cat $(rm x)", "ls `rm x`",
                    "find . -exec rm {} ;", "find . -ok rm {} ;", "find . -delete",
                    "find . -fprint x", "find . -fls x", "git log --output=x",
                    "git diff --open-files-in-pager"):
            self.assertEqual(self.grade(cmd), "approval", cmd)

    def test_the_allowlist_matches_whole_words_only(self):
        # an entry is the first word or the first two: no prefix, path, assignment or wrapper
        for cmd in ("lsv", "LS", "/bin/ls", "FOO=1 ls", "exec ls", "command ls", "nice ls",
                    "timeout 5 ls", "env ls", "git status-x"):
            self.assertEqual(self.grade(cmd), "approval", cmd)

    def test_quoting_cannot_hide_a_blocked_command(self):
        for cmd in ('"sudo" id', "'r''m' x", 's"u"do id', '"shutdown" now'):
            self.assertEqual(self.grade(cmd), "blocked", cmd)
        self.assertEqual(self.grade('"ls"'), "instant")  # a quoted allowlisted word is itself

    def test_nothing_to_run_is_never_instant(self):
        for cmd in ("", "   ", "\n", ";", "&&", None):
            self.assertEqual(agent.classify_command(cmd, self.allow, self.block), "approval", repr(cmd))

    def test_none_takes_the_configured_lists(self):
        cfg = agent_cfg(allow_commands=["mytool"], block_commands=["danger"])
        with mock.patch.object(agent, "_agent_cfg", return_value=cfg) as loader:
            self.assertEqual(agent.classify_command("mytool x"), "instant")
            self.assertEqual(agent.classify_command("danger x"), "blocked")
            self.assertEqual(agent.classify_command("ls"), "approval")  # not on the configured list
            self.assertEqual(agent.classify_command("mytool", block=[]), "instant")  # one side given
            self.assertEqual(agent.classify_command("danger", allow=[]), "blocked")
        self.assertEqual(loader.call_count, 5)


class AgentConfig(unittest.TestCase):
    """agent._agent_cfg: DEFAULTS["agent"] under the file's "agent" object, one level deep."""

    def load(self, file_cfg):
        with mock.patch.object(config, "load", return_value=file_cfg):
            return agent._agent_cfg()

    def test_no_agent_key_gives_the_defaults(self):
        self.assertEqual(self.load({"port": 7000}), config.DEFAULTS["agent"])

    def test_a_partial_agent_object_keeps_the_other_keys(self):
        cfg = self.load({"agent": {"enabled": True, "model": "m", "coach": {"enabled": True}}})
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["model"], "m")
        self.assertEqual(cfg["allow_commands"], config.DEFAULTS["agent"]["allow_commands"])
        self.assertEqual(cfg["approval_timeout_sec"], config.DEFAULTS["agent"]["approval_timeout_sec"])
        # the coach object is merged on its own level
        coach = cfg["coach"]
        self.assertTrue(coach["enabled"])
        self.assertEqual(coach["max_chars"], config.DEFAULTS["agent"]["coach"]["max_chars"])

    def test_the_result_is_a_copy(self):
        cfg = self.load({})
        cfg["allow_commands"].append("rm")
        cfg["coach"]["enabled"] = True
        self.assertNotIn("rm", config.DEFAULTS["agent"]["allow_commands"])
        self.assertFalse(config.DEFAULTS["agent"]["coach"]["enabled"])

    def test_an_unreadable_config_file_gives_the_defaults(self):
        for error in (ValueError("not json"), OSError("permission denied")):
            with mock.patch.object(config, "load", side_effect=error):
                self.assertEqual(agent._agent_cfg(), config.DEFAULTS["agent"], error)


class ToolGate(unittest.TestCase):
    def setUp(self):
        self.cfg = agent_cfg()

    def gates(self, name, arguments):
        call = tool_call(1, name, arguments)
        return agent.gate_denies(call, self.cfg), agent.gate_needs_approval(call, self.cfg)

    def test_readonly_tools_ask_nobody(self):
        self.assertEqual(self.gates("files_list", {}), (False, False))
        self.assertEqual(self.gates("files_read", {"path": "a.md"}), (False, False))

    def test_the_shell_command_decides(self):
        for command, expected in (("ls", (False, False)), ("touch x", (False, True)),
                                  ("rm x", (True, False)), ("ls; rm x", (True, False)),
                                  ("ls | sh", (False, True))):
            self.assertEqual(self.gates("shell_run", {"command": command}), expected, command)

    def test_a_tool_that_is_not_registered_is_denied(self):
        self.assertEqual(self.gates("files_write", {"path": "x"}), (True, False))


# --- event buffer and history --------------------------------------------------


class EventBuffer(unittest.TestCase):
    def setUp(self):
        self.session = agent.AgentSession()

    def idx(self, since):
        return [e["idx"] for e in self.session.events_since(since)]

    def test_idx_starts_at_one_and_never_repeats(self):
        first = self.session.emit("user", text="안녕")
        second = self.session.emit("assistant", text="반갑다")
        self.assertEqual((first["idx"], second["idx"]), (1, 2))
        for event, idx, etype in zip(self.session.events, (1, 2), ("user", "assistant")):
            self.assertEqual((event["idx"], event["type"]), (idx, etype))
            self.assertIsInstance(event["ts"], float)
        self.assertEqual((self.session.events[0]["text"], self.session.events[1]["text"]),
                         ("안녕", "반갑다"))

    def test_events_since_returns_what_comes_after_the_cursor(self):
        for n in range(5):
            self.session.emit("tick", n=n)
        self.assertEqual(self.idx(0), [1, 2, 3, 4, 5])
        self.assertEqual(self.idx(3), [4, 5])  # strictly after the cursor
        self.assertEqual(self.idx(-1), [1, 2, 3, 4, 5])
        self.assertEqual(self.idx(5), [])
        self.assertEqual(self.idx(99), [])

    def test_a_bad_cursor_means_from_the_start(self):
        self.session.emit("tick")
        self.session.emit("tick")
        for bad in (None, "abc", "", "2.5", object()):
            self.assertEqual(self.idx(bad), [1, 2], bad)

    def test_the_buffer_keeps_the_last_events_max(self):
        total = agent.EVENTS_MAX + 500
        for _ in range(total):
            self.session.emit("tick")
        self.assertEqual(len(self.session.events), agent.EVENTS_MAX)
        self.assertEqual(self.idx(0), list(range(total - agent.EVENTS_MAX + 1, total + 1)))
        self.assertEqual(self.session.events_since(0), self.session.events)  # the trimmed stay gone

    def test_the_increment_after_a_trim_is_still_exact(self):
        total = agent.EVENTS_MAX + 500
        for _ in range(total):
            self.session.emit("tick")
        cursor = self.session.events[-1]["idx"]  # what an up to date phone holds
        for n in range(3):
            self.session.emit("late", n=n)
        new = self.session.events_since(cursor)
        self.assertEqual([e["idx"] for e in new], [cursor + 1, cursor + 2, cursor + 3])
        self.assertEqual([e["n"] for e in new], [0, 1, 2])
        self.assertEqual(len(self.session.events), agent.EVENTS_MAX)  # still capped
        # a cursor that fell off the front gets all that is still held, in order, without a gap
        self.assertEqual(self.idx(1), list(range(total - agent.EVENTS_MAX + 4, total + 4)))

    def test_concurrent_emitters_never_repeat_an_idx(self):
        per_thread = agent.EVENTS_MAX // 4 + 25

        def worker():
            for _ in range(per_thread):
                self.session.emit("tick")

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertFalse(any(t.is_alive() for t in threads))
        total = 4 * per_thread
        self.assertEqual(self.idx(0), list(range(total - agent.EVENTS_MAX + 1, total + 1)))


class HistoryWindow(unittest.TestCase):
    def test_the_window_never_opens_on_a_tool_result(self):
        session = agent.AgentSession()
        for message in ({"role": "user", "content": "q1"}, {"role": "assistant", "content": ""},
                        {"role": "tool", "tool_call_id": "c1", "content": "r1"},
                        {"role": "assistant", "content": "a1"}, {"role": "user", "content": "q2"}):
            session.add_message(message)
        contents = lambda limit: [m["content"] for m in session.recent_history(limit)]
        self.assertEqual(contents(30), ["q1", "", "r1", "a1", "q2"])
        self.assertEqual(contents(3), ["a1", "q2"])  # r1 is dropped along with the call that asked
        self.assertEqual(contents(1), ["q2"])
        self.assertEqual(session.recent_history(0), [])


# --- system prompt -------------------------------------------------------------


class SystemPrompt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="swiv-agent-coach-")
        cls.nodes_path = os.path.join(cls.dir, "nodes.ndjson")
        cls.rules_path = os.path.join(cls.dir, "rules.md")
        write(cls.nodes_path, "\n".join([
            json.dumps({"id": "n1", "type": "fact", "name": "jekyll blog post publish flow",
                        "props": {"result": "draft를 쓴 뒤 front matter를 확인하고 build한다."},
                        "keywords": ["jekyll", "blog"], "source": "impl-note", "confidence": 0.9,
                        "ts": "2026-11-20T09:00:00Z", "lastAccessed": "2026-11-20T09:00:00Z",
                        "accessCount": 5}, ensure_ascii=False),
            json.dumps({"id": "n2", "type": "fact", "name": "zfs storage pool layout",
                        "props": {"result": "pool vdev notes"}, "keywords": ["zfs", "storage"],
                        "source": "impl-note", "confidence": 0.9,
                        "ts": "2026-11-20T09:00:00Z", "lastAccessed": "2026-11-20T09:00:00Z",
                        "accessCount": 500}, ensure_ascii=False),
        ]) + "\n")
        write(cls.rules_path,
              "## Rule (2026-07-09)\n"
              "**Diagnose before acting.** Never implement a fix from a problem description.\n"
              "Origin: a cancel was read as a failure.\n"
              "\n"
              "## Rule (2026-11-01)\n"
              "**Measure first.** Set thresholds below the measured value.\n"
              "Origin: tests.\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def cfg(self, enabled, **coach):
        cfg = agent_cfg()
        cfg["coach"].update(enabled=enabled, **coach)
        return cfg

    def fixtures(self, enabled=True, **coach):
        paths = {"nodes_path": self.nodes_path, "rules_path": self.rules_path}
        return self.cfg(enabled, **{**paths, **coach})

    def block(self, cfg, query="jekyll blog"):
        """The part of the prompt that comes after the base prompt."""
        prompt = agent.system_prompt(cfg, query)
        self.assertTrue(prompt.startswith(agent.BASE_PROMPT), "the base prompt must lead")
        return prompt[len(agent.BASE_PROMPT):]

    def test_disabled_leaves_the_base_prompt_alone(self):
        self.assertEqual(self.block(self.fixtures(enabled=False)), "")
        self.assertEqual(self.block(self.cfg(enabled=False)), "")  # the default: no coach paths at all

    def test_no_usable_coach_key_leaves_the_base_prompt_alone(self):
        for cfg in (agent_cfg(), {k: v for k, v in agent_cfg().items() if k != "coach"},
                    agent_cfg(coach=None), agent_cfg(coach="yes"), agent_cfg(coach={})):
            self.assertEqual(self.block(cfg), "")

    def test_enabled_appends_the_rules_then_the_matching_nodes(self):
        block = self.block(self.fixtures())
        self.assertTrue(block.startswith("\n\n[coach]"), block[:40])
        self.assertIn("## 행동 규칙 (검증됨)", block)
        self.assertIn("Diagnose before acting. (2026-07-09)", block)
        self.assertIn("Measure first. (2026-11-01)", block)
        self.assertIn("## 장기 기억 (관련 노드)", block)
        self.assertIn("jekyll blog post publish flow", block)  # matches the query
        self.assertNotIn("zfs storage pool layout", block)  # does not
        self.assertLess(block.index("## 행동 규칙"), block.index("## 장기 기억"))

    def test_enabled_with_nothing_to_inject_gives_the_base_prompt(self):
        missing = os.path.join(self.dir, "nope")
        # nothing on disk at all
        cfg = self.fixtures(nodes_path=missing, rules_path=missing)
        self.assertEqual(self.block(cfg), "")
        self.assertEqual(agent.system_prompt(cfg, "qqzznothing"), agent.BASE_PROMPT)
        # files there, but no node matches the query and the rules file is missing
        self.assertEqual(agent.system_prompt(self.fixtures(rules_path=missing), "qqzznothing"),
                         agent.BASE_PROMPT)

    def test_the_budget_caps_the_block(self):
        cfg = self.fixtures(max_chars=300)
        block = self.block(cfg)
        self.assertTrue(block.startswith("\n\n[coach]"))
        self.assertLessEqual(len(block) - 2, 300)

    def test_a_failing_coach_leaves_the_base_prompt_alone(self):
        err = io.StringIO()
        with mock.patch.object(agent.coach, "load_nodes", side_effect=OSError("disk gone")), \
                contextlib.redirect_stderr(err):
            self.assertEqual(self.block(self.fixtures()), "")
        self.assertIn("coach injection skipped (OSError)", err.getvalue())

    def test_the_turn_sends_it_as_the_system_message(self):
        cfg = self.fixtures()
        llm = ScriptedLLM([reply("답")])
        with mock.patch.object(agent, "llm_chat", llm), \
                mock.patch.object(agent, "_agent_cfg", return_value=cfg):
            self.assertEqual(agent.run_turn(agent.AgentSession(), "jekyll blog"), "done")
        system = llm.calls[0][0][0]
        self.assertEqual(system, {"role": "system", "content": agent.system_prompt(cfg, "jekyll blog")})
        self.assertIn("jekyll blog post publish flow", system["content"])


# --- the turn ------------------------------------------------------------------


class TurnCase(unittest.TestCase):
    """A session whose turns run with the model, the config and the file
    root replaced. `complete_turn` does a whole turn; `start` and `wait_end` split one
    when the test has to act in the middle (the approval gate)."""

    def setUp(self):
        self.cfg = agent_cfg()
        self.root = tempfile.mkdtemp(prefix="swiv-agent-turn-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        write(os.path.join(self.root, "note.txt"), "swiv agent test note\n")
        for patcher in (mock.patch.object(agent, "FILES_ROOT", self.root),
                        mock.patch.object(agent, "_agent_cfg", return_value=self.cfg)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.session = TracedSession()
        self.llm = ScriptedLLM([])

    def turn(self, *replies):
        """Replace llm_chat with the scripted replies."""
        self.llm = ScriptedLLM(replies)
        patcher = mock.patch.object(agent, "llm_chat", self.llm)
        patcher.start()
        self.addCleanup(patcher.stop)
        return self.llm

    def start(self, text="안녕하세요"):
        """Start a turn; the returned idx is that of the last event before it."""
        mark = self.session.events[-1]["idx"] if self.session.events else 0
        self.assertTrue(self.session.start_turn(text))
        return mark

    def wait_end(self, mark):
        """Block until the turn started after `mark` publishes turn_end; returns its events."""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            events = self.session.events_since(mark)
            if events and events[-1]["type"] == "turn_end":
                return events
            time.sleep(0.005)
        self.fail("the turn did not end in 10s (state %s, events %s)"
                  % (self.session.state, types(self.session.events_since(mark))))

    def complete_turn(self, *replies, text="안녕하세요"):
        """A whole turn on the session thread: scripted, started and waited for;
        returns its events. With no `replies` the llm scripted by an earlier
        self.turn(...) is used as is."""
        if replies:
            self.turn(*replies)
        return self.wait_end(self.start(text))

    def request(self, mark, after=None):
        """Block until the turn asks for approval (again after the request `after`)."""
        since = after["idx"] if after else mark
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            requests = of_type(self.session.events_since(since), "approval_request")
            if requests:
                return requests[0]
            time.sleep(0.002)
        self.fail("the turn did not ask for approval in 5s (state %s)" % self.session.state)

    def statuses(self, events):
        """{call_id: status} of a turn's tool results."""
        return {e["call_id"]: e["status"] for e in of_type(events, "tool_result")}


class TurnLoop(TurnCase):
    def test_a_plain_answer_runs_then_goes_idle(self):
        events = self.complete_turn(reply("홈 폴더에 파일이 3개 있다"))
        self.assertEqual(self.session.trace, [("running", "assistant"), ("idle", "turn_end")])
        self.assertEqual(types(events), ["assistant", "turn_end"])
        self.assertEqual(events[0]["text"], "홈 폴더에 파일이 3개 있다")
        self.assertEqual(events[-1]["reason"], "done")
        self.assertEqual(self.session.state, "idle")
        self.assertEqual(self.session.query, "안녕하세요")
        self.assertEqual(self.session.history, [
            {"role": "user", "content": "안녕하세요"},
            {"role": "assistant", "content": "홈 폴더에 파일이 3개 있다"},
        ])
        # the model got the base prompt, the user text and the tool registry, once
        self.assertEqual(len(self.llm.calls), 1)
        messages, tools = self.llm.calls[0]
        self.assertIs(tools, agent.TOOLS)
        self.assertEqual(messages, [{"role": "system", "content": agent.BASE_PROMPT},
                                    {"role": "user", "content": "안녕하세요"}])

    def test_the_next_turn_carries_the_conversation(self):
        self.complete_turn(reply("첫 답"))
        events = self.complete_turn(reply("두 번째 답"), text="이어서")
        self.assertEqual(events[-1]["reason"], "done")
        self.assertEqual([m["role"] for m in self.session.history],
                         ["user", "assistant", "user", "assistant"])
        self.assertEqual([m["content"] for m in self.llm.calls[0][0][1:]],
                         ["안녕하세요", "첫 답", "이어서"])

    def test_empty_model_text_still_reaches_the_phone(self):
        events = self.complete_turn(reply(""))
        self.assertEqual(types(events), ["assistant", "turn_end"])
        self.assertEqual(events[0]["text"], "(빈 응답)")
        self.assertEqual(self.session.history[-1]["content"], "(빈 응답)")

    def test_a_tool_call_runs_then_the_model_is_asked_again(self):
        self.cfg["allow_commands"].append("echo")  # echo hi runs at once only because of this
        events = self.complete_turn(reply(calls=[tool_call(1, "shell_run", {"command": "echo hi"})]),
                          reply("hi라고 출력했다"))
        self.assertEqual(self.session.trace, [
            ("running", "tool_call"), ("running", "tool_result"),
            ("running", "assistant"), ("idle", "turn_end")])
        self.assertEqual(types(events), ["tool_call", "tool_result", "assistant", "turn_end"])
        call, result = events[0], events[1]
        self.assertEqual((call["call_id"], call["name"], call["args"], call["grade"]),
                         ("call_1", "shell_run", {"command": "echo hi"}, "instant"))
        self.assertEqual((result["call_id"], result["name"], result["result"], result["status"]),
                         ("call_1", "shell_run", "hi\n[exit 0]", "ok"))
        self.assertEqual(events[2]["text"], "hi라고 출력했다")
        self.assertEqual(of_type(events, "approval_request"), [])
        self.assertEqual(self.session.history, [
            {"role": "user", "content": "안녕하세요"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "shell_run", "arguments": '{"command": "echo hi"}'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "hi\n[exit 0]"},
            {"role": "assistant", "content": "hi라고 출력했다"},
        ])
        # the second model call is the first one plus the call and its result
        self.assertEqual(len(self.llm.calls), 2)
        self.assertEqual(self.llm.calls[1][0], self.llm.calls[0][0] + self.session.history[1:3])

    def test_max_steps_stops_a_loop_that_keeps_calling_tools(self):
        read = reply(calls=[tool_call(1, "files_read", {"path": "note.txt"})])
        events = self.complete_turn(itertools.repeat(read))
        self.assertEqual(types(events), ["tool_call", "tool_result"] * agent.MAX_STEPS
                         + ["assistant", "turn_end"])
        self.assertEqual(events[-1]["reason"], "max_steps")
        self.assertEqual(len(self.llm.calls), agent.MAX_STEPS)  # one model call per step, none beyond
        self.assertEqual(of_type(events, "tool_call")[0]["grade"], "readonly")
        self.assertEqual(of_type(events, "tool_result")[-1]["result"], "swiv agent test note\n")
        self.assertEqual([m["role"] for m in self.session.history],
                         ["user"] + ["assistant", "tool"] * agent.MAX_STEPS + ["assistant"])
        self.assertTrue(self.session.history[-1]["content"])  # the user is told why it stopped
        self.assertEqual(self.session.state, "idle")

    def test_an_llm_error_ends_the_turn_and_frees_the_session(self):
        self.cfg["api_key"] = "sk-test-secret-000"
        events = self.complete_turn(agent.LLMError('LLM 호출 실패: HTTP 401 {"error": "bad key sk-test-secret-000"}'))
        self.assertEqual(self.session.trace, [("running", "error"), ("idle", "turn_end")])
        self.assertEqual(types(events), ["error", "turn_end"])
        self.assertEqual(events[-1]["reason"], "error")
        self.assertIn("HTTP 401", events[0]["message"])
        self.assertNotIn("sk-test-secret-000", json.dumps(self.session.events, ensure_ascii=False))
        self.assertEqual(self.session.history, [{"role": "user", "content": "안녕하세요"}])
        self.assertEqual(self.session.state, "idle")
        # not stuck: the next turn runs
        events = self.complete_turn(reply("다시 답한다"), text="다시 부탁한다")
        self.assertEqual(events[-1]["reason"], "done")
        self.assertEqual(self.session.history[-1]["content"], "다시 답한다")

    def test_any_other_exception_is_reported_to_the_phone(self):
        events = self.complete_turn(ValueError("boom"))
        self.assertEqual(types(events), ["error", "turn_end"])
        self.assertEqual(events[0]["message"], "ValueError: boom")
        self.assertEqual(events[-1]["reason"], "error")
        self.assertEqual(self.session.state, "idle")

    def test_run_turn_returns_the_turn_end_reason(self):
        endless = itertools.repeat(reply(calls=[tool_call(1, "files_read", {"path": "note.txt"})]))
        for scripted, reason in (([reply("답")], "done"), ([endless], "max_steps"),
                                 ([agent.LLMError("down")], "error")):
            with self.subTest(reason=reason):
                session = agent.AgentSession()
                self.turn(*scripted)
                self.assertEqual(agent.run_turn(session, "질문"), reason)
                self.assertEqual(session.events[-1]["reason"], reason)
                self.assertEqual(session.state, "idle")

    def test_start_turn_runs_on_a_daemon_thread_and_refuses_a_second_turn(self):
        entered, release = threading.Event(), threading.Event()

        def slow_model(cfg_agent, messages, tools, on_text=None):
            entered.set()
            release.wait(10)
            return reply("이제 답한다")

        with mock.patch.object(agent, "llm_chat", slow_model):
            try:
                mark = self.start("첫 질문")
                self.assertEqual(self.session.state, "running")  # set before the thread starts
                self.assertTrue(entered.wait(5))  # the loop is off the test thread by now
                workers = [t for t in threading.enumerate() if t.name == "agent-turn" and t.is_alive()]
                self.assertTrue(workers)
                self.assertTrue(all(t.daemon for t in workers))
                self.assertFalse(self.session.start_turn("겹치는 질문"))  # one turn at a time
                self.assertEqual(self.session.state, "running")
                self.assertEqual([m["content"] for m in self.session.history], ["첫 질문"])
            finally:
                release.set()
            events = self.wait_end(mark)
        self.assertEqual(types(events), ["error", "assistant", "turn_end"])
        self.assertEqual(events[-1]["reason"], "done")
        self.assertEqual([m["content"] for m in self.session.history], ["첫 질문", "이제 답한다"])

    def test_blank_text_starts_no_turn(self):
        for bad in ("", "   ", "\n\t", None, 42):
            self.assertFalse(self.session.start_turn(bad))
        self.assertEqual(self.session.trace, [("idle", "error")] * 5)
        self.assertEqual(self.session.history, [])
        # the session still works: a normal turn right after the rejects ends done
        self.turn(reply("답"))
        self.assertEqual(self.wait_end(self.start())[-1]["reason"], "done")


class ApprovalGate(TurnCase):
    def setUp(self):
        super().setUp()
        self.cfg["approval_timeout_sec"] = 5  # long enough that only the test's answer ends the wait

    def unlisted(self, command, call_id="call_1"):
        """A shell call that is not on the allow list: the gate has to ask."""
        return tool_call(call_id, "shell_run", {"command": command})

    def test_approve_runs_the_command(self):
        marker = os.path.join(self.root, "approved.txt")
        self.turn(reply(calls=[self.unlisted("touch approved.txt")]), reply("파일을 만들었다"))
        mark = self.start()
        request = self.request(mark)
        self.assertEqual(self.session.state, "waiting")
        self.assertEqual((request["call_id"], request["name"], request["args"], request["timeout_sec"]),
                         ("call_1", "shell_run", {"command": "touch approved.txt"}, 5))
        self.assertFalse(os.path.exists(marker))  # nothing runs before the answer
        self.assertFalse(self.session.approve("call_stale"))  # another call's id opens nothing
        self.assertEqual(self.session.state, "waiting")
        self.assertFalse(os.path.exists(marker))
        self.assertTrue(self.session.approve())
        events = self.wait_end(mark)
        self.assertTrue(os.path.exists(marker))
        self.assertEqual(types(events), ["tool_call", "approval_request", "tool_result",
                                         "assistant", "turn_end"])
        self.assertEqual(events[0]["grade"], "approval")
        self.assertEqual(self.statuses(events), {"call_1": "ok"})
        self.assertEqual(self.session.trace, [
            ("running", "tool_call"), ("waiting", "approval_request"), ("running", "tool_result"),
            ("running", "assistant"), ("idle", "turn_end")])
        self.assertFalse(self.session.approve())  # the gate is shut again

    def test_deny_leaves_the_command_unrun(self):
        marker = os.path.join(self.root, "denied.txt")
        self.turn(reply(calls=[self.unlisted("touch denied.txt")]), reply("거부되었다고 알린다"))
        mark = self.start()
        self.request(mark)
        self.assertFalse(self.session.start_turn("다른 요청"))  # waiting is still a running turn
        self.assertEqual(self.session.state, "waiting")
        self.assertTrue(self.session.deny())
        events = self.wait_end(mark)
        self.assertFalse(os.path.exists(marker))
        self.assertEqual(types(events), ["tool_call", "approval_request", "error", "tool_result",
                                         "assistant", "turn_end"])
        self.assertEqual(self.statuses(events), {"call_1": "denied"})
        # the model is told, so that it can tell the user
        self.assertEqual(self.session.history[2],
                         {"role": "tool", "tool_call_id": "call_1", "content": DENIED})
        self.assertEqual(self.session.history[-1]["content"], "거부되었다고 알린다")

    def test_a_timeout_denies_when_nobody_answers(self):
        self.cfg["approval_timeout_sec"] = 0.1
        marker = os.path.join(self.root, "unanswered.txt")
        self.turn(reply(calls=[self.unlisted("touch unanswered.txt")]), reply("응답이 없어서 하지 않았다"))
        events = self.complete_turn()  # nobody approves or denies
        self.assertFalse(os.path.exists(marker))
        self.assertEqual(of_type(events, "approval_request")[0]["timeout_sec"], 0.1)
        self.assertEqual(of_type(events, "tool_result")[0]["result"], DENIED)
        self.assertEqual(self.statuses(events), {"call_1": "denied"})
        self.assertEqual(self.session.trace, [
            ("running", "tool_call"), ("waiting", "approval_request"), ("running", "tool_result"),
            ("running", "assistant"), ("idle", "turn_end")])
        self.assertFalse(self.session.approve())  # too late: nothing is pending

    def test_a_blocked_command_is_refused_without_asking(self):
        marker = os.path.join(self.root, "keep.txt")
        write(marker, "keep")
        self.turn(reply(calls=[self.unlisted("rm keep.txt")]), reply("정책으로 막혔다"))
        events = self.complete_turn()
        self.assertEqual(types(events), ["tool_call", "tool_result", "assistant", "turn_end"])
        self.assertEqual(events[0]["grade"], "blocked")
        self.assertEqual(of_type(events, "tool_result")[0]["result"], BLOCKED)
        self.assertEqual(self.statuses(events), {"call_1": "blocked"})
        self.assertTrue(os.path.exists(marker))  # rm never ran

    def test_an_unknown_tool_is_refused_without_asking(self):
        self.turn(reply(calls=[tool_call(1, "browser_open", {"url": "http://example.invalid"})]),
                  reply("그 도구는 없다"))
        events = self.complete_turn()
        self.assertEqual(types(events), ["tool_call", "tool_result", "assistant", "turn_end"])
        self.assertEqual(events[0]["grade"], "blocked")
        self.assertEqual(of_type(events, "tool_result")[0]["result"], BLOCKED)

    def test_every_unlisted_call_waits_for_its_own_approval(self):
        first, second = (os.path.join(self.root, "a.txt"), os.path.join(self.root, "b.txt"))
        self.turn(reply(calls=[self.unlisted("touch a.txt", "call_1"),
                               self.unlisted("touch b.txt", "call_2")]), reply("a는 했다"))
        mark = self.start()
        a = self.request(mark)
        self.assertEqual(a["call_id"], "call_1")
        self.assertFalse(os.path.exists(first) or os.path.exists(second))
        self.assertTrue(self.session.approve())
        b = self.request(mark, after=a)
        self.assertEqual(b["call_id"], "call_2")
        self.assertTrue(os.path.exists(first))  # a ran on its approval
        self.assertFalse(os.path.exists(second))  # b is still waiting
        self.assertTrue(self.session.deny())
        events = self.wait_end(mark)
        self.assertEqual(types(events), ["tool_call", "approval_request", "tool_result"] * 2
                         + ["assistant", "turn_end"])
        self.assertEqual(self.statuses(events), {"call_1": "ok", "call_2": "denied"})
        self.assertFalse(os.path.exists(second))


class GlobalSession(TurnCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, agent, "_SESSION", agent._SESSION)
        agent._SESSION = None

    def test_one_shared_session_until_reset(self):
        first = agent.get_session()
        self.assertIs(agent.get_session(), first)
        second = agent.reset_session()
        self.assertIsNot(second, first)
        self.assertIs(agent.get_session(), second)
        self.assertEqual((second.state, second.history, second.events), ("idle", [], []))

    def test_reset_denies_the_pending_approval_of_the_old_session(self):
        self.cfg["approval_timeout_sec"] = 5
        marker = os.path.join(self.root, "gone.txt")
        self.turn(reply(calls=[tool_call(1, "shell_run", {"command": "touch gone.txt"})]),
                  reply("취소되었다"))
        agent._SESSION = self.session
        mark = self.start()
        self.request(mark)
        fresh = agent.reset_session()
        events = self.wait_end(mark)
        self.assertIsNot(fresh, self.session)
        self.assertEqual(self.statuses(events), {"call_1": "denied"})  # the old turn ends on its own
        self.assertFalse(os.path.exists(marker))
        self.assertEqual((fresh.state, fresh.history, fresh.events), ("idle", [], []))


# --- tools on a temp file root -------------------------------------------------


class FileTools(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="swiv-agent-files-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "docs"))
        write(os.path.join(self.root, "docs", "a.md"), "# hi\n")
        os.symlink("/etc", os.path.join(self.root, "escape"))
        self.cfg = agent_cfg()
        patcher = mock.patch.object(agent, "FILES_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tool(self, name, arguments):
        return agent.run_tool({"name": name, "arguments": arguments}, self.cfg)

    def test_files_inside_the_root_are_read(self):
        self.assertEqual(self.tool("files_read", {"path": "docs/a.md"}), "# hi\n")
        self.assertEqual(self.tool("files_read", {"path": "docs/../docs/a.md"}), "# hi\n")
        self.assertEqual(self.tool("files_read", {"path": "a.md"}), "error: 파일이 없거나 파일이 아니다")
        self.assertEqual(self.tool("files_read", {}), "error: path가 필요하다")

    def test_paths_outside_the_root_are_blocked(self):
        for path in ("../etc/passwd", "docs/../../etc/passwd", "docs/../../../../etc/shadow",
                     "/etc/passwd"):
            self.assertEqual(self.tool("files_read", {"path": path}), OUTSIDE, path)
        self.assertEqual(self.tool("files_list", {"path": "../"}), OUTSIDE)

    def test_a_symlink_out_of_the_root_is_blocked(self):
        self.assertEqual(self.tool("files_read", {"path": "escape/passwd"}), OUTSIDE)
        self.assertEqual(self.tool("files_list", {"path": "escape"}), OUTSIDE)

    def test_a_shell_cwd_outside_the_root_is_blocked(self):
        for cwd in ("..", "../..", "/etc", "escape"):
            self.assertEqual(self.tool("shell_run", {"command": "echo hi", "cwd": cwd}), OUTSIDE, cwd)
        self.assertEqual(self.tool("shell_run", {"command": "echo hi", "cwd": "docs"}), "hi\n[exit 0]")


class ShellTool(FileTools):
    def test_output_and_exit_status(self):
        self.assertEqual(self.tool("shell_run", {"command": "echo hi"}), "hi\n[exit 0]")
        self.assertEqual(self.tool("shell_run", {"command": "echo err >&2; exit 3"}), "err\n[exit 3]")
        for command in ("", "   ", 42, None):
            self.assertEqual(self.tool("shell_run", {"command": command}), "error: command가 비어 있다",
                             repr(command))

    def test_long_output_keeps_the_head_and_the_tail(self):
        out = self.tool("shell_run", {"command": "seq 1 5000"})
        self.assertTrue(out.startswith("1\n2\n3\n"), out[:20])
        self.assertTrue(out.endswith("4999\n5000\n[exit 0]"), out[-30:])
        self.assertLessEqual(len(out), agent.OUTPUT_CHARS + 60)
        self.assertIn("중간", out)

    def test_a_hanging_command_is_killed_at_the_timeout(self):
        self.cfg["shell_timeout_sec"] = 1
        started = time.monotonic()
        out = self.tool("shell_run", {"command": "echo started; sleep 30"})
        self.assertEqual(out, "started\n[timeout after 1s]")
        self.assertLess(time.monotonic() - started, 20)  # killed, not waited out


# --- the /agent/* endpoints -----------------------------------------------------

class AgentEndpoints(unittest.TestCase):
    """/agent/* over real HTTP: a ThreadingHTTPServer on an ephemeral port,
    authed by a session token planted in server.SESSIONS (no pam involved)."""

    @staticmethod
    def cfg(enabled):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["agent"]["enabled"] = enabled
        return cfg

    def setUp(self):
        agent.reset_session()
        token = secrets.token_urlsafe(16)
        server.SESSIONS[token] = time.time() + 60
        self.cookie = "swiv=" + token
        self._quiet = mock.patch.object(server.H, "log_message", lambda *a, **k: None)
        self._quiet.start()
        self.addCleanup(self._quiet.stop)
        self.addCleanup(lambda: server.SESSIONS.pop(token, None))
        self.addCleanup(agent.reset_session)

    def serve(self, enabled=True):
        old = server.H.cfg
        server.H.cfg = self.cfg(enabled)
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        httpd.daemon_threads = True
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)
        self.addCleanup(lambda: setattr(server.H, "cfg", old))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return "127.0.0.1", httpd.server_address[1]

    def request(self, addr, method, path, body=None):
        conn = http.client.HTTPConnection(addr[0], addr[1], timeout=10)
        with contextlib.closing(conn):
            conn.request(method, path, json.dumps(body) if body is not None else None,
                         {"Cookie": self.cookie})
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, json.loads(data) if data[:1] in (b"{", b"[") else data.decode()

    def test_disabled_hides_every_agent_route(self):
        addr = self.serve(enabled=False)
        for method, path in (("GET", "/agent/poll"), ("GET", "/agent/session"),
                             ("POST", "/agent/chat"), ("POST", "/agent/approve"),
                             ("POST", "/agent/cancel"), ("POST", "/agent/stop")):
            with self.subTest(path=path):
                status, body = self.request(addr, method, path, {} if method == "POST" else None)
                self.assertEqual((status, body), (404, "agent card disabled"))
        status, cards_json = self.request(addr, "GET", "/cards")
        self.assertEqual(status, 200)
        self.assertNotIn("agent", [c["kind"] for c in cards_json])

    def test_enabled_serves_the_session_view(self):
        addr = self.serve(enabled=True)
        status, view = self.request(addr, "GET", "/agent/poll")
        self.assertEqual((status, view), (200, {"state": "idle", "idx": 0, "events": []}))
        status, view = self.request(addr, "GET", "/agent/session")
        self.assertEqual(status, 200)
        self.assertEqual((view["state"], view["idx"], view["history"]), ("idle", 0, []))
        status, cards_json = self.request(addr, "GET", "/cards")
        kinds = [c["kind"] for c in cards_json]
        self.assertIn("agent", kinds)  # ahead of the file card, desk may lead
        self.assertLess(kinds.index("agent"), kinds.index("files"))

    def test_chat_refuses_blank_text_and_reports_the_reason(self):
        addr = self.serve(enabled=True)
        status, answer = self.request(addr, "POST", "/agent/chat", {"text": "   "})
        self.assertEqual(status, 200)
        self.assertFalse(answer["ok"])
        self.assertIn("빈", answer["error"])

    def test_a_turn_flows_through_poll_events_and_history(self):
        addr = self.serve(enabled=True)
        with mock.patch.object(agent, "llm_chat", lambda *a, **k: reply("서버 응답")):
            status, answer = self.request(addr, "POST", "/agent/chat", {"text": "안녕하세요"})
            self.assertEqual((status, answer), (200, {"ok": True}))
            deadline = time.monotonic() + 10
            view = None
            while time.monotonic() < deadline:
                _, view = self.request(addr, "GET", "/agent/poll")
                if view["state"] == "idle" and view["events"]:
                    break
                time.sleep(0.01)
            types_ = [e["type"] for e in view["events"]]
            self.assertEqual(types_, ["assistant", "turn_end"])
            self.assertEqual(view["events"][0]["text"], "서버 응답")
            _, session_view = self.request(addr, "GET", "/agent/session")
            last = session_view["history"][-1]
            self.assertEqual((last["role"], last["content"], last["n"]), ("assistant", "서버 응답", 0))
            # the answer comes as a tree to build, never as HTML to parse (mdview)
            self.assertEqual(last["blocks"], [{"i": 0, "tree": [{"t": "p", "c": ["서버 응답"]}]}])
            self.assertEqual((view["events"][0]["n"], view["events"][0]["blocks"]),
                             (0, last["blocks"]))
            # polling from the last idx skips what was already seen
            _, tail = self.request(addr, "GET", "/agent/poll?since=%d" % view["idx"])
            self.assertEqual(tail["events"], [])

    def test_approve_cancel_and_stop_answer_false_with_nothing_pending(self):
        addr = self.serve(enabled=True)
        for path in ("/agent/approve", "/agent/cancel", "/agent/stop"):
            with self.subTest(path=path):
                status, answer = self.request(addr, "POST", path, {})
                self.assertEqual((status, answer), (200, {"ok": False}))

    def test_a_running_turn_is_held_by_the_poll_until_it_answers(self):
        addr = self.serve(enabled=True)
        with mock.patch.object(agent, "llm_chat", lambda *a, **k: reply("지금 도착")):
            threading.Timer(0.4, lambda: self.request(addr, "POST", "/agent/chat", {"text": "질문"})).start()
            t0 = time.monotonic()
            status, view = self.request(addr, "GET", "/agent/poll?since=0&wait=10")
            took = time.monotonic() - t0
        self.assertEqual(status, 200)
        self.assertTrue(0.3 < took < 8, took)  # held, and let go by the answer, not by the timeout
        self.assertEqual([e["type"] for e in view["events"]], ["assistant", "turn_end"])
        self.assertEqual((view["events"][0]["n"], len(view["events"][0]["blocks"])), (0, 1))

    def test_a_bad_wait_is_ignored(self):
        addr = self.serve(enabled=True)
        status, view = self.request(addr, "GET", "/agent/poll?since=0&wait=soon&full=maybe")
        self.assertEqual((status, view), (200, {"state": "idle", "idx": 0, "events": []}))



# --- streaming -------------------------------------------------------------------


def event(content=None, calls=None, finish=None):
    """One server-sent chunk of a streamed completion."""
    delta = {}
    if content is not None:
        delta["content"] = content
    if calls is not None:
        delta["tool_calls"] = calls
    return {"choices": [{"delta": delta, "finish_reason": finish}]}


class FakeStream(io.BytesIO):
    """What opener.open() returns for a completion: the pieces as server-sent
    events (a dict is one event, bytes go in as they are)."""

    def __init__(self, pieces, ctype="text/event-stream"):
        body = b""
        for piece in pieces:
            body += piece if isinstance(piece, bytes) else b"data: " + json.dumps(piece).encode() + b"\n\n"
        super().__init__(body)
        self.headers = {"Content-Type": ctype}


class BrokenStream(FakeStream):
    def __iter__(self):
        yield b"data: " + json.dumps(event("first")).encode() + b"\n\n"
        raise OSError("connection reset")


class FakeOpener:
    def __init__(self, response):
        self.response, self.requests = response, []

    def open(self, request, timeout=None):
        self.requests.append(request)
        return self.response


class StreamingLLM:
    """Stands in for a model that streams: hands the pieces to on_text, then
    answers with the whole text."""

    def __init__(self, pieces):
        self.pieces, self.calls = pieces, 0

    def __call__(self, cfg_agent, messages, tools, on_text=None):
        self.calls += 1
        for piece in self.pieces:
            on_text(piece)
        return {"text": "".join(self.pieces), "tool_calls": []}


class StreamReading(unittest.TestCase):
    CFG = agent_cfg(base_url="https://llm.example/v1", model="m", api_key="sk-test-secret-000")
    ASK = [{"role": "user", "content": "질문"}]

    def chat(self, pieces, on_text="collect", ctype="text/event-stream"):
        seen = []
        opener = FakeOpener(FakeStream(pieces, ctype))
        with mock.patch.object(agent.urllib.request, "build_opener", return_value=opener):
            result = agent.llm_chat(self.CFG, self.ASK, {},
                                    on_text=seen.append if on_text == "collect" else on_text)
        return result, "".join(seen), opener

    def test_the_text_is_handed_over_as_it_arrives(self):
        result, seen, opener = self.chat([event("안녕"), event("하세요"), event(finish="stop"), b"data: [DONE]\n\n"])
        self.assertEqual((result["text"], result["tool_calls"]), ("안녕하세요", []))
        self.assertEqual(seen, "안녕하세요")
        self.assertIs(json.loads(opener.requests[0].data)["stream"], True)

    def test_a_tool_call_arrives_in_pieces_and_is_joined(self):
        pieces = [event(calls=[{"index": 0, "id": "call_1", "function": {"name": "files_", "arguments": ""}}]),
                  event(calls=[{"index": 1, "id": "call_2", "function": {"name": "shell_run", "arguments": '{"comm'}}]),
                  event(calls=[{"index": 0, "function": {"name": "list", "arguments": '{"path": ""}'}}]),
                  event(calls=[{"index": 1, "function": {"arguments": 'and": "ls"}'}}]),
                  b"data: [DONE]\n\n", event("too late")]
        result, seen, _ = self.chat(pieces)
        self.assertEqual(result["tool_calls"], [
            {"id": "call_1", "name": "files_list", "arguments": {"path": ""}},
            {"id": "call_2", "name": "shell_run", "arguments": {"command": "ls"}}])
        self.assertEqual((result["text"], seen), ("", ""))

    def test_noise_around_the_events_is_ignored(self):
        pieces = [b": keep-alive\n", b"\n", b"event: message\n", b"data: {not json\n\n",
                  {"choices": [], "usage": {"total_tokens": 3}},
                  event([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])]
        result, seen, _ = self.chat(pieces)
        self.assertEqual((result["text"], seen), ("ab", "ab"))

    def test_an_error_chunk_names_no_key(self):
        with self.assertRaises(agent.LLMError) as caught:
            self.chat([event("a"), {"error": {"message": "bad key sk-test-secret-000", "code": 401}}])
        self.assertIn("bad key", str(caught.exception))
        self.assertNotIn("sk-test-secret-000", str(caught.exception))

    def test_a_stream_that_breaks_is_not_retried(self):
        opener = FakeOpener(BrokenStream([]))
        with mock.patch.object(agent.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(agent.LLMError) as caught:
                agent.llm_chat(self.CFG, self.ASK, {}, on_text=lambda part: None)
        self.assertIn("끊", str(caught.exception))
        self.assertEqual(len(opener.requests), 1)  # the phone has part of the text already

    def test_a_provider_that_answers_whole_is_read_whole(self):
        whole = json.dumps({"choices": [{"message": {"content": "한 번에", "tool_calls": []}}]}).encode()
        result, seen, _ = self.chat([whole], ctype="application/json")
        self.assertEqual((result["text"], seen), ("한 번에", ""))

    def test_without_a_callback_nothing_is_streamed_and_the_key_stays_in_the_header(self):
        whole = json.dumps({"choices": [{"message": {"content": "한 번에", "tool_calls": []}}]}).encode()
        opener = FakeOpener(FakeStream([whole], ctype="application/json"))
        with mock.patch.object(agent.urllib.request, "build_opener", return_value=opener):
            result = agent.llm_chat(self.CFG, self.ASK, {})
        self.assertEqual(result["text"], "한 번에")
        request = opener.requests[0]
        self.assertIs(json.loads(request.data)["stream"], False)
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-test-secret-000")


class StreamingAnswer(TurnCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(agent, "STREAM_TICK", 0)  # every piece is rendered at once
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_blocks_settle_as_the_text_arrives(self):
        s = self.session
        s.feed_text("첫 문단입니다.\n\n- 하나\n")
        self.assertEqual([(b["n"], b["i"], b["final"]) for b in s.md_changes()],
                         [(0, 0, True), (0, 1, False)])
        self.assertEqual(s.md_changes(), [])  # nothing changed since
        s.feed_text("- 둘\n")
        self.assertEqual([(b["i"], b["final"]) for b in s.md_changes()], [(1, False)])  # one open list still
        s.feed_text("\n")
        self.assertEqual([(b["i"], b["final"]) for b in s.md_changes()], [(1, True)])  # the blank line settles it
        self.assertEqual(s.draft_text(), "첫 문단입니다.\n\n- 하나\n- 둘\n\n")

    def test_a_line_that_ends_on_a_newline_does_not_split_a_paragraph(self):
        s = self.session
        s.feed_text("첫 줄\n")
        self.assertEqual([(b["i"], b["final"]) for b in s.md_changes()], [(0, False)])
        s.feed_text("이어지는 줄")
        self.assertEqual([(b["i"], b["final"]) for b in s.md_changes(full=True)], [(0, False)])
        self.assertEqual(len(s.md_changes(full=True)), 1)  # one paragraph, not two

    def test_a_turn_streams_and_ends_with_every_block_final(self):
        pieces = ["## 제목\n\n", "본문 `code`입니다.\n\n", "```bash\nmake install\n"]
        with mock.patch.object(agent, "llm_chat", StreamingLLM(pieces)):
            events = self.wait_end(self.start())
        self.assertEqual(types(events), ["assistant", "turn_end"])
        self.assertEqual(events[0]["text"], "".join(pieces))
        view = self.session.events_view(0)[0]
        self.assertEqual((view["n"], len(view["blocks"])), (0, 3))
        self.assertEqual([b["tree"][0].get("t") for b in view["blocks"]], ["h2", "p", "pre"])
        self.assertEqual(view["blocks"][2]["tree"][0]["lang"], "bash")
        last = self.session.history_view(50)[-1]
        self.assertEqual((last["n"], last["blocks"]), (0, view["blocks"]))
        self.assertEqual(self.session.md_changes(), [])  # the draft is gone once the text is whole

    def test_each_answer_has_its_own_n(self):
        with mock.patch.object(agent, "llm_chat", StreamingLLM(["첫 답"])):
            self.wait_end(self.start())
        with mock.patch.object(agent, "llm_chat", StreamingLLM(["둘째 답"])):
            events = self.wait_end(self.start("이어서"))
        self.assertEqual(events[0]["n"], 1)
        self.assertEqual([m["n"] for m in self.session.history_view(50) if "n" in m], [0, 1])


class Stopping(TurnCase):
    def test_stop_while_the_text_streams_keeps_what_arrived(self):
        session = self.session

        def model(cfg_agent, messages, tools, on_text=None):
            on_text("여기까지 도착한 ")
            self.assertTrue(session.stop())
            on_text("텍스트")  # refused: the reading stops here
            self.fail("the reading went on after the stop")

        with mock.patch.object(agent, "llm_chat", model):
            events = self.wait_end(self.start())
        self.assertEqual(types(events), ["assistant", "turn_end"])
        self.assertEqual(events[0]["text"], "여기까지 도착한 ")
        self.assertEqual(events[-1]["reason"], "stopped")
        self.assertEqual(self.session.state, "idle")
        self.assertFalse(session.stop())  # nothing runs
        # not stuck: the next turn is a normal one
        self.turn(reply("다시 답한다"))
        self.assertEqual(self.complete_turn(text="다시")[-1]["reason"], "done")
        self.assertFalse(session.is_stopped())

    def test_stop_refuses_a_pending_approval_and_skips_what_follows(self):
        never = os.path.join(self.root, "never.txt")

        def model(cfg_agent, messages, tools, on_text=None):
            return {"text": "", "tool_calls": [
                {"id": "call_1", "name": "shell_run", "arguments": {"command": "touch never.txt"}},
                {"id": "call_2", "name": "files_read", "arguments": {"path": "note.txt"}}]}

        with mock.patch.object(agent, "llm_chat", model):
            mark = self.start()
            self.request(mark)
            self.assertTrue(self.session.stop())
            events = self.wait_end(mark)
        self.assertFalse(os.path.exists(never))
        self.assertEqual(self.statuses(events), {"call_1": "denied", "call_2": "stopped"})
        self.assertEqual(events[-1]["reason"], "stopped")
        self.assertEqual([m["content"] for m in self.session.history if m.get("role") == "tool"],
                         ["denied by user or timeout", "stopped by user"])


class LongPoll(unittest.TestCase):
    def later(self, delay, action):
        timer = threading.Timer(delay, action)
        timer.start()
        self.addCleanup(timer.cancel)

    def test_it_returns_as_soon_as_an_event_lands(self):
        s = agent.AgentSession()
        self.later(0.3, lambda: s.emit("assistant", text="x", n=0))
        t0 = time.monotonic()
        s.wait_events(0, 10)
        self.assertLess(time.monotonic() - t0, 5)

    def test_an_event_that_is_already_there_does_not_wait(self):
        s = agent.AgentSession()
        s.emit("error", message="e")
        t0 = time.monotonic()
        s.wait_events(0, 10)
        self.assertLess(time.monotonic() - t0, 2)

    def test_nothing_new_waits_out_the_timeout(self):
        s = agent.AgentSession()
        s.emit("error", message="e")
        t0 = time.monotonic()
        s.wait_events(1, 0.3)
        self.assertGreaterEqual(time.monotonic() - t0, 0.25)

    def test_a_rendered_block_wakes_it_too(self):
        with mock.patch.object(agent, "STREAM_TICK", 0):
            s = agent.AgentSession()
            self.later(0.3, lambda: s.feed_text("스트리밍 중인 글"))
            t0 = time.monotonic()
            s.wait_events(0, 10)
            self.assertLess(time.monotonic() - t0, 5)
        self.assertEqual([(b["n"], b["i"], b["final"]) for b in s.md_changes()], [(0, 0, False)])


if __name__ == "__main__":
    unittest.main()
