"""The small pages the browser lands on when a provider sends it back to SED (/auth/callback, /auth/finish).

Plain HTML with no script: every value is escaped, and the page's own Content-Security-Policy allows nothing but inline
styles. A successful sign-in moves on with a meta refresh, which the browser treats as a same-site navigation.
"""

from __future__ import annotations

import html

from fastapi.responses import HTMLResponse

PAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
STYLE = (
    "body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:#f6f7f9;color:#1f2328;margin:0}"
    "main{max-width:34rem;margin:12vh auto;padding:2rem;background:#fff;border:1px solid #d8dde3;border-radius:12px}"
    "h1{font-size:1.25rem;margin:0 0 .75rem}p{line-height:1.5;margin:.5rem 0}a{color:#1a5fb4}"
    "@media (prefers-color-scheme:dark){body{background:#16181c;color:#e6e8eb}"
    "main{background:#1f2227;border-color:#343a42}a{color:#8ab4f8}}"
)


def page(
    title: str,
    message: str,
    *,
    go_to: str | None = None,
    link: str = "/",
    link_text: str = "Back to SED",
    status: int = 200,
) -> HTMLResponse:
    esc = html.escape
    refresh = f'<meta http-equiv="refresh" content="0;url={esc(go_to, quote=True)}">' if go_to else ""
    target, text = (go_to, "Continue") if go_to else (link, link_text)
    body = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)} - SED</title>{refresh}<style>{STYLE}</style></head>"
        f'<body><main><h1>{esc(title)}</h1><p>{esc(message)}</p><p><a href="{esc(target, quote=True)}">{esc(text)}</a>'
        "</p></main></body></html>"
    )
    headers = {"Cache-Control": "no-store", "Content-Security-Policy": PAGE_CSP}
    return HTMLResponse(body, status_code=status, headers=headers)
