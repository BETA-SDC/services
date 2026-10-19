"""Account service privacy: schema."""



def initialize_private(connection):
    statements = """
        CREATE TABLE IF NOT EXISTS private_fields(
            user_id INTEGER NOT NULL, field TEXT NOT NULL, ciphertext TEXT NOT NULL,
            updated_at INTEGER NOT NULL, PRIMARY KEY(user_id,field));
        CREATE TABLE IF NOT EXISTS private_consents(
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, session_hash TEXT NOT NULL,
            purpose TEXT NOT NULL, fields_json TEXT NOT NULL, policy TEXT NOT NULL,
            created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, consumed_at INTEGER);
        CREATE TABLE IF NOT EXISTS private_audit(
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, action TEXT NOT NULL,
            fields_json TEXT NOT NULL, policy TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS passkeys(
            credential_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
            credential_data TEXT NOT NULL, sign_count INTEGER NOT NULL,
            created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS passkey_flows(
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, session_hash TEXT NOT NULL,
            kind TEXT NOT NULL, state_json TEXT NOT NULL, context_json TEXT NOT NULL,
            expires_at INTEGER NOT NULL);
    """
    for statement in statements.split(";"):
        if statement.strip():
            connection.execute(statement)

