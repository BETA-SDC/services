"""Encrypted system-mail notifications shared by attendance workflows."""
import sqlite3

from flask import abort, current_app

from ..access_control.policy import is_admin
from server.system_mail import queue_message


def notify(connection, user, kind, subject, body, **options):
    return queue_message(connection, current_app.extensions["private_cipher"], user, kind, subject, body, **options)


def notify_admins(connection, kind, subject, body):
    connection.row_factory = sqlite3.Row
    recipients = [dict(row) for row in connection.execute(
        "SELECT id,email FROM registrations WHERE email_verified_at IS NOT NULL").fetchall()]
    admins = [user for user in recipients if is_admin(connection, user["id"])]
    if not admins:
        abort(503, description="暂无可接收系统邮件的管理员，请联系维护人员。")
    for user in admins:
        notify(connection, user, kind, subject, body)
