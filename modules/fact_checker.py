"""Fact checking and validation of AI research output against collected sources.

The AI proposes a structured report; this module verifies it deterministically:
- every cited source id must exist
- numbers and quotes must literally occur in the cited article text
- corroboration requires independent domains, not just multiple links
- unverifiable claims are demoted and surfaced instead of being presented as facts
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

NUMBER_RE = re.compile(r"\d[\d.,\u00a0 ]*\d|\d")
PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")

VERDICT_SUPPORTED = "supported"
VERDICT_SINGLE = "single_source"
VERDICT_CONFLICTING = "conflicting"
VERDICT_UNVERIFIED = "unverified"
VERDICT_UNSUPPORTED = "unsupported"


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text.replace("đ", "d").replace("Đ", "D")).strip().lower()


def _numbers(text: str) -> list[str]:
    found = []
    for match in NUMBER_RE.finditer(text or ""):
        raw = match.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 2:
            found.append(digits)
    return found


def _contains_number(source_text: str, number_digits: str) -> bool:
    haystack = re.sub(r"\D", "", source_text or "")
    return number_digits in haystack if number_digits else True


def _quote_supported(quote: str, source_text: str) -> bool:
    needle = _fold(quote).strip(" .,\"'“”‘’")
    if len(needle) < 12:
        return False
    haystack = _fold(source_text)
    if needle in haystack:
        return True
    # Allow light truncation with ellipsis: test the two halves independently.
    if "..." in needle or "…" in needle:
        parts = [p.strip() for p in re.split(r"\.\.\.|…", needle) if len(p.strip()) >= 12]
        return bool(parts) and all(part in haystack for part in parts)
    return False


class FactChecker:
    def validate(
        self,
        report: dict,
        sources: list[dict],
        topic_title: str,
        min_sources: int = 2,
    ) -> dict:
        issues: list[str] = []
        source_by_id = {int(s["id"]): s for s in sources if str(s.get("id", "")).isdigit()}
        domains_with_content = {
            s.get("domain") for s in sources if s.get("content") and s.get("domain")
        }
        domains_all = {s.get("domain") for s in sources if s.get("domain")}
        independent = len(domains_with_content or domains_all)
        links_only = [s for s in sources if not s.get("content")]

        facts = report.get("key_facts") or []
        if isinstance(facts, str):
            facts = [{"fact": facts, "source_ids": []}]
        cleaned_facts: list[dict] = []
        fact_rows: list[dict] = []
        supported_count = 0

        for item in facts:
            if isinstance(item, str):
                item = {"fact": item, "source_ids": []}
            if not isinstance(item, dict):
                continue
            claim = str(item.get("fact") or item.get("claim") or "").strip()
            if not claim:
                continue
            raw_ids = item.get("source_ids") or item.get("sources") or []
            ids: list[int] = []
            for value in raw_ids if isinstance(raw_ids, list) else [raw_ids]:
                try:
                    ids.append(int(value))
                except (TypeError, ValueError):
                    continue
            valid = [i for i in ids if i in source_by_id]
            if len(valid) != len(ids):
                issues.append(f"Bỏ nguồn không tồn tại trong dẫn chứng: {claim[:80]}")
            cited_sources = [source_by_id[i] for i in valid]
            cited_domains = {s.get("domain") for s in cited_sources}
            status = str(item.get("status") or "").lower()
            notes = ""

            # Numbers must exist in at least one cited source.
            cited_text = "\n".join(
                (s.get("content") or "") + "\n" + (s.get("excerpt") or "") for s in cited_sources
            )
            missing_numbers = [
                number for number in _numbers(claim) if not _contains_number(cited_text, number)
            ]
            if missing_numbers:
                status = VERDICT_UNSUPPORTED
                notes = "Số liệu không tìm thấy trong nguồn được dẫn"
                issues.append(f"Số liệu chưa kiểm chứng: {claim[:90]}")
            elif status in ("conflicting", "conflict", "mâu thuẫn"):
                status = VERDICT_CONFLICTING
                notes = "Các nguồn có thông tin mâu thuẫn"
            elif len(cited_domains) >= 2:
                status = VERDICT_SUPPORTED
                supported_count += 1
            elif len(cited_domains) == 1:
                status = VERDICT_SINGLE
                notes = "Chỉ một nguồn độc lập đề cập"
            elif not cited_sources:
                status = VERDICT_UNVERIFIED
                notes = "Không có nguồn dẫn chứng cụ thể"
            else:
                status = VERDICT_UNVERIFIED

            row = {
                "claim": claim,
                "verdict": status,
                "confidence": round(
                    {VERDICT_SUPPORTED: 0.9, VERDICT_SINGLE: 0.55, VERDICT_CONFLICTING: 0.4,
                     VERDICT_UNVERIFIED: 0.25, VERDICT_UNSUPPORTED: 0.1}.get(status, 0.25),
                    2,
                ),
                "notes": notes,
                "source_urls": [s.get("url") for s in cited_sources if s.get("url")],
            }
            fact_rows.append(row)
            cleaned_facts.append(
                {
                    "fact": claim,
                    "source_ids": valid,
                    "verdict": status,
                    "notes": notes,
                }
            )

        # Quotes: only keep verbatim quotes found in a cited article.
        quotes_raw = report.get("quotes") or []
        quotes: list[dict] = []
        for quote in quotes_raw if isinstance(quotes_raw, list) else []:
            if isinstance(quote, str):
                quote = {"text": quote, "speaker": "", "source_id": 0}
            if not isinstance(quote, dict):
                continue
            text = str(quote.get("text") or "").strip()
            if not text:
                continue
            try:
                source_id = int(quote.get("source_id") or quote.get("source") or 0)
            except (TypeError, ValueError):
                source_id = 0
            cited = source_by_id.get(source_id)
            candidates = [cited] if cited else sources
            if any(_quote_supported(text, (s.get("content") or s.get("excerpt") or "")) for s in candidates if s):
                quotes.append(
                    {
                        "text": text,
                        "speaker": str(quote.get("speaker") or ""),
                        "source_id": source_id or None,
                    }
                )
            else:
                issues.append(f"Loại trích dẫn không tìm thấy trong nguồn: “{text[:70]}…”")

        # Timeline sanity
        timeline = []
        for entry in report.get("timeline") or []:
            if isinstance(entry, dict) and (entry.get("event") or entry.get("date")):
                timeline.append({"date": str(entry.get("date") or ""), "event": str(entry.get("event") or "")})
            elif isinstance(entry, str):
                timeline.append({"date": "", "event": entry})

        confirmed = [f["fact"] for f in cleaned_facts if f["verdict"] == VERDICT_SUPPORTED]
        unconfirmed = [
            f["fact"] for f in cleaned_facts
            if f["verdict"] in (VERDICT_SINGLE, VERDICT_UNVERIFIED, VERDICT_UNSUPPORTED)
        ]
        conflicting = [
            {"claim": f["fact"], "notes": f["notes"]}
            for f in cleaned_facts if f["verdict"] == VERDICT_CONFLICTING
        ]
        for entry in report.get("conflicting") or []:
            if isinstance(entry, dict):
                text = str(entry.get("claim") or entry.get("description") or "").strip()
                if text:
                    conflicting.append({"claim": text, "notes": str(entry.get("notes") or "")})
            elif isinstance(entry, str) and entry.strip():
                conflicting.append({"claim": entry.strip(), "notes": ""})

        total_facts = max(1, len(cleaned_facts))
        support_ratio = supported_count / total_facts
        base = {0: 20, 1: 45}.get(independent, 65 if independent == 2 else 78 if independent == 3 else 88)
        confidence = base * (0.7 + 0.3 * support_ratio)
        confidence -= min(20, 5 * len(conflicting))
        confidence -= min(12, 1.5 * len(issues))
        if independent < min_sources:
            confidence = min(confidence, 55)
        if len(links_only) == len(sources) and sources:
            confidence = min(confidence, 40)
        confidence = round(max(5.0, min(97.0, confidence)), 1)

        if confidence < 45:
            label = "insufficient"
        elif confidence < 60:
            label = "low"
        elif confidence < 75:
            label = "moderate"
        elif confidence < 88:
            label = "high"
        else:
            label = "very_high"

        summary = str(report.get("summary") or "").strip()
        if not summary:
            summary = (sources[0].get("excerpt") or "")[:400] if sources else ""

        cleaned = {
            "topic_title": topic_title,
            "summary": summary,
            "key_facts": cleaned_facts,
            "timeline": timeline,
            "entities": {
                "people": [str(x) for x in (report.get("entities") or {}).get("people", []) if x],
                "organizations": [str(x) for x in (report.get("entities") or {}).get("organizations", []) if x],
                "locations": [str(x) for x in (report.get("entities") or {}).get("locations", []) if x],
            },
            "confirmed": confirmed,
            "unconfirmed": unconfirmed,
            "conflicting": conflicting,
            "missing_information": [str(x) for x in (report.get("missing_information") or []) if x],
            "quotes": quotes,
            "confidence": confidence,
            "confidence_label": label,
            "confidence_notes": str(report.get("confidence_notes") or ""),
            "issues": issues,
            "corroboration": {
                "independent_domains": sorted(
                    d for d in (domains_with_content or domains_all) if d
                ),
                "independent_domain_count": independent,
                "domains_all": sorted(d for d in domains_all if d),
                "sources_with_content": sum(1 for s in sources if s.get("content")),
                "links_only": len(links_only),
                "fact_support_ratio": round(support_ratio, 2),
            },
            "sources": [
                {
                    "id": s.get("id"),
                    "title": s.get("title"),
                    "url": s.get("url"),
                    "domain": s.get("domain"),
                    "published_at": s.get("published_at"),
                    "reliability": s.get("reliability"),
                    "content_available": bool(s.get("content")),
                    "chars": s.get("content_chars", 0),
                }
                for s in sources
            ],
        }
        return {
            "report": cleaned,
            "facts": fact_rows,
            "confidence": confidence,
            "confidence_label": label,
            "issues": issues,
        }
