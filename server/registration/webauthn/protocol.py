"""Account service webauthn: protocol."""
import hashlib
import hmac
from fido2.server import Fido2Server
from fido2.utils import websafe_decode
from fido2.webauthn import AttestedCredentialData, PublicKeyCredentialRpEntity
from flask import current_app


def user_handle(user_id):
    return hmac.new(str(current_app.secret_key).encode(), f"passkey:{user_id}".encode(),
                    hashlib.sha256).digest()



def server():
    domain = current_app.config["REGISTRATION_DOMAIN"]
    result = Fido2Server(PublicKeyCredentialRpEntity(id=domain, name="BETA Account"),
                        verify_origin=lambda origin: origin == "https://" + domain)
    result.timeout = 60000
    return result



def credentials(connection, user):
    return [AttestedCredentialData(websafe_decode(row[0])) for row in connection.execute(
        "SELECT credential_data FROM passkeys WHERE user_id=?", (user["id"],))]

