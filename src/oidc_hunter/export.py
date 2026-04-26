"""Candidate export and report rendering."""

from __future__ import annotations

from pathlib import Path

import yaml


def render_candidates_yaml(conn) -> str:
    rows = conn.execute(
        """
        SELECT candidate_id, name, issuer, openid_configuration_url, jwks_uri, aliases_json
        FROM candidate_entries
        WHERE status = 'active'
        ORDER BY lower(name), issuer
        """
    ).fetchall()
    candidates = []
    for row in rows:
        entry = {
            "id": row["candidate_id"],
            "name": row["name"],
            "issuer": row["issuer"],
        }
        if row["openid_configuration_url"]:
            entry["openid_configuration"] = row["openid_configuration_url"]
        if row["jwks_uri"]:
            entry["jwks_uri"] = row["jwks_uri"]
        candidates.append(entry)
    return yaml.safe_dump({"candidates": candidates}, sort_keys=False)


def write_candidates_yaml(conn, path: Path) -> int:
    rendered = render_candidates_yaml(conn)
    path.write_text(rendered, encoding="utf-8")
    return len(yaml.safe_load(rendered).get("candidates", []))


def write_report(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
