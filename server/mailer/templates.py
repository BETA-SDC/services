"""Mail delivery: templates."""
from email.message import EmailMessage
from email.utils import formatdate
import hashlib
from .config import DOMAIN, SENDER


def base_message(recipient, subject):
    message = EmailMessage()
    message["From"] = f"BETA SDC Service <{SENDER}>"
    message["To"] = recipient
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False)
    return message


def make_message(recipient, token):
    message = base_message(recipient, "BETA · 验证你的邮箱")
    message["Message-ID"] = f"<verify-{hashlib.sha256(token.encode()).hexdigest()}@163.com>"
    link = f"https://{DOMAIN}/verify#token={token}"
    message.set_content(
        "你好！\n\n请连接校园网，打开下方链接，再点击“确认验证邮箱”：\n\n"
        f"{link}\n\n链接自申请起 30 分钟内有效，且只能使用一次。"
        "重新发送邮件后，请使用最新链接。\n\n"
        "如果你没有注册 BETA 账户，请忽略此邮件，不要转发验证链接。\n\nBETA\n"
    )
    return message



def make_signup_message(recipient, code):
    message = base_message(recipient, "BETA · 注册邮箱验证码")
    message.set_content(
        f"你好！\n\n你的 BETA 注册验证码是：{code}\n\n"
        "请在发起注册的浏览器中填写，10 分钟内有效。请勿向他人透露验证码。\n"
        "完成邮箱验证并提交注册后，才会创建账户。\n\n"
        "如果你没有申请注册，请忽略此邮件。\n\nBETA\n"
    )
    return message



def make_second_factor_message(recipient, code):
    message = base_message(recipient, "BETA · 账户二次验证")
    message.set_content(
        f"你的 BETA 账户二次验证码是：{code}\n\n"
        "请在已登录且发起本次验证的浏览器中填写，5 分钟内有效，仅能使用一次。\n"
        "本次验证用于查看或修改隐私资料、管理通行密钥。不要将验证码告诉任何人。\n"
        "如非本人操作，请勿提供验证码，并检查账户登录安全。\n\nBETA SDC Service\n"
    )
    return message

