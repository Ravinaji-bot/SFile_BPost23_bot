"""One look for every job / status message of the bot (easy to read on a phone).

    <b>🔄 Auto Cycle</b>   ✅ ON          <- title: bold + ONE emoji, the state next to it
    ━━━━━━━━━━━━━━                        <- a line between groups
    ⏱ Every : <b>4 h</b>                  <- emoji, plain label, BOLD value
    🎬 Movie channel : <code>-100..</code> <- ids / commands in <code> (tap to copy)
    <blockquote>Next: <code>/cmd</code></blockquote>   <- what to do next, in a quote box

Rules: plain letters for normal text (no fancy fonts - they are hard to read and not searchable),
bold only for the title and the values, at most 3 styles in one message, lines under ~35 characters.
Only HTML tags are used (the client accepts HTML)."""

LINE = "━━━━━━━━━━━━━━"


def head(emoji, title, state=""):
    return f"<b>{emoji} {title}</b>" + (f"   {state}" if state else "")


def on_off(value, on="ON", off="OFF"):
    return f"✅ {on}" if value else f"⛔ {off}"


def row(emoji, label, value, code=False):
    return f"{emoji} {label} : " + (f"<code>{value}</code>" if code else f"<b>{value}</b>")


def cmd(text):
    return f"<code>{text}</code>"


def tip(*lines):
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


def bar(done, total, width=10):
    """▰▰▰▱▱▱▱▱▱▱  30%"""
    total = max(int(total or 0), 1)
    done = min(max(int(done or 0), 0), total)
    filled = round(width * done / total)
    return "▰" * filled + "▱" * (width - filled) + f"  {done * 100 // total}%"


def block(*groups):
    """Groups of lines (lists / strings) joined with the separator line."""
    out = []
    for g in groups:
        if not g:
            continue
        out.append("\n".join(g) if isinstance(g, (list, tuple)) else str(g))
    return ("\n" + LINE + "\n").join(out)
