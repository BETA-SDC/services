"""Conservative document syntax checks, not identity verification."""
from datetime import date
import re

from .validation import InvalidRegistration

DOCUMENTS = {
    "cn_id": "中国大陆居民身份证",
    "hmt_residence": "港澳台居民居住证",
    "hk_id": "香港居民身份证",
    "macao_id": "澳门居民身份证",
    "tw_id": "台湾身份证／新式居留证",
    "hk_macao_pass": "港澳居民来往内地通行证",
    "tw_pass": "台湾居民来往大陆通行证",
    "passport": "护照",
    "foreign_pr": "外国人永久居留身份证（18位）",
}
FIELDS = ("student_id", "phone", "document")
CN_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
TW_CODES = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                    (10, 11, 12, 13, 14, 15, 16, 17, 34, 18, 19, 20, 21,
                     22, 35, 23, 24, 25, 26, 27, 28, 29, 32, 30, 31, 33)))


def invalid(message):
    raise InvalidRegistration(message)


def validate_document(value):
    if not isinstance(value, dict) or set(value) != {"type", "number", "country"}:
        invalid("请填写证件类型、签发国家或地区及证件号码。")
    kind, number, country = (value[key] for key in ("type", "number", "country"))
    if not all(isinstance(item, str) for item in (kind, number, country)):
        invalid("证件信息格式无效。")
    number, country = number.strip().upper(), country.strip().upper()
    if kind not in DOCUMENTS or not re.fullmatch(r"[A-Z]{2}", country):
        invalid("请选择证件类型，并填写两位国家或地区代码。")
    checksum = False
    if kind in ("cn_id", "hmt_residence", "foreign_pr"):
        if not re.fullmatch(r"[0-9]{17}[0-9X]", number):
            invalid("此类证件号码应为 18 位，末位可为 X。")
        if kind == "hmt_residence" and number[:6] not in ("810000", "820000", "830000"):
            invalid("港澳台居民居住证应以 810000、820000 或 830000 开头。")
        if kind == "cn_id":
            if number[:2] not in {f"{i:02}" for i in (
                    11, 12, 13, 14, 15, 21, 22, 23, 31, 32, 33, 34, 35, 36, 37,
                    41, 42, 43, 44, 45, 46, 50, 51, 52, 53, 54, 61, 62, 63, 64, 65)}:
                invalid("居民身份证地区代码格式无效。")
        if kind != "foreign_pr":
            try:
                birthday = date(int(number[6:10]), int(number[10:12]), int(number[12:14]))
                if not date(1800, 1, 1) <= birthday <= date.today() or number[14:17] == "000":
                    raise ValueError()
            except ValueError:
                invalid("证件号码中的出生日期或顺序码无效。")
            if "10X98765432"[sum(int(n) * w for n, w in zip(number[:17], CN_WEIGHTS)) % 11] != number[-1]:
                invalid("证件号码校验位不正确，请核对。")
            checksum = True
        # Foreign permanent resident numbering differs by card generation.
    elif kind == "hk_id":
        match = re.fullmatch(r"([A-Z]{1,2})([0-9]{6})\(?([0-9A])\)?", number)
        if not match or ("(" in number) != (")" in number):
            invalid("香港身份证格式应为 A123456(7) 或 AB123456(7)。")
        letters, digits, check = match.groups()
        values = ([36] if len(letters) == 1 else []) + [ord(c) - 55 for c in letters]
        values += [int(c) for c in digits] + [10 if check == "A" else int(check)]
        if sum(a * b for a, b in zip(values, range(9, 0, -1))) % 11:
            invalid("香港身份证校验位不正确。")
        number = letters + digits + check
        checksum = True
    elif kind == "tw_id":
        if not re.fullmatch(r"[A-Z][1289][0-9]{8}", number):
            invalid("台湾身份证或新式居留证应为字母、1/2/8/9 和 8 位数字。")
        code = TW_CODES[number[0]]
        total = code // 10 + code % 10 * 9
        total += sum(int(c) * weight for c, weight in zip(number[1:], (8, 7, 6, 5, 4, 3, 2, 1, 1)))
        if total % 10:
            invalid("台湾证件校验位不正确。")
        checksum = True
    else:
        patterns = {"macao_id": r"[157][0-9]{7}", "hk_macao_pass": r"[HM][0-9]{8}",
                    "tw_pass": r"[0-9]{8}", "passport": r"[A-Z0-9]{6,20}"}
        if not re.fullmatch(patterns[kind], number):
            invalid("证件号码长度或格式不符合所选证件类型。")
    return {"type": kind, "number": number, "country": country,
            "validation": "checksum" if checksum else "format_only"}


def validate_private(payload):
    if not isinstance(payload, dict) or not payload or set(payload) - set(FIELDS):
        invalid("请选择需要保存的资料字段。")
    result = {}
    for field, value in payload.items():
        if value in ("", None):
            result[field] = None
        elif field == "document":
            result[field] = validate_document(value)
        elif not isinstance(value, str):
            invalid("资料格式无效。")
        elif field == "student_id":
            value = value.strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{2,31}", value):
                invalid("学号应为 3 至 32 位字母、数字或连字符；不代表已通过学校核验。")
            result[field] = value
        else:
            value = value.strip()
            if not re.fullmatch(r"1[3-9][0-9]{9}", value):
                invalid("请填写中国大陆 11 位手机号码，无需 +86。")
            result[field] = value
    return result
