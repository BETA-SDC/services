"""Explicit host-local administrator bootstrap; never auto-promote an account."""
import argparse

from server.registration import create_app
from server.registration.permissions import audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--database")
    args = parser.parse_args()
    app = create_app(args.database)
    store = app.extensions["registration_store"]
    account = store.find_account(args.username)
    if not account or account["username"].casefold() != args.username.casefold() or not account["email_verified_at"]:
        parser.error("A verified, exact local username is required")
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT OR IGNORE INTO account_roles VALUES (?,'admin')", (account["id"],))
        audit(connection, account["id"], "role.bootstrap.host", account["id"])
    print("Administrator role granted to the specified verified account.")


if __name__ == "__main__":
    main()
