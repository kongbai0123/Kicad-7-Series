"""Small safe Markdown reader for the project's own learning notes."""
import html
from pathlib import Path
import re
from urllib.parse import quote, urlsplit


def render_document(path: Path, repo: Path) -> str:
    def url(target):
        parts = urlsplit(target)
        if parts.scheme in {"http", "https"}:
            return html.escape(target, quote=True)
        if parts.scheme or parts.netloc:
            return "#"
        resolved = (path.parent / parts.path).resolve()
        if not resolved.is_relative_to(repo):
            return "#"
        relative = resolved.relative_to(repo).as_posix()
        prefix = "/read/" if resolved.suffix == ".md" else "/files/"
        return prefix + quote(relative, safe="/")

    token = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)|\[([^\]]+)\]\(([^)]+)\)|`([^`]+)`|\*\*([^*]+)\*\*")

    def inline(text):
        out = []
        last = 0
        for match in token.finditer(text):
            out.append(html.escape(text[last:match.start()]))
            if match.group(1) is not None:
                out.append(f'<figure><a href="{url(match.group(2))}" target="_blank" rel="noopener"><img src="{url(match.group(2))}" alt="{html.escape(match.group(1), quote=True)}" loading="lazy"></a><figcaption>{html.escape(match.group(1))}</figcaption></figure>')
            elif match.group(3) is not None:
                out.append(f'<a href="{url(match.group(4))}" target="_blank" rel="noopener">{html.escape(match.group(3))}</a>')
            elif match.group(5) is not None:
                out.append(f'<code>{html.escape(match.group(5))}</code>')
            else:
                out.append(f'<strong>{html.escape(match.group(6))}</strong>')
            last = match.end()
        out.append(html.escape(text[last:]))
        return "".join(out)

    lines = path.read_text(encoding="utf-8-sig").splitlines()
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("```"):
            i += 1
            code = []
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            blocks.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
        elif re.match(r"^#{1,6} ", line):
            level = len(line) - len(line.lstrip("#"))
            blocks.append(f"<h{level}>" + inline(line[level + 1:]) + f"</h{level}>")
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                row = lines[i]
                if not re.fullmatch(r"[|:\-\s]+", row):
                    cells = row.strip().strip("|").split("|")
                    tag = "th" if not rows else "td"
                    rows.append("<tr>" + "".join(f"<{tag}>" + inline(cell.strip()) + f"</{tag}>" for cell in cells) + "</tr>")
                i += 1
            blocks.append('<div class="table-scroll"><table>' + "".join(rows) + "</table></div>")
            continue
        elif re.match(r"^(?:- |\d+\. )", line):
            numbered = bool(re.match(r"^\d+\. ", line))
            tag = "ol" if numbered else "ul"
            items = []
            pattern = r"^\d+\. " if numbered else r"^- "
            while i < len(lines) and re.match(pattern, lines[i]):
                items.append("<li>" + inline(re.sub(pattern, "", lines[i], count=1)) + "</li>")
                i += 1
            blocks.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue
        else:
            paragraph = [line]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"^(?:#|\||```|- |\d+\. )", lines[i]):
                paragraph.append(lines[i])
                i += 1
            content = inline("\n".join(paragraph))
            blocks.append(content if len(paragraph) == 1 and paragraph[0].startswith("![") else "<p>" + content + "</p>")
            continue
        i += 1
    title = html.escape(next((line[2:] for line in lines if line.startswith("# ")), path.stem))
    styles = "body{font:17px/1.8 'Microsoft JhengHei',sans-serif;background:#f8f9f6;color:#1b3038;max-width:1000px;margin:32px auto;padding:0 24px}a{color:#087e75}h1{font-size:30px;line-height:1.4}h2{font-size:23px;margin-top:2em}h3{font-size:19px}p,li{overflow-wrap:anywhere}table{border-collapse:collapse;width:100%;font-size:15px}td,th{padding:12px;text-align:left;vertical-align:top;border-bottom:1px solid #d8dfda}th{background:#eaf0eb}.table-scroll{overflow:auto}pre{padding:16px;background:#eaf0eb;border-radius:8px;white-space:pre-wrap;overflow-wrap:anywhere}code{font:0.9em Consolas,monospace}figure{margin:20px 0}img{display:block;max-width:100%;height:auto;border:1px solid #d8dfda;border-radius:6px}figcaption{font-size:14px;color:#5a6b66}header{padding-bottom:16px;border-bottom:1px solid #d8dfda}"
    return '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + title + '</title><style>' + styles + '</style></head><body><header><a href="/">回課程總覽</a></header><main>' + "\n".join(blocks) + '</main></body></html>'
