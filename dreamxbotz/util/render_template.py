#Thanks @dreamxbotz for helping in this journey 

import jinja2
from functools import lru_cache
from info import BIN_CHANNEL, URL
from dreamxbotz.Bot import dreamxbotz
from dreamxbotz.util.human_readable import humanbytes
from dreamxbotz.util.file_properties import get_file_ids
from dreamxbotz.server.exceptions import InvalidHash
import urllib.parse
import logging

logger = logging.getLogger(__name__)

# autoescape=True -> file names can no longer inject HTML/JS into the page.
# (Inside <script> blocks the templates use the |tojson filter.)
_jinja_env = jinja2.Environment(autoescape=True)


@lru_cache(maxsize=8)
def _read_template(path: str) -> jinja2.Template:
    with open(path, encoding="utf-8") as f:
        return _jinja_env.from_string(f.read())


async def render_page(id, secure_hash, src=None):
    file_data = await get_file_ids(dreamxbotz, int(BIN_CHANNEL), int(id))
    if file_data.unique_id[:6] != secure_hash:
        logger.debug(f"link hash: {secure_hash} - {file_data.unique_id[:6]}")
        logger.debug(f"Invalid hash for message with - ID {id}")
        raise InvalidHash

    # Files without a name / mime type must not crash the page.
    raw_name = file_data.file_name or f"file_{id}"
    mime_type = file_data.mime_type or "application/octet-stream"

    src = urllib.parse.urljoin(
        URL,
        f"{id}/{urllib.parse.quote_plus(raw_name)}?hash={secure_hash}",
    )

    tag = mime_type.split("/")[0].strip()
    file_size = humanbytes(file_data.file_size)
    if tag in ["video", "audio"]:
        template_file = "dreamxbotz/template/req.html"
    else:
        template_file = "dreamxbotz/template/dl.html"

    template = _read_template(template_file)

    file_name = raw_name.replace("_", " ")

    return template.render(
        file_name=file_name,
        file_url=src,
        file_size=file_size,
        file_unique_id=file_data.unique_id,
    )
