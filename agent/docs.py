"""Read the paperwork: text out of PDFs and plain files saved from mail or Drive."""

import logging
from pathlib import Path

log = logging.getLogger("yuvalbot.docs")


def read(path: str, max_chars: int = 12000) -> dict:
    p = Path(path)
    if not p.exists():
        return {"error": f"no file at {path}"}
    suffix = p.suffix.lower()

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            return {"error": "pypdf not installed — pip install pypdf"}
        try:
            reader = PdfReader(str(p))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
            if not text.strip():
                return {"file": p.name, "pages": len(reader.pages), "text": "",
                        "note": "no embedded text — this is a scan, and OCR is not "
                                "installed. Open it yourself or send me a photo."}
            return {"file": p.name, "pages": len(reader.pages),
                    "text": text[:max_chars], "truncated": len(text) > max_chars}
        except Exception as e:
            return {"error": f"could not read pdf: {e}"}

    if suffix in (".txt", ".md", ".csv", ".json", ".eml", ".html"):
        text = p.read_text(errors="replace")
        return {"file": p.name, "text": text[:max_chars], "truncated": len(text) > max_chars}

    if suffix == ".docx":
        try:
            import zipfile, re
            with zipfile.ZipFile(p) as z:                 # no python-docx dependency
                xml = z.read("word/document.xml").decode("utf-8", "replace")
            text = re.sub(r"<[^>]+>", " ", xml.replace("</w:p>", "\n"))
            return {"file": p.name, "text": " ".join(text.split())[:max_chars]}
        except Exception as e:
            return {"error": f"could not read docx: {e}"}

    return {"error": f"unsupported file type '{suffix}'", "file": p.name}
