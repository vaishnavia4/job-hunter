"""Extract plain text from a resume (.docx, .pdf, .txt/.md)."""
import re
import zipfile
from pathlib import Path


def extract_text(path):
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(str(p))
    suf = p.suffix.lower()
    if suf == ".docx":
        try:
            import docx
            d = docx.Document(str(p))
            lines = [para.text for para in d.paragraphs if para.text.strip()]
            for t in d.tables:
                for row in t.rows:
                    lines.append(" | ".join(c.text.strip() for c in row.cells))
            return "\n".join(lines)
        except Exception:
            x = zipfile.ZipFile(p).read("word/document.xml").decode("utf8", "ignore")
            paras = re.findall(r"<w:p[ >].*?</w:p>", x, re.S)
            return "\n".join(t for t in ("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", pp)) for pp in paras) if t.strip())
    if suf == ".pdf":
        from pypdf import PdfReader
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)
    return p.read_text(encoding="utf-8", errors="ignore")
