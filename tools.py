import logging
import aiosqlite
from livekit.agents import function_tool, RunContext
import requests
from langchain_community.tools import DuckDuckGoSearchRun
import os
import asyncio
import imaplib
import email
from email.header import decode_header
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, List

# Context sizing constants (should match agent.py)
MAX_CONTEXT_SIZE = 25600
TARGET_CONTEXT_SIZE = 12800

def trim_context(context: str, max_size: int = MAX_CONTEXT_SIZE, target_size: int = TARGET_CONTEXT_SIZE) -> str:
    """
    Trims context to target size if it exceeds max_size. Uses character count as proxy for tokens.
    """
    if len(context) > max_size:
        return context[:target_size]
    elif len(context) > target_size:
        return context[:target_size]
    return context

@function_tool()
async def get_weather(
    context: RunContext,  # type: ignore
    city: str) -> str:
    """
    Get the current weather for a given city.
    """
    try:
        response = requests.get(
            f"https://wttr.in/{city}?format=3")
        if response.status_code == 200:
            logging.info(f"Weather for {city}: {response.text.strip()}")
            return trim_context(response.text.strip())
        else:
            logging.error(f"Failed to get weather for {city}: {response.status_code}")
            return trim_context(f"Could not retrieve weather for {city}.")
    except Exception as e:
        logging.error(f"Error retrieving weather for {city}: {e}")
        return trim_context(f"An error occurred while retrieving weather for {city}.")

@function_tool()
async def search_web(
    context: RunContext,  # type: ignore
    query: str) -> str:
    """
    Search the web using DuckDuckGo.
    """
    try:
        results = DuckDuckGoSearchRun().run(tool_input=query)
        logging.info(f"Search results for '{query}': {results}")
        return trim_context(results)
    except Exception as e:
        logging.error(f"Error searching the web for '{query}': {e}")
        return trim_context(f"An error occurred while searching the web for '{query}'.")
        
def _read_emails_sync(
    gmail_user: str,
    gmail_password: str,
    sender: Optional[str],
    subject: Optional[str],
    body_contains: Optional[str],
    limit: int,
) -> list:
    """Synchronous function to read emails using IMAP."""
    try:
        imap_server = "imap.gmail.com"
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(gmail_user, gmail_password)
        mail.select("inbox")

        search_criteria: List[str] = []
        if sender:
            search_criteria.append(f'FROM "{sender}"')
        if subject:
            search_criteria.append(f'SUBJECT "{subject}"')
        if body_contains:
            search_criteria.append(f'BODY "{body_contains}"')
        query_string = " ".join(search_criteria) if search_criteria else "ALL"

        status, messages = mail.search(None, query_string)
        if status != "OK":
            mail.logout()
            return []

        email_ids = messages[0].split()
        if not email_ids:
            mail.logout()
            return []

        email_ids_to_fetch = email_ids[-limit:]
        emails_found = []
        for e_id in reversed(email_ids_to_fetch):
            _, msg_data = mail.fetch(e_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subj_decoded_parts = decode_header(msg["subject"])
                    subj_decoded = "".join(
                        part.decode(encoding or "utf-8") if isinstance(part, bytes) else part
                        for part, encoding in subj_decoded_parts
                    )
                    from_decoded_parts = decode_header(msg.get("From", ""))
                    from_decoded = "".join(
                        part.decode(encoding or "utf-8") if isinstance(part, bytes) else part
                        for part, encoding in from_decoded_parts
                    )
                    date = msg.get("Date", "")
                    body_content = ""
                    attachments = []
                    recipient_folder = os.path.join("emaildb", from_decoded.replace("<", "").replace(">", "").replace("@", "_at_").replace(" ", "_"))
                    if not os.path.exists(recipient_folder):
                        os.makedirs(recipient_folder, exist_ok=True)
                    if msg.is_multipart():
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            content_disposition = str(part.get("Content-Disposition"))
                            if content_type == "text/plain" and "attachment" not in content_disposition:
                                try:
                                    payload = part.get_payload(decode=True)
                                    if isinstance(payload, bytes):
                                        body_content = payload.decode(part.get_content_charset() or 'utf-8')
                                except Exception:
                                    continue
                            elif "attachment" in content_disposition:
                                filename = part.get_filename()
                                if filename:
                                    decoded_filename = decode_header(filename)[0][0]
                                    if isinstance(decoded_filename, bytes):
                                        decoded_filename = decoded_filename.decode("utf-8", errors="ignore")
                                    file_path = os.path.join(recipient_folder, decoded_filename)
                                    payload = part.get_payload(decode=True)
                                    try:
                                        if isinstance(payload, bytes):
                                            with open(file_path, "wb") as f:
                                                f.write(payload)
                                            attachments.append({
                                                "filename": decoded_filename,
                                                "filepath": file_path,
                                                "mimetype": content_type,
                                                "size": len(payload) if payload else 0
                                            })
                                    except Exception:
                                        continue
                    else:
                        try:
                            payload = msg.get_payload(decode=True)
                            if isinstance(payload, bytes):
                                body_content = payload.decode(msg.get_content_charset() or 'utf-8')
                        except Exception:
                            body_content = "[Could not decode body]"
                    emails_found.append({
                        "from": from_decoded,
                        "subject": subj_decoded,
                        "body": body_content,
                        "date": date,
                        "attachments": attachments
                    })
        mail.logout()
        return emails_found
    except imaplib.IMAP4.error as e:
        logging.error(f"IMAP error occurred: {e}")
        return []
    except Exception as e:
        logging.error(f"Error reading email: {e}")
        return []
async def init_email_db(db_path: str = "emails.db"):
    """Initialize the async SQLite database for storing emails."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender TEXT,
                subject TEXT,
                body TEXT,
                date TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_id INTEGER,
                filename TEXT,
                filepath TEXT,
                mimetype TEXT,
                size INTEGER,
                FOREIGN KEY(email_id) REFERENCES emails(id)
            )
        """)
        await db.commit()

