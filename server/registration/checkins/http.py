"""Attendance blueprints, request guards, errors and shared request parsing."""
from flask import Blueprint, abort, jsonify, request
from werkzeug.exceptions import HTTPException

from ..sites import ACCOUNT_HOST
from ..validation import InvalidRegistration
from .repository import purge_expired, store


checkin = Blueprint("checkin", __name__)


access = Blueprint("checkin_access", __name__)


def payload():
    value = request.get_json()
    if not isinstance(value, dict):
        abort(422, description="请求内容应为对象。")
    return value


def page_number(maximum=100000):
    try:
        return max(1, min(maximum, int(request.args.get("page", "1"))))
    except ValueError:
        abort(422, description="页码无效。")


@checkin.before_request
def protect_checkin():
    if request.host != ACCOUNT_HOST:
        abort(404)
    request.max_content_length = 32768
    if request.endpoint in {"checkin.checkin_module", "checkin.checkin_script", "checkin.checkin_styles"}:
        return
    with store().connection() as connection:
        purge_expired(connection)


@checkin.errorhandler(401)
@checkin.errorhandler(403)
@checkin.errorhandler(404)
@checkin.errorhandler(409)
@checkin.errorhandler(422)
@checkin.errorhandler(429)
@checkin.errorhandler(503)
def checkin_error(error):
    return jsonify(error=error.description), error.code


@checkin.errorhandler(InvalidRegistration)
def invalid_answer(error):
    return jsonify(error=str(error)), 422


@access.before_request
def protect_access():
    return protect_checkin()


@access.errorhandler(HTTPException)
def access_error(error):
    return jsonify(error=error.description), error.code


@access.errorhandler(InvalidRegistration)
def invalid_access(error):
    return jsonify(error=str(error)), 422
