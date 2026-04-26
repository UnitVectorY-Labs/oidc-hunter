"""Catalog and candidate YAML importers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _first_mapping_value(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _iter_service_entries(data: Any) -> list[tuple[str | None, dict[str, Any]]]:
    if isinstance(data, dict):
        services = data.get("services", data)
        if isinstance(services, dict):
            return [
                (str(service_id), value)
                for service_id, value in services.items()
                if isinstance(value, dict)
            ]
        if isinstance(services, list):
            entries: list[tuple[str | None, dict[str, Any]]] = []
            for value in services:
                if isinstance(value, dict):
                    service_id = value.get("id") or value.get("service_id")
                    entries.append((str(service_id) if service_id else None, value))
            return entries
    if isinstance(data, list):
        return [
            (str(item.get("id") or item.get("service_id")), item)
            for item in data
            if isinstance(item, dict)
        ]
    return []


def import_catalog_yaml(conn, run_id: str, yaml_text: str) -> int:
    data = yaml.safe_load(yaml_text) or {}
    rows = []
    for service_id, entry in _iter_service_entries(data):
        openid_configuration = _first_mapping_value(
            entry,
            ("openid-configuration", "openid_configuration", "open_id_configuration"),
        )
        rows.append(
            (
                run_id,
                service_id,
                entry.get("name") or service_id,
                entry.get("issuer") or entry.get("issuer_hint"),
                openid_configuration,
                entry.get("jwks_uri"),
                json.dumps(_as_list(entry.get("aliases")), sort_keys=True),
            )
        )
    conn.executemany(
        """
        INSERT OR IGNORE INTO catalog_entries(
            run_id, service_id, name, issuer_hint, openid_configuration_url,
            jwks_uri, aliases_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def load_candidates(conn, candidates_path: Path) -> int:
    if not candidates_path.exists():
        candidates_path.write_text("candidates: []\n", encoding="utf-8")
        return 0

    data = yaml.safe_load(candidates_path.read_text(encoding="utf-8")) or {}
    candidates = data.get("candidates", data)
    if isinstance(candidates, dict):
        iterable = candidates.values()
    elif isinstance(candidates, list):
        iterable = candidates
    else:
        iterable = []

    rows = []
    for index, entry in enumerate(iterable, start=1):
        if not isinstance(entry, dict):
            continue
        candidate_id = entry.get("id") or entry.get("candidate_id") or f"candidate-{index}"
        rows.append(
            (
                str(candidate_id),
                entry.get("name") or candidate_id,
                entry.get("issuer"),
                _first_mapping_value(
                    entry,
                    ("openid-configuration", "openid_configuration", "open_id_configuration"),
                ),
                entry.get("jwks_uri"),
                json.dumps(_as_list(entry.get("aliases")), sort_keys=True),
                entry.get("status") or "active",
            )
        )

    conn.executemany(
        """
        INSERT OR IGNORE INTO candidate_entries(
            candidate_id, name, issuer, openid_configuration_url,
            jwks_uri, aliases_json, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)
