from aiohttp import web
import re
import math
import logging
import secrets
import mimetypes
import base64
from collections import OrderedDict
from urllib.parse import urlparse
from aiohttp.http_exceptions import BadStatusLine
from dreamxbotz.Bot import multi_clients, work_loads
from dreamxbotz.server.exceptions import FIleNotFound, InvalidHash
from dreamxbotz.util.custom_dl import ByteStreamer
from dreamxbotz.util.render_template import render_page
import info

logger = logging.getLogger(__name__)


routes = web.RouteTableDef()

def _parse_path(request: web.Request, path: str):
    """Returns (message_id, secure_hash) from `<hash><id>` or `<id>/<name>?hash=<hash>`."""
    match = re.search(r"^([a-zA-Z0-9_-]{6})(\d+)$", path)
    if match:
        return int(match.group(2)), match.group(1)
    id_match = re.match(r"^(\d+)(?:/|$)", path) or re.search(r"(\d+)(?:\/\S+)?", path)
    if not id_match:
        raise web.HTTPNotFound(text="Not found")
    return int(id_match.group(1)), request.rel_url.query.get("hash")


def _hash_ok(secure_hash):
    """The hash is the first 6 chars of the file unique id. A request without a plausible hash can
    never be valid, so reject it BEFORE any Telegram API call is made."""
    return bool(secure_hash) and re.fullmatch(r"[a-zA-Z0-9_-]{6}", secure_hash) is not None


@routes.get("/favicon.ico")
async def favicon_route_handler(request):
    return web.FileResponse('dreamxbotz/template/favicon.ico')

@routes.get("/", allow_head=True)
async def root_route_handler(request):
    return web.json_response("dreamxbotz")

@routes.get(r"/watch/{path:\S+}", allow_head=True)
async def watch_handler(request: web.Request):
    try:
        path = request.match_info["path"]
        id, secure_hash = _parse_path(request, path)
        if not _hash_ok(secure_hash):
            raise web.HTTPForbidden(text="Invalid hash")
        return web.Response(text=await render_page(id, secure_hash), content_type='text/html')
    except web.HTTPNotFound:
        raise
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except AttributeError:
        # malformed path / message without media
        raise web.HTTPNotFound(text="Not found")
    except (BadStatusLine, ConnectionResetError):
        # client went away - nothing to send, but a handler must still return a response
        return web.Response(status=499)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception("route handler failed")
        raise web.HTTPInternalServerError(text="Internal server error")
_LP_ALLOWED_HOSTS = ("tmdb.org", "media-amazon.com", "ssl-images-amazon.com")
_LP_CACHE = OrderedDict()
_LP_CACHE_MAX = 60                 # images
_LP_CACHE_MAX_BYTES = 30 * 1024 * 1024   # and at most ~30 MB in total


def _lp_host_ok(url: str) -> bool:
    try:
        u = urlparse(url)
        host = (u.hostname or "").lower()
        return u.scheme in ("http", "https") and any(
            host == h or host.endswith("." + h) for h in _LP_ALLOWED_HOSTS
        )
    except Exception:
        return False


@routes.get(r"/lp/{token}.jpg", allow_head=True)
async def landscape_preview_handler(request: web.Request):
    token = request.match_info["token"]
    try:
        src = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    except Exception:
        raise web.HTTPNotFound(text="Not found")
    if not _lp_host_ok(src):
        raise web.HTTPForbidden(text="Host not allowed")

    data = _LP_CACHE.get(src)
    if data is None:
        from plugins.Dreamxfutures.Imdbposter import fetch_image
        buf = await fetch_image(src, (1280, 720))
        if buf is None or isinstance(buf, str):
            raise web.HTTPFound(src)
        data = buf.getvalue()
        _LP_CACHE[src] = data
        while len(_LP_CACHE) > _LP_CACHE_MAX or (
                len(_LP_CACHE) > 1 and sum(len(v) for v in _LP_CACHE.values()) > _LP_CACHE_MAX_BYTES):
            _LP_CACHE.popitem(last=False)
    else:
        _LP_CACHE.move_to_end(src)

    return web.Response(
        body=data,
        content_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )
