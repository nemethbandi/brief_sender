import sys
from types import ModuleType, SimpleNamespace

from mail.outlook_sender import EmailMessage, OutlookSender


def test_outlook_com_is_initialized_and_inline_image_is_attached(monkeypatch, tmp_path) -> None:
    events: list[str] = []
    properties: list[tuple[str, object]] = []

    pythoncom = ModuleType("pythoncom")
    pythoncom.CoInitialize = lambda: events.append("initialize")
    pythoncom.CoUninitialize = lambda: events.append("uninitialize")

    class FakeMail:
        To = Subject = HTMLBody = ""

        class AttachmentsCollection:
            @staticmethod
            def Add(path):
                events.append("attach")
                accessor = SimpleNamespace(SetProperty=lambda key, value: properties.append((key, value)))
                return SimpleNamespace(PropertyAccessor=accessor)

        Attachments = AttachmentsCollection()

        def Display(self) -> None:
            events.append("display")

    fake_mail = FakeMail()
    fake_outlook = SimpleNamespace(CreateItem=lambda kind: fake_mail)
    client = ModuleType("win32com.client")
    client.Dispatch = lambda name: (events.append("dispatch") or fake_outlook)
    win32com = ModuleType("win32com")
    win32com.client = client

    monkeypatch.setitem(sys.modules, "pythoncom", pythoncom)
    monkeypatch.setitem(sys.modules, "win32com", win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", client)

    image = tmp_path / "chart.png"
    image.write_bytes(b"png")
    message = OutlookSender().create_email(
        "pm@example.com", "Brief", '<img src="cid:chart">', [(str(image), "chart")]
    )
    OutlookSender().display_email(message)

    assert events == ["initialize", "dispatch", "attach", "display", "uninitialize"]
    assert fake_mail.To == "pm@example.com"
    assert any(value == "chart" for _, value in properties)
