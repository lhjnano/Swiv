import copy
import unittest

import mdview


def tags(node):
    """Every tag of a tree, in document order."""
    if isinstance(node, str):
        return []
    return ([node["t"]] if "t" in node else []) + [t for child in node["c"] for t in tags(child)]


def text(node):
    if isinstance(node, str):
        return node
    return "".join(text(child) for child in node["c"])


class Blocks(unittest.TestCase):
    def test_a_blank_line_settles_what_came_before(self):
        parts, defs = mdview.blocks("첫 문단입니다.\n\n- 하나\n- 둘")
        self.assertEqual(parts, [("첫 문단입니다.", True, None), ("- 하나\n- 둘", False, None)])
        self.assertEqual(defs, [])

    def test_the_last_block_stays_open_until_the_text_ends_on_a_blank_line(self):
        self.assertEqual(mdview.blocks("문단\n")[0], [("문단", False, None)])
        self.assertEqual(mdview.blocks("문단\n\n")[0], [("문단", True, None)])
        self.assertEqual(mdview.blocks("")[0], [])

    def test_a_closed_fence_is_settled_and_an_open_one_is_not(self):
        parts, _ = mdview.blocks("```bash\nmake deps\n```\n\n```python\nprint(")
        self.assertEqual(parts, [("```bash\nmake deps\n```", True, "bash"),
                                 ("```python\nprint(", False, "python")])

    def test_a_blank_line_inside_a_fence_does_not_split(self):
        parts, _ = mdview.blocks("```\na\n\nb\n```")
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0][1:], (True, None))

    def test_blank_lines_between_the_items_of_a_list_do_not_split_it(self):
        parts, _ = mdview.blocks("- 하나\n\n- 둘\n\n문단")
        self.assertEqual([p[0] for p in parts], ["- 하나\n\n- 둘", "문단"])
        self.assertEqual([p[1] for p in parts], [True, False])

    def test_a_shorter_or_other_closing_marker_does_not_close_a_fence(self):
        parts, _ = mdview.blocks("````\n```\n````\n")
        self.assertEqual(len(parts), 1)
        self.assertTrue(parts[0][1])
        parts, _ = mdview.blocks("```\n~~~\n")
        self.assertEqual(len(parts), 1)
        self.assertFalse(parts[0][1])  # still open: the model may write on

    def test_link_definitions_come_apart(self):
        parts, defs = mdview.blocks("[a]: https://example.com/a\n\n글 [링크][a]\n\n[b]: https://example.com/b")
        self.assertEqual(defs, ["[a]: https://example.com/a", "[b]: https://example.com/b"])
        self.assertEqual([p[0] for p in parts], ["글 [링크][a]"])

    def test_leading_and_trailing_blank_lines_make_no_blocks(self):
        parts, _ = mdview.blocks("\n\n문단\n\n\n")
        self.assertEqual(parts, [("문단", True, None)])


class Message(unittest.TestCase):
    def test_the_parts_of_an_answer(self):
        out = mdview.message("## 제목\n\n본문 `code`와 **굵게**.\n\n```bash\nmake install\n```\n\n"
                             "| a | b |\n|---|---|\n| 1 | 2 |", final=True)
        self.assertEqual([(i, final) for i, _, final in out], [(0, True), (1, True), (2, True), (3, True)])
        self.assertEqual(tags({"c": out[0][1]}), ["h2"])
        self.assertEqual(tags({"c": out[1][1]}), ["p", "code", "strong"])
        self.assertEqual([n.get("lang") for n in out[2][1]], ["bash"])
        self.assertEqual(tags({"c": out[2][1]})[:3], ["pre", "span", "code"])  # highlighted once final
        self.assertTrue({"table", "thead", "tbody", "th", "td"} <= set(tags({"c": out[3][1]})))

    def test_the_open_block_is_plain_and_the_settled_ones_highlighted(self):
        source = "```python\nprint(1)\n```\n\n```bash\necho hi"
        out = mdview.message(source, final=False)
        closed, open_ = out[0][1], out[1][1]
        self.assertEqual((out[0][2], out[1][2]), (True, False))
        self.assertEqual(tags({"c": closed})[:4], ["pre", "span", "code", "span"])  # Pygments spans
        self.assertEqual(tags({"c": open_}), ["pre", "code"])  # plain while it is written
        self.assertEqual(text({"c": open_}), "echo hi\n")  # the open fence reads as code already

    def test_ending_the_text_makes_the_open_block_final(self):
        out = mdview.message("- 하나\n- 둘", final=True)
        self.assertEqual([(i, final) for i, _, final in out], [(0, True)])

    def test_a_link_definition_reaches_the_block_that_uses_it(self):
        out = mdview.message("[a]: https://example.com/x\n\n[링크][a]")
        link = [n for n in tags_to_nodes(out[-1][1]) if n["t"] == "a"]
        self.assertEqual(link[0].get("a"), {"href": "https://example.com/x"})

    def test_the_same_block_is_rendered_once(self):
        first = mdview.message("**같은** 문단", final=True)[0][1]
        second = mdview.message("**같은** 문단", final=True)[0][1]
        self.assertIs(first, second)


def tags_to_nodes(nodes):
    for node in nodes:
        if isinstance(node, dict):
            yield node
            yield from tags_to_nodes(node["c"])


class Safety(unittest.TestCase):
    """What a model or a tool writes is data: no tag, URL or attribute of it
    reaches the phone unless it is on the list."""

    EVIL = ('<script>alert(1)</script>\n\n<img src=x onerror=alert(2)>\n\n'
            '[x](javascript:alert(3)) [y](https://ok.example/a?b=1) <a href="data:text/html,x">z</a> '
            '<a href="HTTPS://ok.example/c" onclick="x()" style="color:red">w</a>\n\n'
            '<div onclick="alert(4)">d</div> <iframe src="//evil.example"></iframe>\n\n'
            '<style>body{display:none}</style> tail <b onmouseover="x()">bold?</b>\n\n'
            '<form action="//evil.example"><input name=p></form> <object data="//evil.example"></object>')

    def test_raw_html_in_prose_is_dropped_with_its_subtree(self):
        out = mdview.message(self.EVIL, final=True)
        rendered = [n for _, tree, _ in out for n in tags_to_nodes(tree)]
        self.assertEqual(sorted({n["t"] for n in rendered}), ["a", "p"])
        flat = " ".join(text({"c": tree}) for _, tree, _ in out)
        self.assertNotIn("alert(", flat)  # nothing executable survives as text either
        self.assertNotIn("<", flat)
        self.assertIn("x y z w", flat)
        self.assertIn("tail", flat)

    def test_only_http_links_survive_without_their_other_attributes(self):
        out = mdview.message(self.EVIL, final=True)
        links = [n for _, tree, _ in out for n in tags_to_nodes(tree) if n["t"] == "a"]
        # an unsafe or unknown-scheme link keeps its text, as a node with no attributes
        self.assertEqual([n.get("a") for n in links],
                         [None, {"href": "https://ok.example/a?b=1"}, None, {"href": "HTTPS://ok.example/c"}])

    def test_code_shows_markup_as_text(self):
        for i, tree, _ in mdview.message("```html\n<script>alert(5)</script>\n```", final=True):
            self.assertEqual(text({"c": tree}), "<script>alert(5)</script>\n")

    def test_the_tree_is_json_and_deep_copies_stay_equal(self):
        out = mdview.message("- 하나\n\n> 인용 `x` [링크](https://example.com/)", final=True)
        self.assertEqual(copy.deepcopy(out), out)


if __name__ == "__main__":
    unittest.main()
