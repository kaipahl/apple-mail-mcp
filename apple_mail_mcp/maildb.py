"""
Read-only access to Apple Mail's local SQLite database and .emlx files.
Designed for large mailboxes (100K+ messages).
"""

import email
import email.policy
import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from email.header import decode_header
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

logger = logging.getLogger(__name__)

# Mail's Envelope Index stores date_received / date_sent as Unix epoch
# seconds (not Core Data's 2001-based epoch).

# Status of each macOS account (name, address, active) lives in the system
# Accounts database, keyed by the UUID that appears in mailbox URLs.
_ACCOUNTS_DB = Path.home() / "Library" / "Accounts" / "Accounts4.sqlite"
_LOCAL_ACCOUNT = "On My Mac"

_EMLX_SUFFIXES = (".emlx", ".partial.emlx")
_EXTRA_HEADERS = ("to", "cc", "reply-to")

_MESSAGE_SELECT = """
    SELECT
        m.ROWID                        AS id,
        COALESCE(addr.address, '')      AS sender_address,
        COALESCE(addr.comment, '')      AS sender_name,
        COALESCE(subj.subject, '(no subject)') AS subject,
        m.date_received,
        COALESCE(mb.url, '')            AS mailbox_url,
        m.read,
        m.mailbox                       AS mailbox_id
    FROM messages m
    LEFT JOIN addresses addr ON m.sender   = addr.ROWID
    LEFT JOIN subjects  subj ON m.subject  = subj.ROWID
    LEFT JOIN mailboxes mb   ON m.mailbox  = mb.ROWID"""

# Pre-compiled regexes for HTML-to-text stripping
_RE_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_RE_BLOCK_CLOSE = re.compile(r"</(p|div|tr|li)>", re.IGNORECASE)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_MULTI_NEWLINE = re.compile(r"\n{3,}")


