"""Account service site_accounts: schema."""
from urllib.parse import quote
from .domain import PROFILE_HOST


def initialize_sites(connection):
    for statement in (
        """CREATE TABLE IF NOT EXISTS site_sessions(
            token_hash TEXT PRIMARY KEY,site_host TEXT NOT NULL,parent_hash TEXT NOT NULL DEFAULT '')""",
        """CREATE TABLE IF NOT EXISTS sso_flows(
            state_hash TEXT PRIMARY KEY,nonce_hash TEXT NOT NULL,target_host TEXT NOT NULL,
            next_path TEXT NOT NULL,required INTEGER NOT NULL,expires_at INTEGER NOT NULL,
            ticket_hash TEXT NOT NULL DEFAULT '',parent_hash TEXT NOT NULL DEFAULT '',
            user_id INTEGER,auth_time INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS public_profiles(
            user_id INTEGER PRIMARY KEY,slug TEXT UNIQUE COLLATE NOCASE NOT NULL,bio TEXT NOT NULL DEFAULT '')""",
        """CREATE TABLE IF NOT EXISTS profile_aliases(
            slug TEXT PRIMARY KEY COLLATE NOCASE,user_id INTEGER NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS profile_requests(
            id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,slug TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',created_at INTEGER NOT NULL,reviewed_at INTEGER,reviewer_id INTEGER)""",
        """CREATE TABLE IF NOT EXISTS user_preferences(
            user_id INTEGER PRIMARY KEY,theme TEXT NOT NULL DEFAULT 'system')""",
    ):
        connection.execute(statement)
    for user_id, registration_number in connection.execute("SELECT id,username FROM registrations"):
        initialize_profile(connection, user_id, registration_number)



def initialize_profile(connection, user_id, registration_number):
    """Use the immutable system registration number as the default profile path."""
    connection.execute("INSERT INTO public_profiles(user_id,slug) VALUES(?,?) ON CONFLICT(user_id) DO NOTHING",
                       (user_id, registration_number))
    connection.execute("INSERT INTO profile_aliases(slug,user_id) VALUES(?,?) ON CONFLICT(slug) DO NOTHING",
                       (registration_number, user_id))



def own_profile(connection, user):
    row = connection.execute("SELECT slug,bio FROM public_profiles WHERE user_id=?", (user["id"],)).fetchone()
    if not row:
        initialize_profile(connection, user["id"], user["username"])
        row = (user["username"], "")
    latest = connection.execute("SELECT slug,state FROM profile_requests WHERE user_id=? ORDER BY id DESC LIMIT 1",
                                (user["id"],)).fetchone()
    return {"slug": row[0], "bio": row[1], "url": f"https://{PROFILE_HOST}/" + quote(row[0], safe=""),
            "request": {"slug": latest[0], "state": latest[1]} if latest else None}

