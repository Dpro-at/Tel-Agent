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


def _js_string(value: str) -> str:
    """A JSON string safe to sit inside a `<script>` block.

    `json.dumps` escapes quotes and backslashes and stops there - it does not touch
    `<`, so a value containing `</script>` ends the block and everything after it is
    parsed as markup. That is the whole of the reflective-XSS class, and JSON encoding
    alone is the usual near-miss.

    U+2028 and U+2029 are here too: they are valid inside a JSON string and are
    line terminators to a JavaScript parser, so an unescaped one truncates the
    statement.

    This value is a channel address that was matched against the database before
    reaching here, so today it can only be what this product generated. That is not a
    reason to interpolate it unescaped - it is a reason the bug would have waited for
    somebody to add a second caller.
    """
    encoded = json.dumps(value)
    # The right-hand sides are the six-character escape *sequences*, so what lands
    # in the page is a backslash followed by u003c - not the character itself.
    # Replacing `<` with `<` is the version of this that reads correctly and does
    # nothing at all, which is how the first attempt at this went.
    for raw, escaped in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        encoded = encoded.replace(raw, escaped)
    return encoded


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
  .them {{ align-self: flex-start; background: rgba(128,128,128,.16); }}
  .note {{ align-self: center; font-size: 12.5px; opacity: .7; text-align: center; }}
  /* Sent and unsent must not look the same. Opacity alone would not say it on a
     screen somebody is reading in daylight, so the outline carries it too. */
  .unsent {{ opacity: .55; outline: 1px dashed rgba(255,255,255,.55); }}
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
  var PATH = {_js_string(path)};
  var panel = document.getElementById("panel");
  var bubble = document.getElementById("bubble");
  var log = document.getElementById("log");
  var form = document.getElementById("form");
  var text = document.getElementById("text");
  // The thread has to survive a reload, or a visitor who refreshes asks their
  // question again and the agent answers one it was already asked, referring to
  // things that are no longer on the screen. The handle is kept per channel, and in
  // this iframe's own storage - the page embedding the widget cannot read it, which
  // is the same wall the iframe exists for.
  var HANDLE_KEY = "tel-agent:chat:" + PATH;
  var conversation = null;
  try {{
    conversation = localStorage.getItem(HANDLE_KEY) || null;
  }} catch (error) {{
    // A browser with storage switched off still chats; it just starts fresh each
    // time, which is what happened before this existed.
  }}

  function remember(handle) {{
    conversation = handle;
    try {{
      localStorage.setItem(HANDLE_KEY, handle);
    }} catch (error) {{}}
  }}

  function forget() {{
    conversation = null;
    try {{
      localStorage.removeItem(HANDLE_KEY);
    }} catch (error) {{}}
  }}

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
    return line;
  }}

  // A message that did not arrive must not sit there looking like one that did, and
  // the words the visitor typed are theirs - handing them back beats making somebody
  // type a paragraph twice because a network blinked.
  function failed(line, body) {{
    line.classList.add("unsent");
    say("This message could not be sent.", "note");
    if (!text.value) text.value = body;
  }}

  // What was said before the reload, drawn before the visitor types anything. A
  // refused handle - closed, or from a channel this is not - is dropped rather than
  // shown as an error: the next message simply starts a new thread.
  async function restore() {{
    if (!conversation) return;
    try {{
      var answer = await fetch(
        "/public/chat/" + encodeURIComponent(PATH) + "/messages?conversation=" +
        encodeURIComponent(conversation)
      );
      if (!answer.ok) {{ forget(); return; }}
      var thread = await answer.json();
      thread.messages.forEach(function (line) {{
        say(line.text, line.speaker === "visitor" ? "me" : "them");
      }});
    }} catch (error) {{
      // Offline, or the server is down. The handle is kept: the thread is still
      // there, and the next message continues it.
    }}
  }}

  bubble.addEventListener("click", function () {{ show(true); }});
  document.getElementById("close").addEventListener("click", function () {{ show(false); }});

  // The reply, as it is produced. A bubble that fills in word by word is the only
  // shape that survives the phone: an agent that composes a whole answer before
  // sending it leaves a caller listening to silence (Rule 3).
  async function listen() {{
    var line = document.createElement("div");
    line.className = "msg them";
    log.appendChild(line);

    var source = new EventSource(
      "/public/chat/" + encodeURIComponent(PATH) + "/stream?conversation=" +
      encodeURIComponent(conversation)
    );
    await new Promise(function (done) {{
      source.onmessage = function (event) {{
        var payload = JSON.parse(event.data);
        if (payload.delta) {{
          // textContent again: what the agent says is not markup either, and it will
          // be a model's output before long.
          line.textContent += payload.delta;
          log.scrollTop = log.scrollHeight;
        }}
        if (payload.done) {{ source.close(); done(); }}
      }};
      source.onerror = function () {{
        source.close();
        if (!line.textContent) {{
          line.className = "msg note";
          line.textContent = "No reply just now.";
        }} else {{
          // Half an answer. The server stores a reply only when it finished, so this
          // one is not in the transcript at all - and a visitor left holding two
          // sentences of a paragraph should know they were cut off rather than read
          // them as the whole answer.
          line.classList.add("unsent");
          say("That reply was cut off.", "note");
        }}
        done();
      }};
    }});
  }}

  form.addEventListener("submit", async function (event) {{
    event.preventDefault();
    var body = text.value.trim();
    if (!body) return;
    text.value = "";
    var line = say(body, "me");
    var button = form.querySelector("button");
    button.disabled = true;
    try {{
      var answer = await fetch("/public/chat/" + encodeURIComponent(PATH) + "/messages", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ text: body, conversation: conversation }})
      }});
      if (answer.ok) {{
        remember((await answer.json()).conversation);
        await listen();
      }} else {{
        // One sentence for every refusal, because the server gives one. A visitor
        // cannot act on "origin not allowed" and should not be shown it.
        failed(line, body);
      }}
    }} catch (error) {{
      failed(line, body);
    }} finally {{
      button.disabled = false;
      text.focus();
    }}
  }});

  restore();
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
    # `channel.webhook_path`, not the `path` from the URL. They are equal - the lookup
    # matched on exact equality - but they do not come from the same place, and what is
    # written into a page should be the value this product stored rather than the string
    # a stranger sent. The escaping in `_js_string` stands either way; this is the half
    # that makes it unnecessary.
    body = (
        _page(channel.webhook_path or "")
        if channel and allowed
        else "<!doctype html><title>Chat</title>"
    )

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