def _escape_like(value: str) -> str:
    """Escape LIKE special characters so they match literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class MailDatabase:
    """Read-only interface to Apple Mail's local database."""

    def __init__(
        self, mail_dir: Optional[str] = None, accounts_db: Optional[str] = None
    ):
        self.mail_dir = Path(mail_dir) if mail_dir else Path.home() / "Library" / "Mail"
        self.accounts_db = Path(accounts_db) if accounts_db else _ACCOUNTS_DB
        self.v10_dir = self.mail_dir / "V10"
        self.db_path = self.v10_dir / "MailData" / "Envelope Index"

        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Mail database not found at {self.db_path}. "
                "Make sure Apple Mail is configured."
            )

        # Cache list of Messages/ directories for fast .emlx lookup
        self._messages_dirs: list[Path] = []
        self._scan_messages_dirs()

    def _scan_messages_dirs(self) -> None:
        """Find all Messages/ directories under V10 (excluding MailData).

        Handles both on-disk layouts Apple Mail has used:

        - ``<mailbox>.mbox/Messages`` (older layout)
        - ``<mailbox>.mbox/<uuid>/Data/<n>/<n>/<n>/Messages`` (current
          Mail, where the digits encode the .emlx file number)

        This is run once at startup. Finds one directory per active
        Data/ shard - usually dozens to a few thousand, regardless of
        how many emails exist.
        """
        self._messages_dirs = []
        if not self.v10_dir.exists():
            return
        for mbox_dir in self.v10_dir.rglob("*.mbox"):
            # Old layout: Messages/ directly inside the .mbox bundle.
            direct = mbox_dir / "Messages"
            if direct.is_dir():
                self._messages_dirs.append(direct)
            # Current layout: nested under <uuid>/Data/<n>/<n>/<n>/.
            # Globbing the fixed depth avoids walking the message files
            # themselves, keeping startup fast on large mailboxes.
            for data_dir in mbox_dir.glob("*/Data"):
                for messages_dir in data_dir.glob("*/*/*/Messages"):
                    if messages_dir.is_dir():
                        self._messages_dirs.append(messages_dir)
        logger.info("Found %d mailbox message directories", len(self._messages_dirs))

    def _connect(self) -> sqlite3.Connection:
        """Open a read-only connection to the mail database."""
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _unix_to_iso(timestamp: Optional[float]) -> Optional[str]:
        """Convert a Unix timestamp to an ISO 8601 string."""
        if timestamp is None:
            return None
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            return None

    @staticmethod
    def _iso_to_unix(iso_str: str, end_of_day: bool = False) -> Optional[float]:
        """Convert an ISO date string to a Unix timestamp.

        With ``end_of_day``, a date without time ("2025-12-31") resolves to
        the last second of that day, so ``date_to`` is inclusive.
        """
        try:
            dt = datetime.fromisoformat(iso_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.timestamp()
            if end_of_day and len(iso_str) == 10:
                ts += 86399
            return ts
        except ValueError:
            return None

    @staticmethod
    def _decode_mime_header(value: str) -> str:
        """Decode MIME-encoded header value."""
        if not value:
            return ""
        try:
            parts = decode_header(value)
            decoded = []
            for part, encoding in parts:
                if isinstance(part, bytes):
                    decoded.append(
                        part.decode(encoding or "utf-8", errors="replace")
                    )
                else:
                    decoded.append(str(part))
            return "".join(decoded)
        except Exception:
            return str(value)

    @staticmethod
    def _mailbox_display_name(url: str) -> str:
        """Extract a human-readable mailbox name from its URL."""
        if not url:
            return "Unknown"
        if "/" in url:
            return unquote(url.split("/")[-1]).replace(".mbox", "")
        return url

    @staticmethod
    def _account_id(url: str) -> Optional[str]:
        """Extract the account UUID from a mailbox URL.

        ``imap://<uuid>/INBOX`` → ``<uuid>``; ``local://…`` → "On My Mac".
        """
        parts = urlsplit(url or "")
        if parts.scheme == "local":
            return _LOCAL_ACCOUNT
        return parts.netloc or None

    def _load_accounts(self) -> dict[str, dict[str, Any]]:
        """Map account UUIDs to name, address and active state.

        Reads the macOS Accounts database. Child accounts (e.g. the mail
        part of a Google account) inherit the parent's username. Returns
        an empty dict if the database is unreadable, so callers degrade
        to bare UUIDs instead of failing.
        """
        try:
            conn = sqlite3.connect(f"file:{self.accounts_db}?mode=ro", uri=True)
        except sqlite3.Error:
            return {}
        try:
            with closing(conn):
                rows = conn.execute("""
                    SELECT a.ZIDENTIFIER,
                           a.ZACCOUNTDESCRIPTION,
                           COALESCE(a.ZUSERNAME, p.ZUSERNAME),
                           a.ZACTIVE
                    FROM ZACCOUNT a
                    LEFT JOIN ZACCOUNT p ON a.ZPARENTACCOUNT = p.Z_PK
                    WHERE a.ZIDENTIFIER IS NOT NULL
                """).fetchall()
        except sqlite3.Error as exc:
            logger.warning("Could not read accounts database: %s", exc)
            return {}
        return {
            ident: {"name": name, "email": username, "active": bool(active)}
            for ident, name, username, active in rows
        }

    def _format_sender(self, name: str, address: str) -> str:
        """Format sender for display."""
        decoded_name = self._decode_mime_header(name)
        if decoded_name:
            return f"{decoded_name} <{address}>" if address else decoded_name
        return address or "Unknown"

    def _row_to_summary(self, row: sqlite3.Row) -> dict[str, Any]:
        """Convert a message SQL row to a summary dict."""
        return {
            "id": row["id"],
            "sender": self._format_sender(
                row["sender_name"], row["sender_address"]
            ),
            "subject": self._decode_mime_header(row["subject"]),
            "date": self._unix_to_iso(row["date_received"]),
            "mailbox": self._mailbox_display_name(row["mailbox_url"]),
            "mailbox_id": row["mailbox_id"],
            "read": bool(row["read"]),
        }

    def _find_emlx_path(self, message_id: int) -> Optional[Path]:
        """Locate the .emlx file for a message across cached directories."""
        for messages_dir in self._messages_dirs:
            for suffix in _EMLX_SUFFIXES:
                candidate = messages_dir / f"{message_id}{suffix}"
                if candidate.exists():
                    return candidate
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_accounts(self) -> list[dict[str, Any]]:
        """List mail accounts that own at least one mailbox.

        Each entry carries the account id (UUID from the mailbox URLs),
        display name, email address, active state, and its INBOX id and
        message count so callers need not guess which INBOX is whose.
        """
        known = self._load_accounts()
        accounts: dict[str, dict[str, Any]] = {}
        for mb in self.list_mailboxes():
            acct_id = mb["account_id"]
            if acct_id is None:
                continue
            acct = accounts.setdefault(acct_id, {
                **self._account_info(acct_id, known),
                "inbox_id": None,
                "inbox_messages": 0,
                "total_messages": 0,
            })
            acct["total_messages"] += mb["total_messages"]
            if mb["name"].upper() == "INBOX":
                acct["inbox_id"] = mb["id"]
                acct["inbox_messages"] = mb["total_messages"]
        return sorted(accounts.values(), key=lambda a: a["name"].lower())

    @staticmethod
    def _account_info(
        acct_id: str, known: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        if acct_id == _LOCAL_ACCOUNT:
            return {"id": acct_id, "name": acct_id, "email": None, "active": True}
        info = known.get(acct_id)
        if info is None:
            # Not in the Accounts DB: removed account whose mail is still cached.
            return {"id": acct_id, "name": acct_id, "email": None, "active": False}
        return {"id": acct_id, **info, "name": info["name"] or acct_id}

    def list_mailboxes(self) -> list[dict[str, Any]]:
        """List all mailboxes with owning account, message and unread counts."""
        known = self._load_accounts()
        with closing(self._connect()) as conn:
            cursor = conn.execute("""
                SELECT
                    mb.ROWID   AS id,
                    mb.url,
                    COUNT(m.ROWID) AS total_messages,
                    SUM(CASE WHEN m.read = 0 AND m.deleted = 0 THEN 1 ELSE 0 END)
                        AS unread_count
                FROM mailboxes mb
                LEFT JOIN messages m
                    ON m.mailbox = mb.ROWID AND m.deleted = 0
                GROUP BY mb.ROWID, mb.url
                ORDER BY total_messages DESC
            """)
            return [
                {
                    "id": row["id"],
                    "name": self._mailbox_display_name(row["url"]),
                    "account_id": self._account_id(row["url"]),
                    "account": self._account_label(row["url"], known),
                    "url": row["url"] or "",
                    "total_messages": row["total_messages"],
                    "unread": row["unread_count"] or 0,
                }
                for row in cursor
            ]

    def _account_label(self, url: str, known: dict[str, dict[str, Any]]) -> Optional[str]:
        acct_id = self._account_id(url)
        if acct_id is None:
            return None
        info = self._account_info(acct_id, known)
        return info["email"] or info["name"]

    # ------------------------------------------------------------------
    # Body search via .emlx file scanning
    # ------------------------------------------------------------------

    def _body_search(
        self,
        body_text: str,
        candidate_ids: list[int],
        max_matches: int,
    ) -> list[int]:
        """Filter candidate message IDs by body content.

        Scans .emlx files for candidates and returns IDs whose body
        contains the search text (case-insensitive).  Stops after
        collecting ``max_matches`` hits.
        """
        search_lower = body_text.lower()
        matched: list[int] = []
        for msg_id in candidate_ids:
            path = self._find_emlx_path(msg_id)
            if path is None:
                continue
            body, _ = self._parse_emlx(path)
            if search_lower in body.lower():
                matched.append(msg_id)
                if len(matched) >= max_matches:
                    break
        return matched

    def search_emails(
        self,
        *,
        query: Optional[str] = None,
        sender: Optional[str] = None,
        subject: Optional[str] = None,
        body: Optional[str] = None,
        mailbox_id: Optional[int] = None,
        unread_only: bool = False,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search emails with SQL-level filtering.

        All filters are AND-combined.  Returns metadata only — use
        ``read_email`` to fetch the full body.

        When ``body`` is provided, the search works in two passes:
        1. SQL filters narrow down candidates (sender, subject, date, etc.)
        2. .emlx files of candidates are scanned for the body text.

        If ``body`` is the *only* filter, the most recent 5000 messages
        are scanned.  Combine with other filters for faster results.

        Args:
            query:       Free-text across sender and subject.
            sender:      Substring match on sender address / display name.
            subject:     Substring match on subject line.
            body:        Full-text search in message body (scans .emlx files).
            mailbox_id:  Restrict to a specific mailbox (from list_mailboxes).
            unread_only: If True, only unread messages.
            date_from:   ISO date string, inclusive lower bound.
            date_to:     ISO date string, inclusive upper bound.
            limit:       Max results (default 20, capped at 200).
            offset:      Pagination offset.
        """
        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        conditions = ["m.deleted = 0"]
        params: list[Any] = []

        if unread_only:
            conditions.append("m.read = 0")

        if mailbox_id is not None:
            conditions.append("m.mailbox = ?")
            params.append(mailbox_id)

        if sender:
            conditions.append("(addr.address LIKE ? ESCAPE '\\' OR addr.comment LIKE ? ESCAPE '\\')")
            like = f"%{_escape_like(sender)}%"
            params.extend([like, like])

        if subject:
            conditions.append("subj.subject LIKE ? ESCAPE '\\'")
            params.append(f"%{_escape_like(subject)}%")

        if query:
            conditions.append(
                "(addr.address LIKE ? ESCAPE '\\'"
                " OR addr.comment LIKE ? ESCAPE '\\'"
                " OR subj.subject LIKE ? ESCAPE '\\')"
            )
            like_q = f"%{_escape_like(query)}%"
            params.extend([like_q, like_q, like_q])

        if date_from:
            ts = self._iso_to_unix(date_from)
            if ts is not None:
                conditions.append("m.date_received >= ?")
                params.append(ts)

        if date_to:
            ts = self._iso_to_unix(date_to, end_of_day=True)
            if ts is not None:
                conditions.append("m.date_received <= ?")
                params.append(ts)

        where = " AND ".join(conditions)

        # When body search is requested, we need a two-pass approach:
        # 1. Get candidate IDs from SQL (larger set)
        # 2. Scan their .emlx files for the body text
        # 3. Then fetch final metadata for matches with limit/offset
        if body:
            # Determine how many candidates to fetch for body scanning.
            # If other filters are present, they'll narrow the set.
            # If body is the only filter, cap at 5000 most recent.
            has_other_filters = any([
                query, sender, subject, mailbox_id,
                unread_only, date_from, date_to,
            ])
            candidate_limit = 50000 if has_other_filters else 5000

            candidate_sql = f"""
                SELECT m.ROWID AS id
                FROM messages m
                LEFT JOIN addresses addr ON m.sender   = addr.ROWID
                LEFT JOIN subjects  subj ON m.subject  = subj.ROWID
                LEFT JOIN mailboxes mb   ON m.mailbox  = mb.ROWID
                WHERE {where}
                ORDER BY m.date_received DESC
                LIMIT ?
            """
            with closing(self._connect()) as conn:
                rows = conn.execute(candidate_sql, params + [candidate_limit]).fetchall()
                candidate_ids = [row["id"] for row in rows]

            if not candidate_ids:
                return []

            # Scan .emlx files for body matches, stopping once we have
            # enough to satisfy offset + limit
            matched_ids = self._body_search(body, candidate_ids, offset + limit)

            # Apply offset and limit to matched IDs
            # (IDs are already in date_received DESC order)
            paged_ids = matched_ids[offset : offset + limit]

            if not paged_ids:
                return []

            # Fetch full metadata for the paged results using parameterized query
            placeholders = ", ".join("?" for _ in paged_ids)
            meta_sql = f"{_MESSAGE_SELECT}\n    WHERE m.ROWID IN ({placeholders})\n    ORDER BY m.date_received DESC"
            with closing(self._connect()) as conn:
                rows = conn.execute(meta_sql, paged_ids).fetchall()
                return [self._row_to_summary(row) for row in rows]

        # Standard search without body (fast, SQL-only)
        sql = f"{_MESSAGE_SELECT}\n    WHERE {where}\n    ORDER BY m.date_received DESC\n    LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_summary(row) for row in rows]

    def read_email(self, message_id: int) -> dict[str, Any]:
        """Read full email content by message ID.

        Fetches metadata from the DB and body from the .emlx file on disk.
        """
        with closing(self._connect()) as conn:
            row = conn.execute(
                f"{_MESSAGE_SELECT}\n    WHERE m.ROWID = ?",
                (message_id,),
            ).fetchone()

            if not row:
                return {"error": f"Message {message_id} not found"}

            body, extra_headers = self._read_emlx(message_id)

            result = self._row_to_summary(row)
            result["body"] = body

            for key in _EXTRA_HEADERS:
                if key in extra_headers:
                    result[key.replace("-", "_")] = extra_headers[key]

            return result

    # ------------------------------------------------------------------
    # .emlx handling
    # ------------------------------------------------------------------

    def _read_emlx(self, message_id: int) -> tuple[str, dict[str, str]]:
        """Locate and parse an .emlx file by message ID.

        Checks each cached Messages/ directory.  Typically only ~10-50
        directories exist, so this is effectively O(1) per message.
        """
        path = self._find_emlx_path(message_id)
        if path is not None:
            return self._parse_emlx(path)
        return "(message body not found on disk)", {}

    @staticmethod
    def _parse_emlx(path: Path) -> tuple[str, dict[str, str]]:
        """Parse an .emlx file and return (body_text, extra_headers)."""
        try:
            with open(path, "rb") as fh:
                first_line = fh.readline()
                try:
                    byte_count = int(first_line.strip())
                    email_bytes = fh.read(byte_count)
                except ValueError:
                    email_bytes = first_line + fh.read()

            msg = email.message_from_bytes(
                email_bytes, policy=email.policy.default
            )
            body = MailDatabase._extract_text(msg)

            headers: dict[str, str] = {}
            for hdr in _EXTRA_HEADERS:
                val = msg.get(hdr)
                if val:
                    headers[hdr] = str(val)

            return body, headers
        except Exception as exc:
            logger.debug("Error parsing %s: %s", path, exc)
            return f"(error reading message: {exc})", {}

    @staticmethod
    def _extract_text(msg: Any) -> str:
        """Extract plain text from email, falling back to stripped HTML."""
        plain_parts: list[str] = []
        html_parts: list[str] = []

        def _decode_part(part: Any) -> Optional[str]:
            payload = part.get_payload(decode=True)
            if not payload:
                return None
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")

        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain":
                    text = _decode_part(part)
                    if text:
                        plain_parts.append(text)
                elif ct == "text/html" and not plain_parts:
                    text = _decode_part(part)
                    if text:
                        html_parts.append(text)
        else:
            text = _decode_part(msg)
            if text:
                if msg.get_content_type() == "text/plain":
                    plain_parts.append(text)
                elif msg.get_content_type() == "text/html":
                    html_parts.append(text)

        if plain_parts:
            return "\n".join(plain_parts).strip()

        if html_parts:
            raw = "\n".join(html_parts)
            raw = _RE_BR.sub("\n", raw)
            raw = _RE_BLOCK_CLOSE.sub("\n", raw)
            raw = _RE_TAG.sub("", raw)
            for entity, char in (("&nbsp;", " "), ("&amp;", "&"),
                                  ("&lt;", "<"), ("&gt;", ">")):
                raw = raw.replace(entity, char)
            raw = _RE_MULTI_NEWLINE.sub("\n\n", raw)
            return raw.strip()

        return "(no text content)"
