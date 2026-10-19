"""Strict normalization of authorized BETA-SDC organization snapshots."""
import re


def organization_access(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("state") not in (
        "member", "not_member", "permission_required", "unavailable"
    ):
        raise ValueError("Invalid organization snapshot")
    teams = snapshot.get("teams", [])
    memberships = snapshot.get("member_team_ids", [])
    if (not isinstance(teams, list) or len(teams) > 1000
            or not isinstance(memberships, list) or len(memberships) > 1000):
        raise ValueError("Invalid team snapshot")
    normalized = {}
    for team in teams:
        if (not isinstance(team, dict) or type(team.get("id")) is not int or not 0 < team["id"] < 2**63
                or not isinstance(team.get("name"), str) or not 1 <= len(team["name"]) <= 100
                or not isinstance(team.get("slug"), str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", team["slug"])):
            raise ValueError("Invalid team")
        parent = team.get("parent_id")
        if parent is not None and (type(parent) is not int or not 0 < parent < 2**63):
            raise ValueError("Invalid parent")
        if team["id"] in normalized:
            raise ValueError("Duplicate team")
        normalized[team["id"]] = {
            "id": team["id"], "name": team["name"], "slug": team["slug"], "parent_id": parent}
    for team in normalized.values():
        seen = {team["id"]}
        parent = team["parent_id"]
        while parent in normalized:
            if parent in seen:
                raise ValueError("Cyclic team hierarchy")
            seen.add(parent)
            parent = normalized[parent]["parent_id"]
    if any(type(item) is not int or item not in normalized for item in memberships):
        raise ValueError("Unknown team membership")
    complete = snapshot.get("structure_complete") is True
    if complete and any(team["parent_id"] is not None and team["parent_id"] not in normalized
                        for team in normalized.values()):
        raise ValueError("Incomplete hierarchy")
    return {"state": snapshot["state"], "teams": list(normalized.values()),
            "member_team_ids": sorted(set(memberships)), "structure_complete": complete,
            "role": "owner" if snapshot.get("state") == "member" and snapshot.get("role") == "owner"
            else "member"}
