"""The widget: the script a customer pastes, and the page it frames.

Two public, unauthenticated GETs, and between them the correction that this file exists
to carry.

**`frame-ancestors` is what decides who may embed the widget.** Not the `Origin` header
on the message - that is stamped by the browser with the *iframe's* origin, which is
this installation, on every page including the allowed ones. The embedding guard has to
be enforced where the embedding happens, which is when the browser decides whether to
render this document inside somebody's page, and `Content-Security-Policy:
frame-ancestors` is the header that decides it. A browser obeys it; there is no header
to compare and nothing for a page to forge.

An empty allowlist renders `frame-ancestors 'none'`, so an unconfigured channel cannot
be embedded anywhere. Same rule as everywhere in §B14: not configured refuses.

**The page is served from this installation, not from the customer's site**, which is
the whole point of the iframe - their page cannot read the conversation and this page
cannot read theirs. It also means the widget's own fetches are same-origin, so there is
no CORS between the widget and the endpoint it posts to.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.models import Channel

logger = logging.getLogger("api.widget")

router = APIRouter(tags=["web chat"])

# The bubble's size, and the panel's when it is open. In the script rather than the page
# because the *host* page owns this box - the iframe cannot resize itself.
_CLOSED = "60px"
_OPEN_WIDTH = "380px"
_OPEN_HEIGHT = "560px"


async def _channel(db: DbSession, path: str) -> Channel | None:
    return await db.scalar(
        select(Channel).where(
            Channel.webhook_path == path,
            Channel.kind == "web",
            Channel.status == "active",
        )
    )


@router.get("/embed.js", include_in_schema=False)
async def embed_script(request: Request) -> Response:
    """The one line a customer pastes, and everything it does.

    It reads its own `data-tel-agent`, builds an iframe at this installation, and sizes
    it. Nothing else: no analytics, no cookies, no reading of the host page. A script on
    somebody else's site is a liability they took on trust, and the way to deserve that
    is to do the minimum in public view.

    Served with no channel lookup - the same file for every installation and every
    channel, so it caches. The address in the tag is checked when the iframe loads.
    """
    base = str(request.base_url).rstrip("/")
    javascript = f"""(function () {{
  var tag = document.currentScript;
  if (!tag) return;
  var path = tag.getAttribute("data-tel-agent");
  if (!path) return;

  var frame = document.createElement("iframe");
  frame.src = "{base}/widget/" + encodeURIComponent(path);
  frame.title = "Chat";
  // No `allow`, and a sandbox that grants only what a chat needs. Anything this
  // widget does not need is something the host page should not have to trust it with.
  frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
  frame.style.cssText = [
    "position:fixed", "bottom:16px", "inset-inline-end:16px",
    "width:{_CLOSED}", "height:{_CLOSED}", "border:0",
    "border-radius:16px", "z-index:2147483000",
    "box-shadow:0 6px 24px rgba(0,0,0,.18)",
    "color-scheme:normal", "transition:width .18s ease,height .18s ease"
  ].join(";");
  document.body.appendChild(frame);

  // The iframe cannot resize itself; the host page owns the box. This is the only
  // message accepted, and only from the frame that was just created.
  window.addEventListener("message", function (event) {{
    if (event.source !== frame.contentWindow) return;
    if (!event.data || event.data.telAgent !== "resize") return;
    var open = event.data.open === true;
    frame.style.width = open ? "{_OPEN_WIDTH}" : "{_CLOSED}";
    frame.style.height = open ? "{_OPEN_HEIGHT}" : "{_CLOSED}";
  }});
}})();
"""
    return Response(
        content=javascript,
        media_type="application/javascript; charset=utf-8",
        headers={
            # Short, because a customer who changes their allowlist should not wait a
            # day for a browser to notice. The file rarely changes; the iframe it points
            # at is what carries the per-channel policy.
            "Cache-Control": "public, max-age=300",
        },
    )


def _page(path: str) -> str:
    """The widget's document. Written here rather than in `web/` on purpose.

    It is served by the API so that its fetches are same-origin with the endpoint, and
    so that an installation running only the API still has a working chat. It is also
    deliberately small: a build step between a customer pasting a tag and a visitor
    seeing a bubble is a build step that will be out of date on somebody's server.
    """
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Chat</title>
<style>
  :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; height: 100vh; overflow: hidden; }}
  #bubble {{ position: absolute; inset: 0; border: 0; border-radius: 16px;
    background: #4f46e5; color: #fff; font-size: 24px; cursor: pointer; }}
  #panel {{ display: none; height: 100vh; flex-direction: column;
    background: Canvas; color: CanvasText; border-radius: 16px; overflow: hidden; }}
  #panel.open {{ display: flex; }}
  header {{ display: flex; align-items: center; justify-content: space-between;
    gap: 8px; padding: 12px 14px; background: #4f46e5; color: #fff; }}
  header button {{ background: none; border: 0; color: inherit; font-size: 20px;
    cursor: pointer; }}
  #log {{ flex: 1; overflow-y: auto; padding: 12px 14px; display: flex;
    flex-direction: column; gap: 8px; }}
  .msg {{ max-width: 85%; padding: 8px 11px; border-radius: 12px; font-size: 14px;
    line-height: 1.45; overflow-wrap: anywhere; }}
  .me {{ align-self: flex-end; background: #4f46e5; color: #fff; }}
  .note {{ align-self: center; font-size: 12.5px; opacity: .7; text-align: center; }}
  form {{ display: flex; gap: 8px; padding: 10px; border-top: 1px solid rgba(128,128,128,.3); }}
  input {{ flex: 1; min-width: 0; padding: 9px 11px; font: inherit; border-radius: 9px;
    border: 1px solid rgba(128,128,128,.4); background: Canvas; color: CanvasText; }}
  form button {{ padding: 9px 14px; font: inherit; border: 0; border-radius: 9px;
    background: #4f46e5; color: #fff; cursor: pointer; }}
  form button[disabled] {{ opacity: .5; cursor: default; }}
</style>
</head><body>
<button id="bubble" aria-label="Open chat">&#128172;</button>
<div id="panel" role="dialog" aria-label="Chat">
  <header><span>Chat</span><button id="close" aria-label="Close chat">&times;</button></header>
  <div id="log" aria-live="polite"></div>
  <form id="form">
    <input id="text" autocomplete="off" placeholder="Type a message" maxlength="4000">
    <button type="submit">Send</button>
  </form>
</div>
<script>
(function () {{
  var PATH = {json.dumps(path)};
  var panel = document.getElementById("panel");
  var bubble = document.getElementById("bubble");
  var log = document.getElementById("log");
  var form = document.getElementById("form");
  var text = document.getElementById("text");
  var conversation = null;

  function resize(open) {{
    parent.postMessage({{ telAgent: "resize", open: open }}, "*");
  }}
  function show(open) {{
    panel.classList.toggle("open", open);
    bubble.style.display = open ? "none" : "block";
    resize(open);
    if (open) text.focus();
  }}
  function say(body, cls) {{
    var line = document.createElement("div");
    line.className = "msg " + cls;
    // textContent, never innerHTML: what a visitor types is not markup, and what
    // comes back is not either.
    line.textContent = body;
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
  }}

  bubble.addEventListener("click", function () {{ show(true); }});
  document.getElementById("close").addEventListener("click", function () {{ show(false); }});

  form.addEventListener("submit", async function (event) {{
    event.preventDefault();
    var body = text.value.trim();
    if (!body) return;
    text.value = "";
    say(body, "me");
    var button = form.querySelector("button");
    button.disabled = true;
    try {{
      var answer = await fetch("/public/chat/" + encodeURIComponent(PATH) + "/messages", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ text: body, conversation: conversation }})
      }});
      if (answer.ok) {{
        conversation = (await answer.json()).conversation;
      }} else {{
        // One sentence for every refusal, because the server gives one. A visitor
        // cannot act on "origin not allowed" and should not be shown it.
        say("This message could not be sent.", "note");
      }}
    }} catch (error) {{
      say("This message could not be sent.", "note");
    }} finally {{
      button.disabled = false;
      text.focus();
    }}
  }});
}})();
</script>
</body></html>
"""


