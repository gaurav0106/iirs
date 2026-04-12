from __future__ import annotations

from dataclasses import dataclass, field

from .models import EvidenceItem


@dataclass(slots=True)
class EvidenceExpectation:
    description: str
    category: str
    tool_name: str | None = None
    source_type: str | None = None
    query_contains: str | None = None
    text_contains: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: dict[str, object]) -> "EvidenceExpectation":
        return cls(
            description=str(payload["description"]),
            category=str(payload["category"]),
            tool_name=str(payload["tool_name"]) if payload.get("tool_name") else None,
            source_type=str(payload["source_type"]) if payload.get("source_type") else None,
            query_contains=str(payload["query_contains"]) if payload.get("query_contains") else None,
            text_contains=[str(item) for item in payload.get("text_contains", [])],
        )

    def matches(self, item: EvidenceItem) -> bool:
        if item.category != self.category:
            return False

        if self.source_type and not any(
            citation.source_type.lower() == self.source_type.lower()
            for citation in item.citations
        ):
            return False

        if self.query_contains and not any(
            self.query_contains.lower() in citation.query.lower()
            for citation in item.citations
        ):
            return False

        if self.text_contains:
            haystack_parts = [item.summary, item.value]
            haystack_parts.extend(citation.excerpt for citation in item.citations)
            haystack = "\n".join(haystack_parts).lower()
            for fragment in self.text_contains:
                if fragment.lower() not in haystack:
                    return False

        return True
