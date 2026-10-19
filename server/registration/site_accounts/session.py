"""Account service site_accounts: session."""
import time
from urllib.parse import urlencode
from flask import current_app, jsonify, request, session
from ..auth import current_account
from .domain import ACCOUNT_HOST, sites
from .schema import own_profile
from .preferences import _preferences


@sites.get("/api/site/session")
def site_session():
    import ipaddress
    from ..github import github_status
    user = current_account()
    profile = None
    avatar = "/default-avatar.png"
    if user:
        store = current_app.extensions["registration_store"]
        account = store.find_account(user["username"])
        with store.connection() as connection:
            profile = own_profile(connection, account)
        linked = github_status(account=user).get("github")
        if linked:
            avatar = linked.get("avatar_url") or avatar
    pending = session.get("pending_sso", "")
    continuation = None
    if user and request.host == ACCOUNT_HOST and pending:
        with current_app.extensions["registration_store"].connection() as connection:
            valid = connection.execute("SELECT 1 FROM sso_flows WHERE state_hash=? AND expires_at>?",
                                       (current_app.extensions["registration_store"].token_hash(pending),
                                        int(time.time()))).fetchone()
        if valid:
            continuation = "/sso/authorize?" + urlencode({"state": pending})
        else:
            session.pop("pending_sso", None)
    return jsonify(user={"name": user["nickname"] or user["real_name"] or user["username"],
                         "avatar": avatar, "profile": profile,
                         "preferences": _preferences(store, account["id"]) if user else None} if user else None,
                   csrf_token=session.get("csrf_token", ""), continue_url=continuation,
                   sso_available=any(ipaddress.ip_address(request.remote_addr) in ipaddress.ip_network(network)
                                     for network in current_app.config["SSO_NETWORKS"]),
                   sso_checked=int(time.time()) - session.get("sso_checked", 0) < 60)

