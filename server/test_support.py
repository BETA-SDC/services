"""Synthetic email challenge fixture; never connects to SMTP."""
def signup_payload(client, headers, store, payload):
    response = client.post("/api/signup/code", headers=headers, json={"email": payload["email"]})
    code = "000000"
    if response.status_code in (200, 429):
        with client.session_transaction(base_url="https://" + headers["Host"]) as session:
            challenge = session.get("signup_challenge", "")
        with store.connection() as connection:
            row = connection.execute("SELECT code FROM signup_mail_outbox WHERE challenge_hash=?",
                                     (store.token_hash(challenge),)).fetchone()
            if row:
                code = row[0]
    return dict({"real_name": "测试成员"}, **payload, email_code=code)
