"""Approved-knowledge retrieval.

Only pages whose front matter says `status: Approved` are loaded. Each page is
split into `## sections`; a section is the unit returned to the agent, with its
citation (page id, title, version) so every answer can be traced to a source.

Retrieval is BM25 over word tokens. It is dependency-free, works for Bangla and
English, and is fast enough for a clinic-sized knowledge base (hundreds of
sections). Swap in pgvector when the corpus grows.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

_TOKEN = re.compile(r"[a-z0-9]+|[ঀ-৿]+")
_STOP = set("""a an the and or of to in for on is are be with you your it this that if my i me can
do does how what when should at as by from not no usually about more than help helps get""".split())

# A few Bangla <-> English bridges so a Bangla question finds English sections.
_SYNONYMS = {
    "কোমর": ["back", "lower"], "পিঠ": ["back"], "ব্যথা": ["pain"], "গোড়ালি": ["heel"], "পা": ["foot"], "এক্সরে": ["x", "ray", "scan"], "এক্স-রে": ["x", "ray"], "এক্স": ["x", "ray"],
    "এমআরআই": ["mri", "scan"], "ব্যায়াম": ["exercise"], "বেল্ট": ["belt"], "সকালে": ["morning"],
    "ঘাড়": ["neck"], "স্ট্রেচ": ["stretching"], "জুতা": ["footwear"], "fasciitis": ["plantar", "heel"],
}


# Words that say nothing about WHICH condition is meant. A section only matches
# when at least one other (topic) word matches, so "neck pain exercises" never
# returns the heel page just because both mention exercise and pain.
_GENERIC = set("""pain pains painful hurt hurts exercise exercises treat treatment treatments help helps fix good
best bad need take get feel doctor physio physiotherapy therapy what which way ways ব্যথা ব্যায়াম চিকিৎসা কী করব
করা লাগবে হয় দরকার ভালো""".split())
_BN_SUFFIXES = ("গুলোর", "গুলো", "ের", "এর", "তে", "টা", "টি", "র", "ে")


def _bn_stem(tok: str) -> str:
    for suf in _BN_SUFFIXES:
        if tok.endswith(suf) and len(tok) - len(suf) >= 2:
            return tok[: -len(suf)]
    return tok


def tokenize(text: str) -> list[str]:
    toks = [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]
    out: list[str] = []
    for t in toks:
        if "\u0980" <= t[0] <= "\u09FF" and t not in _SYNONYMS:
            t = _bn_stem(t)
        elif len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]  # belts -> belt, scans -> scan
        out.append(t)
        out.extend(_SYNONYMS.get(t, []))
    return out


@dataclass
class Section:
    page_id: str
    title: str
    version: str
    heading: str
    text_en: str
    text_bn: str

    @property
    def citation(self) -> str:
        return f"{self.page_id} {self.version}: {self.title} › {self.heading}"


def _parse_page(path: Path) -> tuple[dict, list[tuple[str, str]]]:
    raw = path.read_text(encoding="utf-8")
    meta: dict = {}
    body = raw
    if raw.startswith("---"):
        _, fm, body = raw.split("---", 2)
        meta = yaml.safe_load(fm) or {}
    sections = []
    for block in re.split(r"^## ", body, flags=re.M)[1:]:
        heading, _, text = block.partition("\n")
        sections.append((heading.strip(), text.strip()))
    return meta, sections


class KnowledgeBase:
    def __init__(self, directory: Path):
        self.directory = directory
        self.sections: list[Section] = []
        self.excluded: list[str] = []
        self.version = ""
        self.reload()

    def reload(self) -> None:
        self.sections, self.excluded = [], []
        digest = hashlib.sha256()
        for path in sorted(self.directory.glob("*.md")):
            meta, secs = _parse_page(path)
            if str(meta.get("status", "")).strip().lower() != "approved":
                self.excluded.append(f"{meta.get('id', path.stem)} ({meta.get('status', 'no status')})")
                continue
            digest.update(path.read_bytes())
            for heading, text in secs:
                en_lines, bn_lines = [], []
                for line in text.splitlines():
                    if line.startswith("> বাংলা:"):
                        bn_lines.append(line.removeprefix("> বাংলা:").strip())
                    elif line.strip():
                        en_lines.append(line.strip())
                self.sections.append(Section(
                    str(meta.get("id", path.stem)), str(meta.get("title", path.stem)),
                    str(meta.get("version", "")), heading, " ".join(en_lines), " ".join(bn_lines),
                ))
        # The version string changes whenever any approved page changes,
        # which invalidates cached answers automatically.
        self.version = digest.hexdigest()[:12]
        self._index()

    def _index(self) -> None:
        self._docs = [tokenize(f"{s.title} {s.heading} {s.text_en} {s.text_bn}") for s in self.sections]
        self._df: Counter = Counter()
        for d in self._docs:
            self._df.update(set(d))
        self._avgdl = (sum(len(d) for d in self._docs) / len(self._docs)) if self._docs else 1.0

    def search(self, query: str, k: int = 3, min_score: float = 1.0) -> list[tuple[Section, float]]:
        q = tokenize(query)
        topic_terms = {t for t in q if t not in _GENERIC}
        n = len(self._docs)
        scored = []
        for sec, doc in zip(self.sections, self._docs):
            tf = Counter(doc)
            if topic_terms and not topic_terms & tf.keys():
                continue  # nothing about the asked-for condition in this section
            score = 0.0
            for term in q:
                if term not in tf:
                    continue
                idf = math.log(1 + (n - self._df[term] + 0.5) / (self._df[term] + 0.5))
                f = tf[term]
                weight = 1.0 if (not topic_terms or term in topic_terms) else 0.3  # generic words only break ties
                score += weight * idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * len(doc) / self._avgdl))
            if score >= min_score:
                scored.append((sec, round(score, 2)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]
