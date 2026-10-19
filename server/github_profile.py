"""Validate public GitHub snapshots and derive safe display fields."""
import re


def public_profile(identity):
    if (not isinstance(identity, dict) or type(identity.get("id")) is not int
            or not 0 < identity["id"] < 2 ** 63
            or not isinstance(identity.get("login"), str)
            or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", identity["login"])):
        raise ValueError("Invalid GitHub identity")
    profile = {"id": identity["id"], "login": identity["login"]}
    for field, limit in (("name", 80),):
        value = identity.get(field)
        profile[field] = "".join(c for c in value if c.isprintable()).strip()[:limit] if isinstance(value, str) else ""
    organizations = identity.get("organizations", [])
    if not isinstance(organizations, list) or len(organizations) > 1000:
        raise ValueError("Invalid GitHub organizations")
    profile["organizations"] = []
    for organization in organizations:
        if (not isinstance(organization, dict)
                or type(organization.get("id")) is not int
                or not 0 < organization["id"] < 2 ** 63
                or not isinstance(organization.get("login"), str)
                or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", organization["login"])):
            raise ValueError("Invalid GitHub organization")
        profile["organizations"].append(organization)
    profile["organizations_complete"] = identity.get("organizations_complete") is True
    snapshot = identity.get("public_profile", {})
    if not isinstance(snapshot, dict):
        raise ValueError("Invalid public profile snapshot")
    profile["public_profile"] = snapshot
    if identity.get("organization_access") is not None:
        from server.github_organization import organization_access
        profile["organization_access"] = organization_access(identity["organization_access"])
    return profile
