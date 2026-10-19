"""Idempotent initialization of attendance, approval and transient-grant tables."""
from .domain import page_slug
from server.system_mail import initialize_system_mail


def initialize_checkin(connection):
    initialize_access(connection)
    for statement in (
        """CREATE TABLE IF NOT EXISTS checkin_events(
            id TEXT PRIMARY KEY,creator_id INTEGER NOT NULL,title TEXT NOT NULL,
            location TEXT NOT NULL,purpose TEXT NOT NULL,fields_json TEXT NOT NULL,
            starts_at INTEGER NOT NULL,ends_at INTEGER NOT NULL,created_at INTEGER NOT NULL,
            closed INTEGER NOT NULL DEFAULT 0,token_hash TEXT UNIQUE NOT NULL,token_cipher TEXT NOT NULL,
            retain_until INTEGER NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS checkin_entries(
            id TEXT PRIMARY KEY,event_id TEXT NOT NULL,user_id INTEGER,ciphertext TEXT NOT NULL,
            checked_at INTEGER NOT NULL,policy TEXT NOT NULL)""",
        """CREATE UNIQUE INDEX IF NOT EXISTS checkin_user_once
            ON checkin_entries(event_id,user_id) WHERE user_id IS NOT NULL""",
        """CREATE INDEX IF NOT EXISTS checkin_event_entries ON checkin_entries(event_id,checked_at)""",
        """CREATE TABLE IF NOT EXISTS checkin_identities(
            event_id TEXT NOT NULL,kind TEXT NOT NULL,digest TEXT NOT NULL,entry_id TEXT NOT NULL,
            PRIMARY KEY(event_id,kind,digest))""",
        """CREATE TABLE IF NOT EXISTS checkin_grants(
            token_hash TEXT PRIMARY KEY,event_id TEXT NOT NULL,user_id INTEGER NOT NULL,
            session_hash TEXT NOT NULL,expires_at INTEGER NOT NULL,consumed_at INTEGER)""",
        """CREATE TABLE IF NOT EXISTS checkin_options(
            event_id TEXT PRIMARY KEY,calendar_uid TEXT NOT NULL,custom_json TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS checkin_deletion_requests(
            id INTEGER PRIMARY KEY,event_id TEXT NOT NULL,event_title TEXT NOT NULL,
            requester_id INTEGER NOT NULL,reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
            created_at INTEGER NOT NULL,reviewer_id INTEGER,reviewed_at INTEGER,note TEXT NOT NULL DEFAULT '')""",
        """CREATE UNIQUE INDEX IF NOT EXISTS checkin_pending_delete
            ON checkin_deletion_requests(event_id) WHERE status='pending'""",
        """CREATE TABLE IF NOT EXISTS checkin_pages(
            event_id TEXT PRIMARY KEY,slug TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            visibility TEXT NOT NULL DEFAULT 'public'
            CHECK(visibility IN ('public','members','private')))""",
    ):
        connection.execute(statement)
    for event_id, title in connection.execute(
            "SELECT id,title FROM checkin_events WHERE id NOT IN (SELECT event_id FROM checkin_pages)").fetchall():
        connection.execute("INSERT INTO checkin_pages(event_id,slug) VALUES(?,?)",
                           (event_id, page_slug(title, event_id)))


def initialize_access(connection):
    initialize_system_mail(connection)
    for statement in (
        """CREATE TABLE IF NOT EXISTS checkin_access_requests(
            id INTEGER PRIMARY KEY,event_id TEXT NOT NULL,event_title TEXT NOT NULL,
            applicant_id INTEGER NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','approved','rejected')),
            created_at INTEGER NOT NULL,reviewer_id INTEGER,reviewed_at INTEGER,note TEXT NOT NULL DEFAULT '')""",
        """CREATE UNIQUE INDEX IF NOT EXISTS checkin_pending_access
            ON checkin_access_requests(event_id,applicant_id) WHERE status='pending'""",
        """CREATE TABLE IF NOT EXISTS checkin_access_grants(
            request_id INTEGER PRIMARY KEY,event_id TEXT NOT NULL,user_id INTEGER NOT NULL,
            email TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL,issued_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,revoked_at INTEGER,allow_export INTEGER NOT NULL DEFAULT 0,
            mailed_at INTEGER)""",
    ):
        connection.execute(statement)