async def save_emails_to_db(emails: list, db_path: str = "emails.db"):
    async with aiosqlite.connect(db_path) as db:
        for email_obj in emails:
            cursor = await db.execute(
                "INSERT INTO emails (sender, subject, body, date) VALUES (?, ?, ?, ?)",
                (email_obj["from"], email_obj["subject"], email_obj["body"], email_obj["date"])
            )
            email_id = cursor.lastrowid
            for att in email_obj.get("attachments", []):
                await db.execute(
                    "INSERT INTO attachments (email_id, filename, filepath, mimetype, size) VALUES (?, ?, ?, ?, ?)",
                    (email_id, att["filename"], att["filepath"], att["mimetype"], att["size"])
                )
        await db.commit()

async def query_emails_from_db(
    sender: Optional[str] = None,
    subject: Optional[str] = None,
    body_contains: Optional[str] = None,
    limit: int = 5,
    db_path: str = "emails.db"
) -> list:
    """Query emails from the async SQLite database with optional filters."""
    query = "SELECT id, sender, subject, body, date FROM emails"
    conditions = []
    params = []
    if sender:
        conditions.append("sender LIKE ?")
        params.append(f"%{sender}%")
    if subject:
        conditions.append("subject LIKE ?")
        params.append(f"%{subject}%")
    if body_contains:
        conditions.append("body LIKE ?")
        params.append(f"%{body_contains}%")
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    results = []
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(query, params) as cursor:
            async for row in cursor:
                email_id = row[0]
                att_query = "SELECT filename, filepath, mimetype, size FROM attachments WHERE email_id = ?"
                att_cursor = await db.execute(att_query, (email_id,))
                attachments = []
                async for att_row in att_cursor:
                    attachments.append({
                        "filename": att_row[0],
                        "filepath": att_row[1],
                        "mimetype": att_row[2],
                        "size": att_row[3]
                    })
                results.append({
                    "from": row[1],
                    "subject": row[2],
                    "body": row[3],
                    "date": row[4],
                    "attachments": attachments
                })
    return results

@function_tool()
async def retrieve_emails(
    context: RunContext,  # type: ignore
    sender: Optional[str] = None,
    subject: Optional[str] = None,
    body_contains: Optional[str] = None,
    limit: int = 5,
) -> str:
    """
    Retrieve emails from the local async database with optional filters.
    Args:
        sender: Optional. Filter emails from a specific sender's email address.
        subject: Optional. Filter emails with a specific subject line.
        body_contains: Optional. Filter emails that contain this text in the body.
        limit: The maximum number of recent matching emails to retrieve. Defaults to 5.
    """
    try:
        await init_email_db()
        emails = await query_emails_from_db(sender, subject, body_contains, limit)
        if not emails:
            return trim_context("No emails found in the database matching the criteria.")
        formatted = []
        for e in emails:
            att_info = ""
            if e.get("attachments"):
                att_info = "Attachments:\n" + "\n".join([
                    f"- {a['filename']} ({a['mimetype']}, {a['size']} bytes) at {a['filepath']}"
                    for a in e["attachments"]
                ]) + "\n"
            formatted.append(f"From: {e['from']}\nSubject: {e['subject']}\nDate: {e['date']}\nBody: {e['body'][:500]}...\n{att_info}")
        return trim_context("\n\n---\n\n".join(formatted))
    except Exception as e:
        logging.error(f"Error retrieving emails from DB: {e}")
        return trim_context(f"An error occurred while retrieving emails from the database: {str(e)}")
        
