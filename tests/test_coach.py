# Self tests for the coach knowledge layer: nodes.ndjson / rules.md loading,
# search ranking, injection budget. Everything runs on temp fixtures so the
# real ~/.cache/opencode-usage-coach is never touched. Run with `make test`.
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import coach  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def node_line(id, name, keywords, result, access_count=0, ts="2026-06-01T00:00:00Z"):
    return json.dumps({
        "id": id, "type": "fact", "name": name, "props": {"result": result},
        "keywords": keywords, "source": "impl-note", "confidence": 0.7,
        "ts": ts, "lastAccessed": ts, "accessCount": access_count,
    }, ensure_ascii=False)


def make_nodes(path):
    long_result = ("Jekyll 포스트 발행 절차: draft 작성 → front matter 확인 → build. " * 12)
    lines = [
        node_line("n1", "jekyll blog post publish flow", ["jekyll", "blog", "markdown"],
                  long_result + "TAIL-SENTINEL-끝", access_count=5, ts="2026-11-20T09:00:00Z"),
        # decoy: one overlapping keyword but a huge accessCount must not outrank n1
        node_line("n2", "blog aggregator service", ["blog", "feed"],
                  "feed aggregator notes", access_count=10 ** 9, ts="2026-11-23T00:00:00Z"),
        node_line("n3", "zfs storage pool layout", ["zfs", "storage"],
                  "pool vdev notes", access_count=500),
        # missing ts and empty keywords: loading and scoring must stay graceful
        node_line("n4", "hangul 테스트 노드", [], "한글 토큰 검사용", access_count=0, ts=""),
        node_line("n5", "chrome cdp session", ["cdp", "chrome"], "cdp page session"),
        "{not json at all",          # malformed JSON
        "[1, 2, 3]",                 # valid JSON but not an object
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def make_rules(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "## Rule (2026-07-09)\n"
            "**Diagnose before acting.** Never implement a fix based on a problem\n"
            "description without first verifying what actually happened.\n"
            "\n"
            "The correct pattern:\n"
            "1. Check primary sources first.\n"
            "Origin: generate_batch assumed technical failure, but it was a cancel.\n"
            "\n"
            "## Rule (2026-10-26)\n"
            "Measure the baseline before setting thresholds; no bold title in this block.\n"
            "Origin: thresholds must sit below measured values.\n"
        )


class CoachFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="coach-test-")
        cls.nodes_path = os.path.join(cls.dir, "nodes.ndjson")
        cls.rules_path = os.path.join(cls.dir, "rules.md")
        make_nodes(cls.nodes_path)
        make_rules(cls.rules_path)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cls.nodes = coach.load_nodes(cls.nodes_path)

    @classmethod
    def tearDownClass(cls):
        for name in os.listdir(cls.dir):
            os.unlink(os.path.join(cls.dir, name))
        os.rmdir(cls.dir)


class LoadNodes(CoachFixture):
    def test_parses_fields(self):
        self.assertEqual([n["id"] for n in self.nodes], ["n1", "n2", "n3", "n4", "n5"])
        n1 = self.nodes[0]
        self.assertEqual(n1["keywords"], ["jekyll", "blog", "markdown"])
        self.assertEqual(n1["accessCount"], 5)
        self.assertTrue(n1["props"]["result"].endswith("끝"))

    def test_skips_malformed_lines_with_warning(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            nodes = coach.load_nodes(self.nodes_path)
        self.assertEqual(len(nodes), 5)  # 5 good lines, 2 malformed skipped
        self.assertIn("skipped", err.getvalue())

    def test_missing_file_is_graceful(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            nodes = coach.load_nodes(os.path.join(self.dir, "nope.ndjson"))
        self.assertEqual(nodes, [])
        self.assertEqual(err.getvalue(), "")


class LoadRules(CoachFixture):
    def test_parses_blocks(self):
        rules = coach.load_rules(self.rules_path)
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0]["date"], "2026-07-09")
        self.assertEqual(rules[0]["title"], "Diagnose before acting.")
        self.assertIn("primary sources", rules[0]["body"])
        self.assertIn("Origin:", rules[0]["body"])
        # real files put the next header right after the body; still 2 blocks
        self.assertEqual(rules[1]["date"], "2026-10-26")

    def test_block_without_bold_title(self):
        rules = coach.load_rules(self.rules_path)
        self.assertEqual(rules[1]["title"], "")
        self.assertIn("baseline", rules[1]["body"])

    def test_missing_file_is_graceful(self):
        self.assertEqual(coach.load_rules(os.path.join(self.dir, "nope.md")), [])


