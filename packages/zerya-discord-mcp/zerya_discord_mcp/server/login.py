"""Sign-in page of the OAuth flow: the user pastes the one-time code from /mcp login on Discord."""

import asyncio
import html
from collections.abc import Awaitable, Callable

from mcp.server.auth.provider import construct_redirect_uri
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from zerya_discord_mcp.access.oauth import SCOPE_WRITE, OAuthStore, PendingRequest
from zerya_discord_mcp.i18n import Lang, from_accept_language, t

_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

_STYLE = """
:root { --bg: #f6f5f2; --card: #fff; --fg: #1b1b1f; --muted: #66666e; --accent: #5b5bd6; --border: #e2e1dc;
        --error: #c2410c; color-scheme: light dark; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #111114; --card: #1b1b20; --fg: #ececf1; --muted: #9c9ca8; --accent: #8b8bff; --border: #2c2c33;
          --error: #fb923c; }
}
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px; background: var(--bg);
       color: var(--fg); font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { width: 100%; max-width: 420px; background: var(--card); border: 1px solid var(--border); border-radius: 16px;
       padding: 28px; }
h1 { font-size: 20px; margin: 0 0 4px; }
p { margin: 8px 0; color: var(--muted); }
strong { color: var(--fg); }
code { font: 600 15px ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--fg); }
ol { padding-left: 20px; color: var(--muted); }
input { width: 100%; margin: 16px 0 12px; padding: 12px 14px; font: 600 22px ui-monospace, monospace;
        letter-spacing: 3px; text-align: center; text-transform: uppercase; border: 1px solid var(--border);
        border-radius: 10px; background: var(--bg); color: var(--fg); }
button { width: 100%; padding: 12px; border: 0; border-radius: 10px; background: var(--accent); color: #fff;
         font: 600 16px system-ui, sans-serif; cursor: pointer; }
.error { color: var(--error); }
.small { font-size: 13px; }
"""


def _page(title: str, body: str, lang: Lang, status: int = 200) -> HTMLResponse:
    document = (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )
    headers = dict(_HEADERS)
    headers["Content-Language"] = lang
    headers["Vary"] = "Accept-Language"
    return HTMLResponse(document, status_code=status, headers=headers)


def _expired(lang: Lang) -> HTMLResponse:
    return _page(
        t("page.expired_title", lang),
        f"<h1>{t('page.expired_title', lang)}</h1><p>{t('page.expired', lang)}</p>",
        lang,
        400,
    )


def _form(
    request: PendingRequest, supported: list[str], lang: Lang, server: str, error: str | None = None
) -> HTMLResponse:
    client = html.escape(request.client.client_name or request.client.client_id)
    scopes = request.params.scopes or []
    writes = SCOPE_WRITE in scopes and SCOPE_WRITE in supported
    access = t("page.access.read_write" if writes else "page.access.read", lang)
    # Client names are self-declared at registration; the redirect host is what actually receives the code.
    host = html.escape(request.params.redirect_uri.host or "?")
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    body = f"""
<h1>{t("page.title", lang, server=server)}</h1>
<p>{t("page.wants", lang, client=client, access=access, server=server)}</p>
<ol>
  <li>{t("page.step1", lang, server=server)}</li>
  <li>{t("page.step2", lang)}</li>
</ol>
<form method="post" autocomplete="off">
  <input type="hidden" name="request" value="{html.escape(request.id)}">
  <input name="code" placeholder="XXXX-XXXX" maxlength="12" required autofocus
         aria-label="{t("page.code_label", lang)}">
  {error_html}
  <button type="submit">{t("page.button", lang)}</button>
</form>
<p class="small">{t("page.returns", lang, host=host)}</p>
<p class="small">{t("page.footer", lang)}</p>
"""
    return _page(t("page.title", lang, server=server), body, lang, status=400 if error else 200)


def login_endpoint(
    store: OAuthStore, issuer: str, supported_scopes: list[str], server_name: Callable[[], str] = lambda: "Discord"
) -> Callable[[Request], Awaitable[Response]]:
    async def endpoint(request: Request) -> Response:
        lang = from_accept_language(request.headers.get("accept-language"))
        if request.method == "GET":
            request_id = request.query_params.get("request", "")
            pending = await asyncio.to_thread(store.get_request, request_id) if request_id else None
            return _form(pending, supported_scopes, lang, server_name()) if pending else _expired(lang)

        form = await request.form()
        request_id = str(form.get("request", ""))
        code = str(form.get("code", ""))
        pending = await asyncio.to_thread(store.get_request, request_id) if request_id else None
        if pending is None:
            return _expired(lang)
        user_id = await asyncio.to_thread(store.consume_login_code, code)
        if user_id is None:
            left = await asyncio.to_thread(store.fail_request, request_id)
            if left == 0:
                return _expired(lang)
            await asyncio.sleep(0.5)  # slow down guessing
            return _form(pending, supported_scopes, lang, server_name(), t("page.wrong_code", lang, left=left))
        auth_code = await asyncio.to_thread(store.complete_request, pending, user_id, supported_scopes)
        target = construct_redirect_uri(
            str(pending.params.redirect_uri), code=auth_code, state=pending.params.state, iss=issuer
        )
        return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})

    return endpoint
