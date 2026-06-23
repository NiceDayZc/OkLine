# OkLine examples — mini tools

Small, runnable command-line tools built on `okline`. Each loads a session from
`tokens.json` (create one with `python -m okline qr-login --save tokens.json`)
or `--token` / `--tokens-file`.

Run them from the repo root, e.g. `python examples/whoami.py`.

| Tool | What it does |
|------|--------------|
| [`whoami.py`](whoami.py) | print your profile + account stats |
| [`find_contact.py`](find_contact.py) | look up a friend's mid by display name |
| [`export_contacts.py`](export_contacts.py) | dump all contacts to CSV / JSON |
| [`group_members.py`](group_members.py) | list your groups, or a group's members + names |
| [`backup_chat.py`](backup_chat.py) | save a chat's recent messages to JSON |
| [`send_media.py`](send_media.py) | send an image / file to a chat |
| [`broadcast.py`](broadcast.py) | send one text to many chats (rate-limited, confirm) |
| [`watch.py`](watch.py) | print incoming messages live (or `--echo` bot) |

## Examples

```bash
python examples/whoami.py
python examples/find_contact.py "Hardlyspeak"
python examples/export_contacts.py -o friends.csv
python examples/group_members.py --list
python examples/group_members.py C123...groupmid
python examples/backup_chat.py C123...chat -n 500 -o chat.json
python examples/send_media.py C123...chat --image pic.jpg
python examples/broadcast.py "hello everyone" --to C111... C222...
python examples/watch.py --echo
```

> Use responsibly — your own account only, and don't spam. See the project
> [SECURITY.md](../SECURITY.md) and [disclaimer](../README.md#disclaimer).
