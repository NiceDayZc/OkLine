"""Runnable examples for okline.

These talk to the *real* LINE backend, so they need real credentials.  Each
example is guarded behind a function — call the one you want from ``__main__``.

Requirements: ``pip install -r requirements.txt`` AND **Node.js 18+ on PATH**
(used to compute the mandatory ``X-Hmac`` signature via the bundled
``ltsm.wasm``).  Without it the gateway returns ``REQUEST_INVALID_HMAC``.
"""

from __future__ import annotations

import os

from okline import OkLine, enums


# ---------------------------------------------------------------------------
# 1. Log in with e-mail + password (RSA flow)
# ---------------------------------------------------------------------------
def example_email_login() -> OkLine:
    api = OkLine()
    result = api.auth.email_login(
        os.environ["LINE_EMAIL"],
        os.environ["LINE_PASSWORD"],
        with_e2ee=False,  # set True to negotiate Letter Sealing
    )
    if result.success:
        print("logged in; access token:", (result.access_token or "")[:12], "...")
        print("profile:", api.get_profile())
    elif result.type == enums.LoginResultType.REQUIRE_DEVICE_CONFIRM:
        # LINE shows result.pin_code on this machine; confirm it on your phone.
        print("Enter this PIN on your phone:", result.pin_code)
        # then poll the device-confirm long-poll / re-issue login as needed
    else:
        print("login needs extra verification:", result.type, result.display_message)
    return api


# ---------------------------------------------------------------------------
# 2. Log in by scanning a QR code with your phone
# ---------------------------------------------------------------------------
def example_qr_login() -> OkLine:
    api = OkLine()

    def show_qr(url: str) -> None:
        from okline.qrterm import print_qr

        print(
            "\nScan this QR with the LINE app on your phone (LINE > Add friends > QR code):\n"
        )
        # invert=False suits a dark terminal (PowerShell/Windows Terminal);
        # pass invert=True if your terminal has a light background.
        print_qr(url, border=2, invert=False)
        print("\nor open the link manually:\n   ", url, "\n")

    def show_pin(pin: str) -> None:
        print("Enter this PIN on your phone:", pin)

    result = api.auth.qr_login(on_qr=show_qr, on_pin=show_pin)
    print("QR login result:", "OK" if result.access_token else result.type)
    return api


# ---------------------------------------------------------------------------
# 3. Reuse an existing access token (e.g. sniffed from the extension)
# ---------------------------------------------------------------------------
def example_with_token() -> OkLine:
    api = OkLine(
        access_token=os.environ["LINE_ACCESS_TOKEN"],
        refresh_token=os.environ.get("LINE_REFRESH_TOKEN"),
    )
    print("profile:", api.get_profile())
    return api


# ---------------------------------------------------------------------------
# 4. Send things
# ---------------------------------------------------------------------------
def example_send(api: OkLine, to: str) -> None:
    api.send_text(to, "hello from python 👋")
    api.send_sticker(to, package_id="11537", sticker_id="52002734")
    api.send_location(to, 35.6586, 139.7454, title="Tokyo Tower")
    api.send_flex(
        to,
        "a flex message",
        {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [{"type": "text", "text": "Hello Flex!"}],
            },
        },
    )


# ---------------------------------------------------------------------------
# 5. Receive incoming messages over the SSE operation stream
# ---------------------------------------------------------------------------
def example_receive(api: OkLine) -> None:
    print("listening for operations (Ctrl-C to stop)...")
    for op in api.ops.iter_operations():
        if op.type == enums.OpType.RECEIVE_MESSAGE and op.message:
            msg = op.message
            print(f"[{msg.get('from')}] -> {msg.get('text')!r}")
            # auto-reply example:
            # api.send_text(msg["from"], "got it!")


# ---------------------------------------------------------------------------
# 6. Contacts & groups
# ---------------------------------------------------------------------------
def example_contacts_and_groups(api: OkLine) -> None:
    ids = api.get_all_contact_ids()
    print("friend count:", len(ids) if isinstance(ids, list) else ids)
    if isinstance(ids, list) and ids:
        print("first contact:", api.get_contacts(ids[:1]))

    # create a group and invite someone
    # created = api.create_group("py group", ["u...."])
    # chat_mid = created["chat"]["chatMid"]
    # api.invite_into_chat(chat_mid, ["u...other..."])


# ---------------------------------------------------------------------------
# 7. Paste the response of every call (recording is on by default)
# ---------------------------------------------------------------------------
def example_recording(api: OkLine) -> None:
    api.get_profile()
    api.get_server_time()
    # the most recent exchange as a pasteable HTTP transcript:
    api.print_last()
    # every call made this session:
    print(api.dump())
    # ... or export for browser devtools / sharing:
    api.save_log("okline_session.har", fmt="har")
    api.save_log("okline_session.txt", fmt="text")


if __name__ == "__main__":
    # Pick whichever flow you have credentials for:
    # api = example_email_login()
    api = example_qr_login()
    # api = example_with_token()
    # example_send(api, "u0123456789abcdef0123456789abcdef")
    example_contacts_and_groups(api)
    # example_recording(api)
    api.close()