@function_tool()    
async def send_email(
    context: RunContext,  # type: ignore
    to_email: str,
    subject: str,
    message: str,
    cc_email: Optional[str] = None
) -> str:
    """
    Send an email through Gmail.
    
    Args:
        to_email: Recipient email address
        subject: Email subject line
        message: Email body content
        cc_email: Optional CC email address
    """
    try:
        # Gmail SMTP configuration
        smtp_server = "smtp.gmail.com"
        smtp_port = 587
        
        # Get credentials from environment variables
        gmail_user = os.getenv("GMAIL_USER")
        gmail_password = os.getenv("GMAIL_APP_PASSWORD")  # Use App Password, not regular password
        
        if not gmail_user or not gmail_password:
            logging.error("Gmail credentials not found in environment variables")
            return "Email sending failed: Gmail credentials not configured."
        
        # Create message
        msg = MIMEMultipart()
        msg['From'] = gmail_user
        msg['To'] = to_email
        msg['Subject'] = subject
        
        # Add CC if provided
        recipients = [to_email]
        if cc_email:
            msg['Cc'] = cc_email
            recipients.append(cc_email)
        
        # Attach message body
        msg.attach(MIMEText(message, 'plain'))
        
        # Connect to Gmail SMTP server
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()  # Enable TLS encryption
        server.login(gmail_user, gmail_password)
        
        # Send email
        text = msg.as_string()
        server.sendmail(gmail_user, recipients, text)
        server.quit()
        
        logging.info(f"Email sent successfully to {to_email}")
        return f"Email sent successfully to {to_email}"
        
    except smtplib.SMTPAuthenticationError:
        logging.error("Gmail authentication failed")
        return "Email sending failed: Authentication error. Please check your Gmail credentials."
    except smtplib.SMTPException as e:
        logging.error(f"SMTP error occurred: {e}")
        return f"Email sending failed: SMTP error - {str(e)}"
    except Exception as e:
        logging.error(f"Error sending email: {e}")
        return f"An error occurred while sending email: {str(e)}"

@function_tool()
async def read_emails(
    context: RunContext,  # type: ignore
    sender: Optional[str] = None,
    subject: Optional[str] = None,
    body_contains: Optional[str] = None,
    limit: int = 5,
) -> str:
    """
    Read recent emails from a Gmail account based on search criteria.
    
    Args:
        sender: Optional. Filter emails from a specific sender's email address.
        subject: Optional. Filter emails with a specific subject line.
        body_contains: Optional. Filter emails that contain this text in the body.
        limit: The maximum number of recent matching emails to retrieve. Defaults to 5.
    """
    try:
        gmail_user = os.getenv("GMAIL_USER")
        gmail_password = os.getenv("GMAIL_APP_PASSWORD")
        if not gmail_user or not gmail_password:
            logging.error("Gmail credentials not found in environment variables")
            return trim_context("Email reading failed: Gmail credentials not configured.")

        await init_email_db()
        loop = asyncio.get_running_loop()
        emails = await loop.run_in_executor(
            None,
            _read_emails_sync,
            gmail_user, gmail_password, sender, subject, body_contains, limit
        )
        if not emails:
            return trim_context("No emails found matching the criteria.")
        await save_emails_to_db(emails)
        # Format output for display
        formatted = []
        for e in emails:
            formatted.append(f"From: {e['from']}\nSubject: {e['subject']}\nDate: {e['date']}\nBody: {e['body'][:500]}...\n")
        logging.info("Email search and DB save completed.")
        return trim_context("\n\n---\n\n".join(formatted))
    except Exception as e:
        logging.error(f"Error in read_emails tool: {e}")
        return trim_context(f"An error occurred while setting up to read emails: {str(e)}")