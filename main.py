import imaplib
import email
import getpass
from email.header import decode_header

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 1143
MAX_EMAILS  = 10


def decode_str(value):
    if value is None:
        return ""
    parts = decode_header(value)
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


def get_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                return part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )
    else:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", errors="replace"
        )
    return ""


def main():
    print("Proton Bridge — leitor de emails")
    print("(use o usuário e a senha do Bridge, não do Proton)\n")

    username = input("usuário: ").strip()
    password = getpass.getpass("senha do bridge: ")

    try:
        mail = imaplib.IMAP4(BRIDGE_HOST, BRIDGE_PORT)
        mail.login(username, password)
    except Exception as e:
        print(f"erro ao conectar: {e}")
        return

    mail.select("INBOX")

    _, data = mail.search(None, "ALL")
    ids = data[0].split()

    recent = ids[-MAX_EMAILS:][::-1]  # últimos 10, mais recente primeiro

    print(f"\n{len(recent)} emails mais recentes:\n")
    print("─" * 55)

    for uid in recent:
        _, msg_data = mail.fetch(uid, "(RFC822)")
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        sender  = decode_str(msg.get("From"))
        subject = decode_str(msg.get("Subject"))
        date    = msg.get("Date", "")
        body    = get_body(msg) or ""

        print(f"De:      {sender}")
        print(f"Assunto: {subject}")
        print(f"Data:    {date}")
        print(f"Corpo:   {body[:200].strip()}{'...' if len(body) > 200 else ''}")
        print("─" * 55)

    mail.logout()


if __name__ == "__main__":
    main()