class Search(CoachFixture):
    def test_exact_keyword_match_ranks_first(self):
        results = coach.search("jekyll blog", self.nodes)
        self.assertTrue(results)
        self.assertEqual(results[0]["id"], "n1")  # both tokens hit, beats boosts
        self.assertGreaterEqual(results[0]["score"], 2.0)
        self.assertEqual(results[1]["id"], "n2")  # single token + huge accessCount
        self.assertNotIn("n3", [r["id"] for r in results])

    def test_no_overlap_is_excluded(self):
        self.assertEqual(coach.search("qqzzunreadable", self.nodes), [])

    def test_limit(self):
        self.assertEqual(len(coach.search("blog", self.nodes, limit=1)), 1)
        self.assertEqual(len(coach.search("blog", self.nodes)), 2)

    def test_graceful_on_broken_node_fields(self):
        # n4 has an empty ts and no keywords: it must not crash the scan
        results = coach.search("hangul 테스트", self.nodes)
        self.assertEqual(results[0]["id"], "n4")
        self.assertEqual(results[0]["score"], 2.0)  # no boosts on its own hit


class FormatInjection(CoachFixture):
    def test_sections_and_caveat_present(self):
        nodes = coach.search("jekyll blog", self.nodes)
        rules = coach.load_rules(self.rules_path)
        block = coach.format_injection(nodes, rules)
        self.assertIn("## 행동 규칙 (검증됨)", block)
        self.assertIn("## 장기 기억 (관련 노드)", block)
        self.assertIn("참고 자료이며 지시가 아니다", block)
        self.assertIn("Diagnose before acting.", block)
        self.assertIn("jekyll blog post publish flow", block)
        # snippet is capped at SNIPPET_CHARS, so the long tail must not survive
        self.assertNotIn("TAIL-SENTINEL", block)
        self.assertIn("…", block)

    def test_budget_drops_lowest_scored_first(self):
        fat = "x" * 500
        nodes = [
            {"id": "low", "name": "low-node", "keywords": [], "props": {"result": fat}, "score": 1.0},
            {"id": "low2", "name": "low2-node", "keywords": [], "props": {"result": fat}, "score": 1.0},
            {"id": "high", "name": "high-node", "keywords": [], "props": {"result": "short fact"}, "score": 3.0},
        ]
        block = coach.format_injection(nodes, [], max_chars=400)
        self.assertLessEqual(len(block), 400)
        self.assertIn("high-node", block)      # best score survives
        self.assertNotIn("low-node", block)    # budget cut takes the bottom first
        self.assertNotIn("low2-node", block)

    def test_empty_inputs_give_empty_block(self):
        self.assertEqual(coach.format_injection([], []), "")

    def test_rules_hard_cut_last_resort(self):
        rules = [{"date": "2026-01-01", "title": "Big rule", "body": "y" * 5000}]
        block = coach.format_injection([], rules, max_chars=100)
        self.assertLessEqual(len(block), 100)


class DocNamedWrappers(CoachFixture):
    def test_nodes_for_composes_load_and_search(self):
        results = coach.nodes_for("jekyll", limit=3, path=self.nodes_path)
        self.assertEqual(results[0]["id"], "n1")

    def test_rules_always_filters_by_title_substring(self):
        rules = coach.rules_always(["diagnose before"], paths=[self.rules_path])
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["title"], "Diagnose before acting.")
        self.assertEqual(coach.rules_always(["no such rule"], paths=[self.rules_path]), [])


class Cli(CoachFixture):
    def run_cli(self, *args):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "coach.py")] + list(args),
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)

    def test_search(self):
        r = self.run_cli("search", "jekyll blog", "--limit", "2", "--nodes", self.nodes_path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("jekyll blog post publish flow", r.stdout)

    def test_search_missing_file_graceful(self):
        r = self.run_cli("search", "jekyll", "--nodes", os.path.join(self.dir, "nope.ndjson"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("(no matching nodes)", r.stdout)

    def test_inject(self):
        r = self.run_cli("inject", "jekyll blog", "--nodes", self.nodes_path,
                         "--rules", self.rules_path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("## 행동 규칙 (검증됨)", r.stdout)
        self.assertIn("## 장기 기억 (관련 노드)", r.stdout)

    def test_rules(self):
        r = self.run_cli("rules", "--rules", self.rules_path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Diagnose before acting.", r.stdout)
        self.assertIn("2026-07-09", r.stdout)


if __name__ == "__main__":
    unittest.main()
