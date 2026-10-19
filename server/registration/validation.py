"""Registration input validation without HTTP or database dependencies."""
from dataclasses import dataclass
import re
import secrets
import unicodedata

USERNAME = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff]{2,24}")
EMAIL = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
)
EMAIL_DOMAIN = "westlake.edu.cn"


class InvalidRegistration(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RegistrationInput:
    username: str
    email: str
    real_name: str = ""
    nickname: str = ""


def validate_profile(real_name, nickname=""):
    if not isinstance(real_name, str) or not isinstance(nickname, str):
        raise InvalidRegistration("请填写姓名和有效的昵称。")
    if (not 1 <= len(real_name.strip()) <= 80
            or not any(unicodedata.category(char).startswith("L") for char in real_name)
            or any(unicodedata.category(char)[0] not in ("L", "M")
                   and char not in " -.'·" for char in real_name)):
        raise InvalidRegistration("请填写有效的姓名，最多 80 个字符。")
    if (len(nickname.strip()) > 40
            or any(unicodedata.category(char).startswith("C") or not char.isprintable()
                   for char in nickname)):
        raise InvalidRegistration("昵称最多 40 个字符，不能包含控制字符。")
    return " ".join(real_name.split()), nickname.strip()


def validate_registration(payload):
    if not isinstance(payload, dict):
        raise InvalidRegistration("请填写用户名和邮箱。", 400)
    username, email = payload.get("username"), payload.get("email")
    real_name, nickname = "", ""
    if "real_name" in payload:
        real_name, nickname = validate_profile(payload["real_name"], payload.get("nickname", ""))
        if username is None:
            username = "u_" + secrets.token_hex(10)
    if not isinstance(username, str) or not isinstance(email, str):
        raise InvalidRegistration("请填写用户名和邮箱。", 400)
    username, email = username.strip(), email.strip().lower()
    if not USERNAME.fullmatch(username):
        raise InvalidRegistration("用户名格式无效。")
    if (len(email) > 254 or not EMAIL.fullmatch(email)
            or ".." in email.split("@")[0]):
        raise InvalidRegistration("请填写有效的邮箱地址。")
    local, domain = email.rsplit("@", 1)
    if len(local) > 64 or local.startswith(".") or local.endswith("."):
        raise InvalidRegistration("请填写有效的邮箱地址。")
    if domain != EMAIL_DOMAIN:
        raise InvalidRegistration("仅允许使用 @westlake.edu.cn 邮箱注册。")
    return RegistrationInput(username, email, real_name, nickname)
