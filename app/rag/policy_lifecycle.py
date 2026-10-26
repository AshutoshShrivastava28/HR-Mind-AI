"""Pure policy-lifecycle selection helpers used by the RAG workflow."""
from __future__ import annotations

from typing import Any


def normalize_policy_family(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def build_active_policy_conflicts(policy_map: dict[str, dict]) -> dict[str, dict]:
    """Find policy families with more than one administrator-marked Active version."""
    groups: dict[str, dict] = {}
    for document_id, policy in policy_map.items():
        family_label = " ".join(str(policy.get("policy_family") or "").split())
        key = normalize_policy_family(family_label)
        if not key or str(policy.get("policy_status") or "unreviewed").lower() != "active":
            continue
        group = groups.setdefault(key, {"policy_family": family_label, "documents": []})
        group["documents"].append({
            "document_id": str(policy.get("document_id") or document_id),
            "filename": str(policy.get("filename") or "Unnamed document"),
            "version_label": str(policy.get("version_label") or "unspecified version"),
            "effective_date": str(policy.get("effective_date") or "unspecified date"),
        })
    return {key: group for key, group in groups.items() if len(group["documents"]) > 1}


def filter_company_policy_candidates(
    documents: list[Any],
    policy_map: dict[str, dict],
    limit: int,
) -> tuple[list[Any], dict]:
    """Filter stale versions, prioritize current policies, and attach ambiguity warnings.

    Legacy documents without a known policy family remain searchable but are marked Unreviewed.
    When a family has an Active version, only Active documents in that family remain candidates.
    When multiple documents are Active in the same family, the ambiguity is attached even when
    vector search returned only one of those versions.
    """
    conflicts = build_active_policy_conflicts(policy_map)
    kept: list[tuple[int, int, Any]] = []
    retrieved_conflict_families: set[str] = set()
    stats = {
        "excluded_draft_or_superseded": 0,
        "excluded_by_active_family": 0,
        "active_conflict_families": sorted(group["policy_family"] for group in conflicts.values()),
    }

    for original_index, document in enumerate(documents):
        metadata = dict(getattr(document, "metadata", {}) or {})
        document_id = str(metadata.get("hrmindai_document_id") or "")
        policy = policy_map.get(document_id) if document_id else None

        if not policy:
            metadata.setdefault("hrmindai_document_kind", "company")
            metadata.setdefault("hrmindai_policy_status", "unreviewed")
            metadata.setdefault("hrmindai_policy_family", "")
            metadata.setdefault("hrmindai_version_label", "")
            metadata.setdefault("hrmindai_effective_date", "")
            metadata["hrmindai_policy_conflict"] = False
            metadata["hrmindai_policy_conflict_summary"] = ""
            document.metadata = metadata
            kept.append((1, original_index, document))
            continue

        status = str(policy.get("policy_status") or "unreviewed").strip().lower()
        family_label = " ".join(str(policy.get("policy_family") or "").split())
        family_key = normalize_policy_family(family_label)

        if status in {"draft", "superseded"}:
            stats["excluded_draft_or_superseded"] += 1
            continue

        # If the family has an Active version, an Unreviewed/Draft/Superseded candidate in the
        # same family must not outrank or contradict that explicitly selected version.
        family_actives = [
            item for item in policy_map.values()
            if family_key
            and normalize_policy_family(item.get("policy_family")) == family_key
            and str(item.get("policy_status") or "unreviewed").lower() == "active"
        ]
        if family_actives and status != "active":
            stats["excluded_by_active_family"] += 1
            continue

        metadata.update({
            "hrmindai_document_kind": "company",
            "hrmindai_policy_status": status,
            "hrmindai_policy_family": family_label,
            "hrmindai_version_label": str(policy.get("version_label") or ""),
            "hrmindai_effective_date": str(policy.get("effective_date") or ""),
        })

        conflict = conflicts.get(family_key) if family_key else None
        if conflict:
            retrieved_conflict_families.add(conflict["policy_family"])
            versions = conflict["documents"]
            summary = (
                f'Multiple documents are marked Active for policy family "{conflict["policy_family"]}": '
                + "; ".join(
                    f'{item["filename"]} ({item["version_label"]}, effective {item["effective_date"]})'
                    for item in versions
                )
                + ". The authoritative version is ambiguous and requires HR confirmation."
            )
            metadata["hrmindai_policy_conflict"] = True
            metadata["hrmindai_policy_conflict_summary"] = summary
            metadata["hrmindai_policy_conflict_versions"] = " | ".join(
                f'{item["filename"]} · {item["version_label"]} · {item["effective_date"]}'
                for item in versions
            )
        else:
            metadata["hrmindai_policy_conflict"] = False
            metadata["hrmindai_policy_conflict_summary"] = ""
            metadata["hrmindai_policy_conflict_versions"] = ""

        document.metadata = metadata
        # Approved Active chunks are ordered ahead of general/unreviewed chunks. Conflicts are
        # carried forward explicitly instead of silently selecting a winner.
        priority = 0 if status == "active" else 1
        kept.append((priority, original_index, document))

    kept.sort(key=lambda item: (item[0], item[1]))
    selected = [document for _, _, document in kept[:max(1, int(limit))]]
    stats["selected_chunks"] = len(selected)
    stats["conflict_families"] = len(conflicts)
    stats["retrieved_conflict_families"] = sorted(retrieved_conflict_families)
    return selected, stats
