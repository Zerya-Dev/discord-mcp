"""User-facing texts of the bot and the sign-in page in English and Polish.

The language comes from the Discord interaction locale (slash commands) or the browser's Accept-Language
(sign-in page). Anything that is not Polish falls back to English. Tool output for agents stays English.
"""

from typing import Literal

type Lang = Literal["en", "pl"]

DEFAULT: Lang = "en"

_TEXTS: dict[str, dict[Lang, str]] = {
    # /mcp login
    "login.title": {"en": "🔐 MCP sign-in code", "pl": "🔐 Kod logowania MCP"},
    "login.valid": {"en": "Valid", "pl": "Ważny"},
    "login.valid_value": {"en": "{expires} · single use", "pl": "{expires} · jednorazowy"},
    "login.how": {"en": "How to use it", "pl": "Jak użyć"},
    "login.how_value": {
        "en": "Paste it on the **Connect to {server}** page that opened when you clicked *Connect* in Claude.",
        "pl": "Wklej kod na stronie **Połącz z {server}**, która otworzyła się po kliknięciu *Connect* w Claude.",
    },
    "login.not_connected": {"en": "Not connected yet?", "pl": "Jeszcze nie podłączony?"},
    "login.not_connected_value": {
        "en": "claude.ai → Settings → Connectors → *Add custom connector* → `{url}`\n"
        "Claude Code: `claude mcp add --transport http zerya-discord {url}`",
        "pl": "claude.ai → Ustawienia → Łączniki → *Dodaj własny łącznik* → `{url}`\n"
        "Claude Code: `claude mcp add --transport http zerya-discord {url}`",
    },
    "login.sees": {"en": "What the agent sees", "pl": "Co zobaczy agent"},
    "login.sees_value": {
        "en": "Exactly the channels you can see. The agent can {access} on your behalf.",
        "pl": "Dokładnie te kanały, które widzisz Ty. Agent może {access} w Twoim imieniu.",
    },
    "access.read": {"en": "read", "pl": "czytać"},
    "access.read_write": {"en": "read and post", "pl": "czytać i pisać"},
    "login.footer": {
        "en": "Never share this code or paste it anywhere else: it signs in as you.",
        "pl": "Nie podawaj nikomu tego kodu i nie wklejaj go nigdzie indziej: loguje jako Ty.",
    },
    "login.not_member": {
        "en": "Only members of the server can connect an AI client.",
        "pl": "Tylko członkowie serwera mogą podłączać klientów AI.",
    },
    "error.title": {"en": "No access", "pl": "Brak dostępu"},
    # /mcp list
    "list.title": {"en": "🔌 Connected AI clients", "pl": "🔌 Podłączeni klienci AI"},
    "list.empty": {
        "en": "You have no connected clients. Connect Claude with `/mcp login`.",
        "pl": "Nie masz podłączonych klientów. Podłącz Claude przez `/mcp login`.",
    },
    "list.client": {"en": "MCP client", "pl": "Klient MCP"},
    "list.read_only": {"en": "read only", "pl": "tylko czyta"},
    "list.read_write": {"en": "reads and posts", "pl": "czyta i pisze"},
    "list.entry": {
        "en": "{access} · connected {created} · last used {used}",
        "pl": "{access} · podłączony {created} · ostatnio użyty {used}",
    },
    "list.never": {"en": "never", "pl": "nigdy"},
    "list.footer": {
        "en": "Disconnect: /mcp revoke <id> or /mcp revoke all",
        "pl": "Odłącz: /mcp revoke <id> albo /mcp revoke all",
    },
    # /mcp revoke
    "revoke.missing_title": {"en": "Nothing to disconnect", "pl": "Brak celu"},
    "revoke.missing": {
        "en": "Give a connection ID from `/mcp list`, or `all`.",
        "pl": "Podaj ID połączenia z `/mcp list` albo `all`.",
    },
    "revoke.none_title": {"en": "Nothing disconnected", "pl": "Nic nie odłączono"},
    "revoke.none": {
        "en": "No active connection `{target}`. Check `/mcp list`.",
        "pl": "Brak aktywnego połączenia `{target}`. Sprawdź `/mcp list`.",
    },
    "revoke.done_title": {"en": "✅ Disconnected", "pl": "✅ Odłączono"},
    "revoke.done": {
        "en": "Disconnected {count} client(s). Their tokens stopped working immediately.",
        "pl": "Odłączono klientów: {count}. Ich tokeny przestały działać od razu.",
    },
    # /mcp setup
    "setup.title": {"en": "🛠️ MCP setup · {server}", "pl": "🛠️ Konfiguracja MCP · {server}"},
    "setup.ok": {"en": "Everything is ready.", "pl": "Wszystko gotowe."},
    "setup.problems": {
        "en": "Fix the items marked ❌ (⚠️ are optional).",
        "pl": "Popraw pozycje oznaczone ❌ (⚠️ są opcjonalne).",
    },
    "setup.footer": {
        "en": "Members connect with /mcp login. Settings live in the bot's environment (ZDM_*).",
        "pl": "Członkowie łączą się przez /mcp login. Ustawienia są w zmiennych środowiskowych bota (ZDM_*).",
    },
    # Sign-in page
    "page.title": {"en": "Connect to {server}", "pl": "Połącz z {server}"},
    "page.wants": {
        "en": "<strong>{client}</strong> wants to {access} the {server} Discord history as you.",
        "pl": "<strong>{client}</strong> chce {access} historię Discorda {server} jako Ty.",
    },
    "page.access.read": {"en": "read (search and read messages in)", "pl": "czytać (wyszukiwać i czytać)"},
    "page.access.read_write": {"en": "read and post messages in", "pl": "czytać i pisać w"},
    "page.step1": {
        "en": "On the {server} Discord server, run <code>/mcp login</code>.",
        "pl": "Na serwerze {server} wpisz <code>/mcp login</code>.",
    },
    "page.step2": {"en": "Paste the code you get below.", "pl": "Wklej poniżej otrzymany kod."},
    "page.code_label": {"en": "Sign-in code", "pl": "Kod logowania"},
    "page.button": {"en": "Connect", "pl": "Połącz"},
    "page.returns": {
        "en": "After connecting you will be sent to <strong>{host}</strong>.",
        "pl": "Po połączeniu wrócisz do <strong>{host}</strong>.",
    },
    "page.footer": {
        "en": "The client sees only the channels you can see on Discord. Continue only if you started this "
        "connection yourself. Disconnect any time with <code>/mcp revoke</code>.",
        "pl": "Klient widzi tylko kanały, które widzisz na Discordzie. Kontynuuj tylko, jeśli to Ty zaczynasz to "
        "połączenie. Odłączysz je w każdej chwili przez <code>/mcp revoke</code>.",
    },
    "page.expired_title": {"en": "Sign-in expired", "pl": "Logowanie wygasło"},
    "page.expired": {
        "en": "This sign-in link expired. Go back to Claude and click <strong>Connect</strong> again.",
        "pl": "Ten link logowania wygasł. Wróć do Claude i kliknij <strong>Connect</strong> ponownie.",
    },
    "page.wrong_code": {
        "en": "That code is wrong or expired. Run /mcp login again. Attempts left: {left}.",
        "pl": "Kod jest błędny albo wygasł. Wpisz /mcp login ponownie. Pozostałe próby: {left}.",
    },
    "page.too_many": {"en": "Too many attempts, wait a minute.", "pl": "Za dużo prób, poczekaj minutę."},
}


def from_locale(locale: str | None) -> Lang:
    """Discord locale (``pl``, ``en-US``, ...) or a language tag → supported language."""
    return "pl" if locale and locale.lower().startswith("pl") else DEFAULT


def from_accept_language(header: str | None) -> Lang:
    """Picks the preferred supported language from an Accept-Language header."""
    best: tuple[float, Lang] | None = None
    for part in (header or "").split(","):
        tag, _, params = part.strip().partition(";")
        if not tag:
            continue
        quality = 1.0
        if params.strip().startswith("q="):
            try:
                quality = float(params.strip()[2:])
            except ValueError:
                quality = 0.0
        lang: Lang | None = "pl" if tag.lower().startswith("pl") else ("en" if tag.lower().startswith("en") else None)
        # q=0 means "not acceptable" (RFC 9110), not "least preferred".
        if lang is not None and quality > 0 and (best is None or quality > best[0]):
            best = (quality, lang)
    return best[1] if best else DEFAULT


def t(key: str, lang: Lang, **values: object) -> str:
    texts = _TEXTS[key]
    return texts.get(lang, texts[DEFAULT]).format(**values)