@routes.get(r"/{path:\S+}", allow_head=True)
async def stream_handler(request: web.Request):
    try:
        path = request.match_info["path"]
        id, secure_hash = _parse_path(request, path)
        if not _hash_ok(secure_hash):
            raise web.HTTPForbidden(text="Invalid hash")
        return await media_streamer(request, id, secure_hash)
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except web.HTTPNotFound:
        raise  # Re-raise HTTPNotFound without logging
    except AttributeError:
        raise web.HTTPNotFound(text="Not found")
    except (BadStatusLine, ConnectionResetError):
        return web.Response(status=499)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception("route handler failed")
        raise web.HTTPInternalServerError(text="Internal server error")

class_cache = {}

async def media_streamer(request: web.Request, id: int, secure_hash: str):
    range_header = request.headers.get("Range", 0)
    
    index = min(work_loads, key=work_loads.get)
    faster_client = multi_clients[index]
    
    if info.MULTI_CLIENT:
        logger.info(f"Client {index} is now serving {request.remote}")

    if faster_client in class_cache:
        tg_connect = class_cache[faster_client]
        logger.debug(f"Using cached ByteStreamer object for client {index}")
    else:
        logger.debug(f"Creating new ByteStreamer object for client {index}")
        tg_connect = ByteStreamer(faster_client)
        class_cache[faster_client] = tg_connect
    try:
        file_id = await tg_connect.get_file_properties(id)
    except (FIleNotFound, InvalidHash):
        raise
    except Exception as e:
        if index == 0:
            raise
        # extra client is probably not admin in BIN_CHANNEL -> use the main bot
        logger.warning(f"Client {index} failed to read message {id} ({e!r}); falling back to client 0")
        index = 0
        faster_client = multi_clients[0]
        tg_connect = class_cache.get(faster_client) or ByteStreamer(faster_client)
        class_cache[faster_client] = tg_connect
        file_id = await tg_connect.get_file_properties(id)
    
    if file_id.unique_id[:6] != secure_hash:
        logger.debug(f"Invalid hash for message with ID {id}")
        raise InvalidHash
    
    file_size = file_id.file_size
    if not file_size:
        raise FIleNotFound

    if range_header:
        try:
            from_str, until_str = range_header.replace("bytes=", "").split("-", 1)
            if from_str == "":
                # Suffix range, e.g. "bytes=-500" -> the last 500 bytes
                suffix = int(until_str)
                if suffix <= 0:
                    raise ValueError("empty suffix range")
                from_bytes = max(file_size - suffix, 0)
                until_bytes = file_size - 1
            else:
                from_bytes = int(from_str)
                until_bytes = int(until_str) if until_str else file_size - 1
        except ValueError:
            return web.Response(
                status=416,
                body="416: Range not satisfiable",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
    else:
        from_bytes = request.http_range.start or 0
        until_bytes = (request.http_range.stop or file_size) - 1

    # RFC 7233: a last-byte-pos beyond the end is clamped, not rejected
    until_bytes = min(until_bytes, file_size - 1)
    if (from_bytes < 0) or (until_bytes < from_bytes):
        return web.Response(
            status=416,
            body="416: Range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    chunk_size = 1024 * 1024

    offset = from_bytes - (from_bytes % chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = until_bytes % chunk_size + 1

    req_length = until_bytes - from_bytes + 1
    part_count = math.ceil((until_bytes + 1) / chunk_size) - math.floor(offset / chunk_size)
    body = tg_connect.yield_file(
        file_id, index, offset, first_part_cut, last_part_cut, part_count, chunk_size
    )

    mime_type = file_id.mime_type
    file_name = file_id.file_name

    if mime_type:
        if not file_name:
            try:
                file_name = f"{secrets.token_hex(2)}.{mime_type.split('/')[1]}"
            except (IndexError, AttributeError):
                file_name = f"{secrets.token_hex(2)}.unknown"
    else:
        if file_name:
            # guess_type() returns a (type, encoding) tuple - we only want the type
            mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        else:
            mime_type = "application/octet-stream"
            file_name = f"{secrets.token_hex(2)}.unknown"

    # Never let a file name break out of the header value
    safe_file_name = re.sub(r'[\r\n"\\]', "_", str(file_name))

    headers = {
        "Content-Type": f"{mime_type}",
        "Content-Length": str(req_length),
        "Content-Disposition": f'inline; filename="{safe_file_name}"',  # inline for streaming
        "Accept-Ranges": "bytes",
        # CORS headers for JSMKV
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Range, Content-Type",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
    }
    if range_header:
        # Content-Range is only valid on a 206 response
        headers["Content-Range"] = f"bytes {from_bytes}-{until_bytes}/{file_size}"

    return web.Response(
        status=206 if range_header else 200,
        body=body,
        headers=headers,
    )
