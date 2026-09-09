"""Create the editable text edition from rendered content, without rewriting."""
from html.parser import HTMLParser
from pathlib import Path
import re
import sys

ORIGIN = "https://team-dirac-igp24.vercel.app"
ARTIFACTS = "https://raw.githubusercontent.com/ash9241/team-dirac-igp24/main/article"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
FENCE = chr(96) * 3

class Article(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.stack = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "article":
            self.active = True
        if not self.active:
            return
        inherited = self.stack[-1]["skip"] if self.stack else False
        classes = attrs.get("class", "").split()
        skip = inherited or tag in {"aside", "script", "style"} or attrs.get("aria-hidden") == "true" or bool(set(classes) & {"sharing-explorer", "roots-study", "construction-path", "polynomial-checks", "result-strip", "math-line"})
        if "math-line" in classes and not inherited:
            self.parts.append("\n\n$$\n"+attrs.get("data-tex","")+"\n$$\n\n")
        item = {"tag": tag, "skip": skip, "href": attrs.get("href", "")}
        if not skip:
            if tag in {"p", "figure", "section", "div", "figcaption", "ol", "li", "details", "summary"}:
                self.parts.append("\n\n")
            if tag == "dt":
                self.parts.append("\n\n- **")
            if tag in {"h2", "h3"}:
                self.parts.append("\n\n" + ("## " if tag == "h2" else "### "))
            if tag == "li":
                self.parts.append("- ")
            if tag == "br":
                self.parts.append(" " if any(x["tag"] in {"h2","h3"} for x in self.stack) else "\n")
            if tag == "a":
                self.parts.append("[")
            if tag == "strong":
                self.parts.append("**")
            if tag == "em":
                self.parts.append("*")
            if tag == "pre":
                self.parts.append("\n\n" + FENCE + "\n")
            if tag == "img":
                src = attrs["src"]
                if src.startswith("/"):
                    src = ARTIFACTS + src
                self.parts.append(f"\n\n![{attrs.get('alt','')}]({src})\n\n")
        if tag not in VOID:
            self.stack.append(item)

    def handle_endtag(self, tag):
        if not self.active:
            return
        index = next((i for i in range(len(self.stack)-1, -1, -1) if self.stack[i]["tag"] == tag), None)
        if index is None:
            return
        item = self.stack[index]
        self.stack = self.stack[:index]
        if not item["skip"]:
            if tag == "dt":
                self.parts.append("**: ")
            if tag == "dd":
                self.parts.append("\n\n")
            if tag == "a":
                href = item["href"]
                if href.startswith("/"):
                    href = ORIGIN + href
                self.parts.append("](" + href + ")")
            if tag == "strong":
                self.parts.append("**")
            if tag == "em":
                self.parts.append("*")
            if tag in {"p", "h2", "h3", "figcaption", "figure", "li", "section", "summary"}:
                self.parts.append("\n\n")
            if tag == "pre":
                self.parts.append("\n" + FENCE + "\n\n")
        if tag == "article":
            self.active = False

    def handle_data(self, data):
        if self.active and self.stack and not self.stack[-1]["skip"]:
            self.parts.append(data)

parser = Article()
html = Path(sys.argv[1]).read_text()
parser.feed(html)
body = re.sub(r"\n[ \t]+", "\n", "".join(parser.parts))
body = re.sub(r"\n{3,}", "\n\n", body).strip()
body = re.sub(r"(?m)^- (\*\*[^*\n]+\*\*)\n\n([^\n]+)", r"- \1. \2", body)
body = body.replace("\n\n↶\n\n", "\n\n")
body = re.sub(r"\[(\d+)\]\(#source-\1\)", r"[^\1]", body)
header = """# Two Batchmates Walk Into a Maths Competition

[Read the illustrated website](https://team-dirac-igp24.vercel.app/) · [Explore the research archive](https://github.com/ash9241/team-dirac-igp24)

By Aishwarya Das. Research with Durgesh Kumar.
September 8, 2026.

"""
def fragment_text(fragment):
    note = Article()
    note.feed("<article>" + fragment + "</article>")
    return "".join(note.parts).strip()

# Take references and credits from the same rendered page as the essay.
notes = "\n\n" + "\n".join(
    f"[^{number}]: {fragment_text(fragment)}"
    for number, fragment in re.findall(r'<li id="source-(\d+)">(.*?)</li>', html, re.S)
) + "\n\n"
credits = re.search(r'<p class="credits">(.*?)</p>', html, re.S)
if credits:
    notes += fragment_text(credits.group(1)) + "\n"
destination = Path(__file__).resolve().parent.parent/"public/downloads/two-batchmates.md"
destination.write_text(header+body+notes)
# Preserve links to the preceding edition with the current text.
destination.with_name("what-we-could-find-together.md").write_text(header+body+notes)
print(f"Text edition exported: {len((header+body).split())} words.")
