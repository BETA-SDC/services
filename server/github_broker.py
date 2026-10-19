"""Credential-isolated GitHub identity exchange, listening only on a private Unix socket."""
from http.server import BaseHTTPRequestHandler
import base64
import json
import os
from pathlib import Path
import re
import socketserver
import time
import urllib.parse
import urllib.request
import urllib.error
from server.github_profile import public_profile
from server.github_organization import organization_access

CALLBACK = "https://register.betasdc.duckdns.org/api/github/callback"
SOCKET = "/run/betasdc-github/broker.sock"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def remote_json(url, *, data=None, headers=None, timeout=5):
    request = urllib.request.Request(url, data=data, headers={
        "Accept": "application/json", "User-Agent": "BETA-Campus-Account",
        **(headers or {}),
    })
    with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
        body = response.read(524289)
    if len(body) > 524288:
        raise ValueError("Oversized provider response")
    result = json.loads(body)
    if not isinstance(result, (dict, list)):
        raise ValueError("Invalid provider response")
    return result


def fetch_public_identity(identity, deadline=None, *, client_id=None, client_secret=None):
    deadline = deadline or time.monotonic() + 8
    verified = public_profile(identity)
    remaining = max(0.1, min(3, deadline - time.monotonic()))
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if client_id and client_secret:
        # OAuth application authentication reads public data without a user token.
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        headers["Authorization"] = "Basic " + basic
    snapshot = remote_json("https://api.github.com/user/" + str(verified["id"]),
                           timeout=remaining, headers=headers)
    if not isinstance(snapshot, dict) or snapshot.get("id") != verified["id"]:
        raise ValueError("GitHub identity changed during exchange")
    verified = public_profile(snapshot)
    organizations, complete = [], False
    for page in range(1, 11):
        remaining = deadline - time.monotonic()
        if remaining <= 0.2:
            break
        try:
            batch = remote_json(
                f"https://api.github.com/users/{verified['login']}/orgs?per_page=100&page={page}",
                timeout=min(3, remaining), headers=headers,
            )
            if not isinstance(batch, list) or len(batch) > 100:
                raise ValueError("Invalid public organizations")
        except (OSError, ValueError):
            break
        organizations.extend(batch)
        if len(batch) < 100:
            complete = True
            break
    return public_profile({
        "id": verified["id"], "login": snapshot["login"], "name": snapshot.get("name"),
        "public_profile": snapshot, "organizations": organizations,
        "organizations_complete": complete,
    })


def fetch_organization(token, scopes, deadline):
    empty = {"state": "permission_required", "teams": [], "member_team_ids": [],
             "structure_complete": False}
    if (not isinstance(scopes, str)
            or not ({"read:org", "write:org", "admin:org"} & set(re.split(r"[, ]+", scopes)))):
        return empty
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}

    def pages(path):
        items = []
        for page in range(1, 11):
            remaining = deadline - time.monotonic()
            if remaining < 0.2:
                raise ValueError("Organization sync deadline exceeded")
            batch = remote_json(f"https://api.github.com{path}&per_page=100&page={page}",
                                headers=headers, timeout=min(3, remaining))
            if not isinstance(batch, list) or len(batch) > 100:
                raise ValueError("Invalid organization response")
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise ValueError("Organization response truncated")

    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0.2:
            raise ValueError("Organization sync deadline exceeded")
        membership = remote_json("https://api.github.com/user/memberships/orgs/BETA-SDC",
                                 headers=headers, timeout=min(3, remaining))
        if (not isinstance(membership, dict)
                or membership.get("organization", {}).get("login", "").casefold() != "beta-sdc"
                or membership.get("state") != "active"):
            # An OAuth app can lack organization access even with read:org.
            return empty
        member_teams = [
            item for item in pages("/user/teams?")
            if item.get("organization", {}).get("login", "").casefold() == "beta-sdc"
        ]
        visible = pages("/orgs/BETA-SDC/teams?")
        by_id = {}
        for team in visible + member_teams:
            by_id[team["id"]] = team
            parent = team.get("parent")
            # Retain a parent node even when the visible list omitted it.
            if parent and parent["id"] not in by_id:
                by_id[parent["id"]] = parent
        teams = [{"id": item["id"], "name": item["name"], "slug": item["slug"],
                  "parent_id": (item.get("parent") or {}).get("id")} for item in by_id.values()]
        return organization_access({
            "state": "member", "teams": teams,
            "member_team_ids": [team["id"] for team in member_teams],
            # Only an owner's complete listing is authoritative for the whole organization.
            "structure_complete": membership.get("role") == "admin",
            "role": "owner" if membership.get("role") == "admin" else "member",
        })
    except urllib.error.HTTPError as error:
        return {**empty, "state": "permission_required" if error.code in (403, 404) else "unavailable"}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {**empty, "state": "unavailable"}


def exchange(code, verifier, client_id, client_secret):
    deadline = time.monotonic() + 24
    data = remote_json("https://github.com/login/oauth/access_token", data=urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret, "code": code,
        "redirect_uri": CALLBACK, "code_verifier": verifier,
    }).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=3)
    token = data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_]{10,512}", token):
        raise ValueError("Provider rejected authorization")
    identity = remote_json("https://api.github.com/user", timeout=3, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
    })
    profile = fetch_public_identity(identity, min(deadline, time.monotonic() + 8),
                                    client_id=client_id, client_secret=client_secret)
    # The user token is transient: only whitelisted organization/team fields leave the broker.
    profile["organization_access"] = fetch_organization(token, data.get("scope", ""), deadline)
    return profile


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(27)
        super().setup()

    def log_message(self, *args):
        pass

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if (self.path not in ("/exchange", "/profile") or not 0 < length <= 2048
                    or self.headers.get("Content-Type") != "application/json"):
                raise ValueError("Invalid broker request")
            data = json.loads(self.rfile.read(length))
            if self.path == "/profile":
                result = fetch_public_identity(data, client_id=self.server.client_id,
                                               client_secret=self.server.client_secret)
                status = 200
                self.respond(result, status)
                return
            if (not isinstance(data, dict)
                    or not isinstance(data.get("code"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", data["code"])
                    or not isinstance(data.get("verifier"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", data["verifier"])):
                raise ValueError("Invalid exchange input")
            result = exchange(data["code"], data["verifier"],
                              self.server.client_id, self.server.client_secret)
            status = 200
        except (OSError, ValueError, RecursionError):
            result, status = {"error": "GitHub exchange unavailable"}, 502
        self.respond(result, status)

    def respond(self, result, status):
        body = json.dumps(result).encode()
        if len(body) > 524288:
            body, status = b'{"error":"GitHub profile too large"}', 502
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 8


def main():
    credentials = Path(os.environ["CREDENTIALS_DIRECTORY"])
    client_id = (credentials / "github-client-id").read_text().strip()
    secret = (credentials / "github-client-secret").read_text().strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{10,100}", client_id) or not re.fullmatch(r"[A-Za-z0-9_]{20,200}", secret):
        raise ValueError("Invalid GitHub credentials")
    Path(SOCKET).unlink(missing_ok=True)
    with Server(SOCKET, Handler) as server:
        os.chmod(SOCKET, 0o660)
        server.client_id, server.client_secret = client_id, secret
        server.serve_forever()


if __name__ == "__main__":
    main()
