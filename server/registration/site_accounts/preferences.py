"""Account service site_accounts: preferences."""
from flask import current_app, jsonify, request
from ..auth import current_account
from .domain import ACCOUNT_HOST, sites


def _preferences(store, user_id):
    with store.connection() as connection:
        row = connection.execute("SELECT theme FROM user_preferences WHERE user_id=?", (user_id,)).fetchone()
    return {"theme": row[0] if row and row[0] in ("system", "light", "dark") else "system"}



@sites.route("/api/preferences", methods=("GET", "PUT"))
def preferences():
    if request.host != ACCOUNT_HOST:
        return jsonify(error="请在账户中心修改偏好设置。"), 403
    user = current_account()
    if not user:
        return jsonify(error="请先登录。"), 401
    store = current_app.extensions["registration_store"]
    account = store.find_account(user["username"])
    if request.method == "GET":
        return jsonify(_preferences(store, account["id"]))
    payload = request.get_json(silent=True)
    theme = payload.get("theme") if isinstance(payload, dict) else None
    if theme not in ("system", "light", "dark"):
        return jsonify(error="颜色模式无效。"), 422
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO user_preferences(user_id,theme) VALUES(?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET theme=excluded.theme",
            (account["id"], theme),
        )
    return jsonify(theme=theme)