@router.get("/widget/{path}", include_in_schema=False)
async def widget_page(request: Request, path: str) -> Response:
    """The document the iframe loads, carrying its own embedding policy.

    A channel that does not exist, one that is switched off, and one with nothing
    allowed all render the same empty page with `frame-ancestors 'none'` - the address
    must not answer "is this a real installation" any more than the message endpoint
    does.
    """
    db: DbSession = request.state.db
    channel = await _channel(db, path)
    allowed = (
        list((channel.settings_json or {}).get("allowed_origins") or []) if channel else []
    )

    # The header the browser enforces. `'none'` when there is nothing to allow, which
    # is also what an unknown address gets.
    ancestors = " ".join(allowed) if allowed else "'none'"
    body = _page(path) if channel and allowed else "<!doctype html><title>Chat</title>"

    if not allowed:
        logger.info(
            "widget page served closed",
            extra={"reason": "no channel or no allowed origins", "path": path},
        )

    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": (
                f"frame-ancestors {ancestors}; "
                # The page loads nothing from anywhere: its style and script are inline
                # and its only request goes back here.
                "default-src 'none'; connect-src 'self'; "
                "style-src 'unsafe-inline'; script-src 'unsafe-inline'"
            ),
            # Per channel, and the policy is in the body's headers, so it must not be
            # cached across channels by anything in between.
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        },
    )
