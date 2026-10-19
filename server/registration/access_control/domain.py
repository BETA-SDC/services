"""Account service access_control: domain."""
import calendar
from datetime import datetime
from zoneinfo import ZoneInfo


ORG = "BETA-SDC"



MEMBERSHIP_LIFETIME = 86400



def owner_expiry(verified_at):
    verified = datetime.fromtimestamp(verified_at, ZoneInfo("Asia/Shanghai"))
    month_index = verified.year * 12 + verified.month - 1 + 6
    year, month = divmod(month_index, 12)
    month += 1
    day = min(verified.day, calendar.monthrange(year, month)[1])
    return int(verified.replace(year=year, month=month, day=day).timestamp())

