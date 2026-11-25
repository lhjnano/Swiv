# markdown for the agent chat card (docs/agent-card.md): a message is rendered
# block by block into a tree of a few allowed nodes. The phone builds that
# tree with textContent, so model and tool output never reach an HTML parser
# there. Blocks that can no longer change are rendered once, with code
# highlighting, and cached; the block still being streamed is re-rendered on
# every poll, plain. Markdown and Pygments are what the file card already uses.
import re
from html.parser import HTMLParser

import markdown

PLAIN = ["fenced_code", "tables", "def_list", "sane_lists"]
FINAL = PLAIN + ["codehilite"]
CONFIG = {"codehilite": {"guess_lang": False}}
CACHE_MAX = 2000
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
OPENER = re.compile(r"^ {0,3}(`{3,}|~{3,})")  # the same, on the first line of a block
CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*$")
ITEM = re.compile(r"^ {0,3}(?:[-+*]|\d{1,9}[.)])\s+\S")
REF = re.compile(r"^ {0,3}\[[^\]]+\]:\s+\S+")
URL = re.compile(r"^https?://", re.IGNORECASE)
TOKEN = re.compile(r"(language-\w+|[a-z]{1,3}[0-9]?)\Z")
# tag -> attributes kept; everything else is dropped with its subtree
TAGS = {"p": (), "h1": (), "h2": (), "h3": (), "h4": (), "h5": (), "h6": (),
        "ul": (), "ol": ("start",), "li": (), "strong": (), "em": (), "del": (),
        "blockquote": (), "hr": (), "br": (), "table": (), "thead": (), "tbody": (),
        "tr": (), "th": (), "td": (), "sup": (), "code": ("class",), "span": ("class",),
        "pre": (), "a": ("href", "title")}
VOID = {"br", "hr", "img", "input"}
HILITE_WRAPPER = "codehilite"

_cache = {}  # (final, source) -> tree


def blocks(text):
    """([(source, final, language)], definitions): the message split at blank
    lines outside code fences. A block is final once a blank line, or the
    closing fence of a code block, follows it: the model can no longer add to
    it. The last block is open, so not final. Blank lines between items of one
    list do not split it. Link reference definitions come apart, to go in
    front of every block that may use them."""
    out, cur, defs = [], [], []
    fence, lang, item = None, None, False
    lines = (text or "").split("\n")
    if lines and lines[-1] == "":  # the text ends on a newline: the next line may continue the block
        lines.pop()
    for n, line in enumerate(lines):
        if fence:
            cur.append(line)
            m = CLOSE.match(line)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                out.append(("\n".join(cur), True, lang))  # a closed fence is settled
                cur, fence, lang, item = [], None, None, False
            continue
        m = FENCE.match(line)
        if m:
            if cur:
                out.append(("\n".join(cur).strip("\n"), True, None))
            fence, lang, item, cur = m.group(1), m.group(2).strip() or None, False, [line]
            continue
        if not line.strip():
            if cur and item and n + 1 < len(lines) and ITEM.match(lines[n + 1]):
                cur.append(line)  # a loose list goes on after the blank line
            elif cur:
                out.append(("\n".join(cur).strip("\n"), True, None))
                cur, item = [], False
            continue
        if REF.match(line) and not cur:
            defs.append(line)
            continue
        if not cur:
            item = bool(ITEM.match(line))
        cur.append(line)
    if cur:
        out.append(("\n".join(cur).strip("\n"), False, lang))
    return out, defs


def render(source, final):
    """The tree of one block: a list of nodes, each {"t": tag, "c": [children]}
    with "a" (kept attributes) when it has any; children are nodes or text.
    `final` blocks are highlighted and cached."""
    key = (bool(final), source)
    tree = _cache.get(key)
    if tree is None:
        html = markdown.markdown(source, extensions=FINAL if final else PLAIN,
                                extension_configs=CONFIG)
        tree = _Tree().parse(html)
        if len(_cache) >= CACHE_MAX:
            _cache.pop(next(iter(_cache)))  # the oldest go
        _cache[key] = tree
    return tree


def message(text, final=False):
    """[(i, tree, final)] for a message: every block in order. With
    `final=False` the last block is still open and comes back plain."""
    parts, defs = blocks(text)
    head = "\n\n".join(defs)
    out = []
    for i, (source, done, language) in enumerate(parts):
        if language is not None and not done:  # a fence still open: closed for this render only
            source += "\n" + OPENER.match(source).group(1)
        tree = render(head + "\n\n" + source if head and language is None else source,
                      final or done)
        for node in tree:
            if isinstance(node, dict) and node.get("t") == "pre" and language:  # the fence's word
                node["lang"] = language
        out.append((i, tree, bool(final or done)))
    return out


class _Tree(HTMLParser):
    """HTML from Markdown to the allowed tree. A tag that is not allowed is
    dropped with its subtree (raw HTML in a model's answer, scripts, images);
    the codehilite wrapper passes its children through."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = {"t": "", "c": []}
        self.stack = [self.root]
        self.dropped = []

    def parse(self, html):
        self.feed(html)
        self.close()
        return self.root["c"]

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if not self.dropped and tag == "div" and attrs.get("class") == HILITE_WRAPPER:
            return
        if self.dropped or tag not in TAGS:
            if tag not in VOID:
                self.dropped.append(tag)
            return
        keep = {}
        for k, v in attrs.items():
            v = v or ""
            ok = (URL.match(v) if k == "href" else TOKEN.match(v) if k == "class"
                  else v.isdigit() or k == "title")
            if k in TAGS[tag] and ok:
                keep[k] = v
        node = {"t": tag, "c": []}
        if keep:
            node["a"] = keep
        self.stack[-1]["c"].append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if not self.dropped and tag not in VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if self.dropped:
            if tag == self.dropped[-1]:
                self.dropped.pop()
            return
        for n in range(len(self.stack) - 1, 0, -1):
            if self.stack[n].get("t") == tag:
                del self.stack[n:]
                return

    def handle_data(self, data):
        if not self.dropped:  # the inside of a dropped tag goes with it
            self.stack[-1]["c"].append(data)
