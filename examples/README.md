# OkLine examples — mini tools

A collection of small, runnable command-line tools built on `okline`.

## Log in once — then everything just works

Run [`login.py`](login.py): it shows a **QR code**, you scan it with the LINE app
(*Settings → Add friends → QR code*) and confirm the PIN, and the session (incl.
your E2EE keys) is saved to `tokens.json`.

```bash
python examples/login.py             # scan the QR once
python examples/whoami.py            # all the other tools reuse the session
```

You don't even have to run `login.py` first — **every tool logs you in on its own**
the first time there's no saved session. Force a fresh login anytime with `--login`.

```bash
python examples/stats.py             # first run prompts the QR too; then instant
python examples/whoami.py --login    # force a new QR login
```

Auth options (all tools accept them): `--tokens-file PATH` (default `tokens.json`),
`--token ACCESS_TOKEN`, `--login` (force re-login).

## Tools

### Account & people
| Tool | What it does |
|------|--------------|
| [`login.py`](login.py) | scan a QR to log in and save the session (run once) |
| [`whoami.py`](whoami.py) | your profile + a few account stats |
| [`stats.py`](stats.py) | richer dashboard (contacts, groups, blocked, favorites, E2EE) |
| [`profile.py`](profile.py) | show a user's profile/buddy detail by mid |
| [`find_contact.py`](find_contact.py) | look up a friend's mid by display name |
| [`search_user.py`](search_user.py) | find a user by their public LINE ID (`--add` to friend) |
| [`add_friend.py`](add_friend.py) | add a friend by mid or LINE ID |
| [`export_contacts.py`](export_contacts.py) | dump all contacts to CSV / JSON |
| [`block.py`](block.py) | list / add / remove blocked contacts |
| [`favorites.py`](favorites.py) | list / add / remove favorite chats |

### Chats & groups
| Tool | What it does |
|------|--------------|
| [`groups.py`](groups.py) | list groups, leave one, or accept an invitation |
| [`group_members.py`](group_members.py) | list a group's members with names |
| [`chatlog.py`](chatlog.py) | print a chat's recent messages (decrypts E2EE when keys are loaded) |
| [`backup_chat.py`](backup_chat.py) | save a chat's recent messages to JSON |

### Sending
| Tool | What it does |
|------|--------------|
| [`send.py`](send.py) | send text / sticker / location to a chat |
| [`send_media.py`](send_media.py) | send an image / file |
| [`react.py`](react.py) | react to (or un-react) a message |
| [`unsend.py`](unsend.py) | recall one of your messages |
| [`broadcast.py`](broadcast.py) | send one text to many chats (rate-limited, confirm) |

### Live / bots
| Tool | What it does |
|------|--------------|
| [`watch.py`](watch.py) | print incoming messages live (or `--echo` bot) |
| [`notify.py`](notify.py) | alert on incoming messages (`--keyword`, `--group-only`/`--dm-only`) |
| [`autoreply.py`](autoreply.py) | keyword → reply bot (`--rule "hi=hello!"`) |

## Examples

```bash
python examples/stats.py
python examples/search_user.py nb.vtg --add
python examples/groups.py list
python examples/send.py C123...chat --text "hi from python"
python examples/send.py U123...friend --sticker 11537 52002734
python examples/chatlog.py C123...chat -n 50
python examples/autoreply.py --rule "ping=pong" --rule "hello=hi there"
python examples/notify.py --keyword urgent
```

> Use responsibly — your own account only, and don't spam. See the project
> [SECURITY.md](../SECURITY.md) and [disclaimer](../README.md#disclaimer).
