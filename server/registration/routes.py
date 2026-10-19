"""HTTP adapters for page assets, health checks, and registration."""
from pathlib import Path
import sqlite3

from flask import Blueprint, current_app, jsonify, request, send_file

from .auth import HASH_METHOD, current_account
from werkzeug.security import generate_password_hash
from .repository import RegistrationAlreadyExists, RegistrationLimitReached
from .validation import InvalidRegistration, validate_registration
from .signup import create_verified_account

pages = Blueprint("pages", __name__)
api = Blueprint("registration", __name__)


def asset(name):
    return send_file(Path(current_app.config["REGISTRATION_ASSETS"]) / name)


@pages.get("/")
@pages.get("/register")
@pages.get("/login")
@pages.get("/account")
@pages.get("/account/privacy")
@pages.get("/verify")
def index():
    return asset("index.html")


@pages.get("/styles.css")
def stylesheet():
    return asset("styles.css")


@pages.get("/main.js")
def javascript():
    return asset("main.js")


@pages.get("/icons.js")
def icons():
    return asset("icons.js")


@pages.get("/mark.png")
def icon():
    return asset("brand-logo.png")


@pages.get("/favicon.ico")
@pages.get("/beta-favicon-v2.ico")
def favicon():
    return asset("beta-favicon-v2.ico")


@pages.get("/brand-logo.png")
def brand_logo():
    return asset("brand-logo-display.png")


@pages.get("/default-avatar.png")
def default_avatar():
    return asset("default-avatar.png")


@pages.get("/admin")
def administration():
    from .permissions import require_admin
    return require_admin(lambda actor: asset("admin.html"))()


@pages.get("/admin.js")
def administration_script():
    return asset("admin.js")


@pages.get("/admin.css")
def administration_styles():
    return asset("admin.css")


@pages.get("/privacy")
def privacy_policy():
    return asset("privacy.html")


@pages.get("/private.js")
def private_script():
    return asset("private.js")


@pages.get("/settings.js")
def settings_script():
    return asset("settings.js")


@api.get("/health")
def health():
    return jsonify(status="ok")


@api.post("/register")
def register():
    if request.mimetype != "application/json":
        return jsonify(error="请求格式无效。"), 415
    try:
        payload = request.get_json()
    except RecursionError:
        return jsonify(error="请填写用户名和邮箱。"), 400
    try:
        if isinstance(payload, dict) and "real_name" not in payload:
            raise InvalidRegistration("请填写姓名。")
        registration = validate_registration(payload)
    except InvalidRegistration as error:
        return jsonify(error=str(error)), error.status
    password = payload.get("password")
    if not isinstance(password, str) or not 15 <= len(password) <= 128:
        return jsonify(error="密码需要 15 至 128 个字符。"), 422
    if current_account():
        return jsonify(error="请先退出当前账户，再注册新账户。"), 409
    code = payload.get("email_code")
    if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
        return jsonify(error="请先获取并填写 6 位邮箱验证码。"), 422
    try:
        store = current_app.extensions["registration_store"]
        if not store.allow_auth_attempt(request.remote_addr, registration.email, action="register"):
            return jsonify(error="尝试次数过多，请 15 分钟后重试。"), 429
        password_hash = generate_password_hash(password, method=HASH_METHOD)
        if not create_verified_account(store, registration, password_hash, code):
            return jsonify(error="验证码不正确、已过期或尝试次数过多，请重新获取。"), 422
    except RegistrationLimitReached:
        return jsonify(error="登记数量已达上限。"), 503
    except RegistrationAlreadyExists:
        return jsonify(error="该用户名或邮箱已登记，请更换后重试。"), 409
    except sqlite3.Error:
        return jsonify(error="暂时无法保存，请稍后重试。"), 503
    return jsonify(real_name=registration.real_name, nickname=registration.nickname, email=registration.email,
                   verification_required=False), 201
