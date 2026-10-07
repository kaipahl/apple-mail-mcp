"""Tests for account resolution and mailbox listing against fixture databases.

The fixtures mirror the real schemas closely enough for the queries used:
Mail's Envelope Index (mailboxes, messages, addresses, subjects) and the
macOS Accounts database (ZACCOUNT).
"""

import sqlite3
from pathlib import Path

import pytest

from apple_mail_mcp.maildb import MailDatabase

KAI = "E1F5A47E-6D88-455D-ADC1-5682CA237370"
CLIENT = "52CD5383-F34B-4103-8354-3EE5CFFE2D18"
GOOGLE = "355503D9-D551-4DEA-BF7C-965B50F574FE"
REMOVED = "DEADBEEF-0000-0000-0000-000000000000"

# 2026-10-06T16:33:53Z as stored by Mail (Unix seconds)
RECEIVED = 1791304433


def _make_envelope_index(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE mailboxes (ROWID INTEGER PRIMARY KEY, url TEXT NOT NULL);
        CREATE TABLE addresses (ROWID INTEGER PRIMARY KEY, address TEXT, comment TEXT);
        CREATE TABLE subjects  (ROWID INTEGER PRIMARY KEY, subject TEXT);
        CREATE TABLE messages (
            ROWID INTEGER PRIMARY KEY, sender INTEGER, subject INTEGER NOT NULL,
            date_received INTEGER, mailbox INTEGER NOT NULL,
            read INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0
        );
    """)
    conn.executemany("INSERT INTO mailboxes VALUES (?, ?)", [
        (14, f"imap://{KAI}/INBOX"),
        (19, f"imap://{KAI}/INBOX/jobs"),
        (47, f"imap://{CLIENT}/INBOX"),
        (5, f"imap://{GOOGLE}/INBOX"),
        (90, f"imap://{REMOVED}/INBOX"),
        (4, "local://E086D866-6E80-43DE-BC53-1E1C77A487A6/Deleted%20Messages"),
    ])
    conn.execute("INSERT INTO addresses VALUES (1, 'a@example.com', 'A')")
    conn.execute("INSERT INTO subjects VALUES (1, 'Hello')")
    rows = (
        [(14, 1, 0)] * 3          # kai INBOX: 3 messages, all read
        + [(14, 0, 1)]            # deleted message, must not be counted
        + [(19, 1, 0)] * 2        # kai/jobs
        + [(47, 0, 0)] * 5        # client INBOX: 5 unread
        + [(90, 1, 0)]            # removed account, cached locally
    )
    conn.executemany(
        "INSERT INTO messages (sender, subject, date_received, mailbox, read, deleted)"
        " VALUES (1, 1, ?, ?, ?, ?)",
        [(RECEIVED, mb, read, deleted) for mb, read, deleted in rows],
    )
    conn.commit()
    conn.close()


def _make_accounts_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE ZACCOUNT (
            Z_PK INTEGER PRIMARY KEY, ZACTIVE INTEGER, ZPARENTACCOUNT INTEGER,
            ZACCOUNTDESCRIPTION VARCHAR, ZIDENTIFIER VARCHAR, ZUSERNAME VARCHAR
        );
    """)
    conn.executemany("INSERT INTO ZACCOUNT VALUES (?, ?, ?, ?, ?, ?)", [
        (1, 1, None, "kai@kaipahl.de", KAI, "kai@kaipahl.de"),
        (2, 1, None, "Client", CLIENT, "b@client.example"),
        (3, 1, None, "Google", "PARENT-GOOGLE", "kai@gmail.example"),
        # Mail child account of Google: no username of its own
        (4, 1, 3, "Google", GOOGLE, None),
    ])
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path: Path) -> MailDatabase:
    mail_data = tmp_path / "Mail" / "V10" / "MailData"
    mail_data.mkdir(parents=True)
    _make_envelope_index(mail_data / "Envelope Index")
    accounts = tmp_path / "Accounts4.sqlite"
    _make_accounts_db(accounts)
    return MailDatabase(mail_dir=str(tmp_path / "Mail"), accounts_db=str(accounts))


class TestAccountId:
    def test_imap_url(self):
        assert MailDatabase._account_id(f"imap://{KAI}/INBOX/jobs") == KAI

    def test_local_url(self):
        assert MailDatabase._account_id("local://X/Deleted%20Messages") == "On My Mac"

    def test_empty_url(self):
        assert MailDatabase._account_id("") is None


class TestListAccounts:
    def _by_id(self, db: MailDatabase) -> dict:
        return {a["id"]: a for a in db.list_accounts()}

    def test_resolves_names_and_emails(self, db: MailDatabase):
        kai = self._by_id(db)[KAI]
        assert kai["email"] == "kai@kaipahl.de"
        assert kai["active"] is True

    def test_reports_own_inbox_not_a_sum(self, db: MailDatabase):
        accounts = self._by_id(db)
        assert accounts[KAI]["inbox_id"] == 14
        assert accounts[KAI]["inbox_messages"] == 3
        assert accounts[KAI]["total_messages"] == 5
        assert accounts[CLIENT]["inbox_messages"] == 5

    def test_child_account_inherits_parent_username(self, db: MailDatabase):
        assert self._by_id(db)[GOOGLE]["email"] == "kai@gmail.example"

    def test_account_missing_from_accounts_db_is_inactive(self, db: MailDatabase):
        removed = self._by_id(db)[REMOVED]
        assert removed["active"] is False
        assert removed["inbox_messages"] == 1

    def test_unreadable_accounts_db_degrades_to_uuids(self, db: MailDatabase, tmp_path: Path):
        db.accounts_db = tmp_path / "missing.sqlite"
        kai = self._by_id(db)[KAI]
        assert kai["name"] == KAI
        assert kai["email"] is None


class TestListMailboxes:
    def test_mailboxes_carry_account(self, db: MailDatabase):
        mailboxes = {m["id"]: m for m in db.list_mailboxes()}
        assert mailboxes[14]["account"] == "kai@kaipahl.de"
        assert mailboxes[14]["account_id"] == KAI
        assert mailboxes[47]["account"] == "b@client.example"
        assert mailboxes[4]["account"] == "On My Mac"

    def test_excludes_deleted_from_counts(self, db: MailDatabase):
        inbox = next(m for m in db.list_mailboxes() if m["id"] == 14)
        assert inbox["total_messages"] == 3


class TestSearchDates:
    def test_dates_are_not_shifted(self, db: MailDatabase):
        results = db.search_emails(mailbox_id=14)
        assert results[0]["date"] == "2026-10-06T16:33:53+00:00"

    def test_date_to_is_inclusive(self, db: MailDatabase):
        assert len(db.search_emails(mailbox_id=14, date_to="2026-10-06")) == 3
        assert len(db.search_emails(mailbox_id=14, date_to="2026-10-05")) == 0

    def test_date_from(self, db: MailDatabase):
        assert len(db.search_emails(mailbox_id=14, date_from="2026-10-06")) == 3
        assert len(db.search_emails(mailbox_id=14, date_from="2026-10-07")) == 0
