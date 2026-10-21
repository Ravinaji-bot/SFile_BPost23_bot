"""Makes the banner pictures of the bot menu (assets/menu/*.jpg): a rainbow word with a mirror
reflection on a black background with a red glow at the top. Run once:  python tools/make_menu_banners.py
(needs Pillow and numpy; the finished pictures are already in assets/menu, the bot does not need this script)."""
import colorsys
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "arialbd.ttf", "Arial Bold.ttf",
]
WORDS = {"welcome": "WELCOME", "settings": "SETTINGS", "options": "OPTIONS", "premium": "PREMIUM",
         "referral": "REFERRAL", "history": "HISTORY"}
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "menu")


def font(size):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def background():
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    glow = np.exp(-(((x - W / 2) ** 2) / (2 * 330 ** 2) + (y ** 2) / (2 * 260 ** 2)))
    img = np.zeros((H, W, 3), np.float32)
    img[..., 0] = glow * 235
    img[..., 1] = glow * 12
    img[..., 2] = glow * 22
    return img


def rainbow_text(word):
    size = 190
    f = font(size)
    while f.getlength(word) > W * 0.78:
        size -= 6
        f = font(size)
    box = f.getbbox(word)
    tw, th = int(f.getlength(word)) + 8, box[3] + 8
    mask = Image.new("L", (tw, th), 0)
    ImageDraw.Draw(mask).text((4, 0), word, font=f, fill=255)
    hue = np.linspace(0.0, 0.78, tw)
    row = np.array([colorsys.hsv_to_rgb(h, 0.95, 1.0) for h in hue], np.float32) * 255
    color = np.broadcast_to(row[None, :, :], (th, tw, 3)).copy()
    return color, np.asarray(mask, np.float32) / 255.0


def make(name, word):
    canvas = background()
    color, alpha = rainbow_text(word)
    th, tw = alpha.shape
    x0 = (W - tw) // 2
    y0 = int(H * 0.30)
    # the word
    region = canvas[y0:y0 + th, x0:x0 + tw]
    canvas[y0:y0 + th, x0:x0 + tw] = region * (1 - alpha[..., None]) + color * alpha[..., None]
    # mirrored, squashed and faded reflection under the word
    ref_h = int(th * 0.55)
    ref_color = np.asarray(Image.fromarray(color[::-1].astype(np.uint8)).resize((tw, ref_h)), np.float32)
    ref_alpha = np.asarray(Image.fromarray((alpha[::-1] * 255).astype(np.uint8)).resize((tw, ref_h)), np.float32) / 255.0
    fade = np.linspace(0.55, 0.0, ref_h)[:, None]
    ry = y0 + th - 14
    ref_alpha = ref_alpha * fade
    region = canvas[ry:ry + ref_h, x0:x0 + tw]
    canvas[ry:ry + ref_h, x0:x0 + tw] = region * (1 - ref_alpha[..., None]) + ref_color * ref_alpha[..., None]
    Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8)).save(os.path.join(OUT, name + ".jpg"), quality=90)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for key, word in WORDS.items():
        make(key, word)
    print("banners written to", OUT)
