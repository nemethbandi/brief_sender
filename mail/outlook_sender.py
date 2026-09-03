from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)


class OutlookError(RuntimeError):
    pass


@dataclass(slots=True)
class EmailMessage:
    recipient: str
    subject: str
    html: str
    inline_images: list[tuple[str, str]] = field(default_factory=list)


class OutlookSender:
    @staticmethod
    def _execute(message: EmailMessage, action: str) -> None:
        pythoncom = None
        initialized = False
        outlook = None
        mail = None
        try:
            import pythoncom
            import win32com.client

            # Streamlit button callbacks can run on a worker thread. COM must
            # be initialized independently on every thread that uses Outlook.
            pythoncom.CoInitialize()
            initialized = True
            outlook = win32com.client.Dispatch("Outlook.Application")
            mail = outlook.CreateItem(0)
            mail.To, mail.Subject = message.recipient, message.subject
            for image_path, content_id in message.inline_images:
                attachment = mail.Attachments.Add(image_path)
                attachment.PropertyAccessor.SetProperty(
                    "http://schemas.microsoft.com/mapi/proptag/0x3712001F", content_id
                )
                attachment.PropertyAccessor.SetProperty(
                    "http://schemas.microsoft.com/mapi/proptag/0x370E001F", "image/png"
                )
                attachment.PropertyAccessor.SetProperty(
                    "http://schemas.microsoft.com/mapi/proptag/0x7FFE000B", True
                )
            mail.HTMLBody = message.html
            if action == "display":
                mail.Display()
            elif action == "send":
                mail.Send()
            else:
                raise ValueError(f"Unsupported Outlook action: {action}")
        except Exception as exc:
            logger.exception("Outlook %s failed", action)
            detail = str(getattr(exc, "strerror", "") or exc).strip()
            message = "Microsoft Outlook COM automation failed."
            if detail:
                message += f" Windows detail: {detail[:300]}"
            raise OutlookError(message) from exc
        finally:
            mail = None
            outlook = None
            if pythoncom is not None and initialized:
                pythoncom.CoUninitialize()

    def create_email(self, recipient: str, subject: str, html: str,
                     inline_images: list[tuple[str, str]] | None = None) -> EmailMessage:
        if "@" not in recipient or recipient.startswith("@") or recipient.endswith("@"):
            raise ValueError("Enter a valid recipient email address.")
        images = inline_images or []
        missing = [path for path, _ in images if not Path(path).is_file()]
        if missing:
            raise ValueError(f"Inline chart image is missing: {missing[0]}")
        return EmailMessage(recipient.strip(), subject.strip(), html, images)

    def display_email(self, message: EmailMessage) -> None:
        self._execute(message, "display")
        logger.info("Opened Outlook draft for %s", message.recipient)

    def send_email(self, message: EmailMessage) -> None:
        self._execute(message, "send")
        logger.info("Sent Outlook email to %s", message.recipient)
