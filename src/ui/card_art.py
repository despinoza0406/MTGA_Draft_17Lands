"""
src/ui/card_art.py
Card art for the canvases and tables: Scryfall's art crop of each card, fetched
in the background, cached on disk and in memory, and handed out as Tk images
cut to the size and shape a screen asks for.

Callers ask with ``CardArt.get(widget, card, w, h, on_ready=...)``. A cached
image comes back at once; otherwise it returns None (draw the colour
placeholder) and ``on_ready`` runs on the Tk thread once the art arrives.
"""

import hashlib
import io
import json
import logging
import os
import queue
import ssl
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Optional

from PIL import Image, ImageDraw, ImageTk

from src import constants

logger = logging.getLogger(__name__)


def download(url: str, timeout: float = 8) -> bytes:
    """GET with Windows' own certificate store. requests only trusts certifi's
    list, which fails behind antivirus HTTPS scanning (Avast, Kaspersky...)."""
    request = urllib.request.Request(url, headers={"User-Agent": "MTGADraftTool"})
    with urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=timeout) as response:
        return response.read()


def art_url(card: Dict) -> Optional[str]:
    """Scryfall art-crop URL for a card, or None when the dataset has no image."""
    urls = (card or {}).get("image") or []
    url = urls[0] if isinstance(urls, list) and urls else (urls if isinstance(urls, str) else "")
    if not url or "scryfall" not in url:
        return None
    for size in ("/large/", "/normal/", "/small/", "/png/", "/border_crop/"):
        url = url.replace(size, "/art_crop/")
    return url


def shape_image(source: Image.Image, width: int, height: int, shape: str = "rect", dim: float = 0.0) -> Image.Image:
    """Centre-crop ``source`` to width x height; "circle" masks it round over transparency."""
    src_w, src_h = source.size
    scale = max(width / src_w, height / src_h)
    resized = source.convert("RGB").resize(
        (max(width, round(src_w * scale)), max(height, round(src_h * scale))), Image.Resampling.LANCZOS
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    out = resized.crop((left, top, left + width, top + height))
    if dim:
        out = Image.blend(out, Image.new("RGB", out.size, (24, 24, 27)), dim)
    if shape == "circle":
        # Draw the mask at 4x and shrink it, so the edge is smooth.
        big = Image.new("L", (width * 4, height * 4), 0)
        ImageDraw.Draw(big).ellipse((0, 0, width * 4 - 1, height * 4 - 1), fill=255)
        rgba = out.convert("RGBA")
        rgba.putalpha(big.resize((width, height), Image.Resampling.LANCZOS))
        return rgba
    return out


class CardArt:
    CACHE_DIR = os.path.join(constants.TEMP_FOLDER, "Art")
    enabled = True

    _executor = ThreadPoolExecutor(max_workers=4)
    _done: "queue.Queue" = queue.Queue()
    _sources: "OrderedDict[str, Image.Image]" = OrderedDict()
    _photos: "OrderedDict[tuple, ImageTk.PhotoImage]" = OrderedDict()
    _waiting: Dict[str, list] = {}
    _failed = set()
    _by_name: Dict[str, str] = {}
    _indexed_sets = set()
    _polling = None
    MAX_SOURCES = 300
    MAX_PHOTOS = 900

    @classmethod
    def get(cls, widget, card: Dict, width: int, height: int, shape: str = "rect",
            dim: float = 0.0, on_ready: Optional[Callable[[], None]] = None):
        """A PhotoImage of the card's art, or None while it loads / when there is none."""
        if not cls.enabled:
            return None
        url = cls._resolve(widget, card, on_ready)
        if not url or url in cls._failed:
            return None
        key = (url, int(width), int(height), shape, round(dim, 2))
        photo = cls._photos.get(key)
        if photo is not None:
            cls._photos.move_to_end(key)
            return photo
        image = cls.pil(widget, card, width, height, shape, dim, on_ready)
        if image is None:
            return None
        try:
            photo = ImageTk.PhotoImage(image, master=widget)
        except Exception as e:
            logger.debug(f"Art resize failed for {url}: {e}")
            return None
        cls._photos[key] = photo
        while len(cls._photos) > cls.MAX_PHOTOS:
            cls._photos.popitem(last=False)
        return photo

    @classmethod
    def pil(cls, widget, card: Dict, width: int, height: int, shape: str = "rect",
            dim: float = 0.0, on_ready: Optional[Callable[[], None]] = None) -> Optional[Image.Image]:
        """Same as get() but a PIL image, for screens that compose their own pictures."""
        if not cls.enabled:
            return None
        url = cls._resolve(widget, card, on_ready)
        if not url or url in cls._failed:
            return None
        source = cls._sources.get(url)
        if source is None:
            cls._request(widget, url, on_ready)
            return None
        cls._sources.move_to_end(url)
        try:
            return shape_image(source, int(width), int(height), shape, dim)
        except Exception as e:
            logger.debug(f"Art resize failed for {url}: {e}")
            return None

    @classmethod
    def _resolve(cls, widget, card, on_ready):
        """The card's own image, else another printing's: custom 17Lands dumps
        leave the image off most printings of a card."""
        url = art_url(card)
        if url:
            return url
        name = (card or {}).get(constants.DATA_FIELD_NAME)
        if not name:
            return None
        if name in cls._by_name:
            return cls._by_name[name]
        code = (card or {}).get("set")
        if code and code not in cls._indexed_sets:
            cls._request(widget, f"set:{code}", on_ready)
        return None

    @staticmethod
    def _index_set(code) -> Dict[str, str]:
        """name -> art URL from the downloaded datasets of a set, full dumps first."""
        folder = constants.SETS_FOLDER
        try:
            files = [f for f in os.listdir(folder) if f.endswith(".json") and code in f.split("_")[0]]
        except OSError:
            return {}
        files.sort(key=lambda f: ("Custom" in f, f))
        index = {}
        for f in files:
            try:
                with open(os.path.join(folder, f), encoding="utf-8") as fh:
                    ratings = json.load(fh).get("card_ratings", {})
            except Exception:
                continue
            for c in ratings.values():
                url = art_url(c)
                if url and c.get(constants.DATA_FIELD_NAME) and c[constants.DATA_FIELD_NAME] not in index:
                    index[c[constants.DATA_FIELD_NAME]] = url
        return index

    @classmethod
    def _request(cls, widget, url, on_ready):
        callbacks = cls._waiting.get(url)
        if callbacks is not None:
            if on_ready and on_ready not in callbacks:
                callbacks.append(on_ready)
            return
        cls._waiting[url] = [on_ready] if on_ready else []
        cls._executor.submit(cls._fetch, url)
        if cls._polling is None:
            try:
                cls._polling = widget.winfo_toplevel()
                cls._polling.after(80, cls._poll)
            except Exception:
                cls._polling = None

    @classmethod
    def _fetch(cls, url):
        if url.startswith("set:"):
            cls._done.put((url, cls._index_set(url[4:])))
            return
        try:
            os.makedirs(cls.CACHE_DIR, exist_ok=True)
            path = os.path.join(cls.CACHE_DIR, hashlib.md5(url.encode("utf-8")).hexdigest() + ".jpg")
            if os.path.exists(path):
                with open(path, "rb") as f:
                    data = f.read()
            else:
                data = download(url)
                with open(path, "wb") as f:
                    f.write(data)
            image = Image.open(io.BytesIO(data))
            image.load()
            cls._done.put((url, image))
        except Exception as e:
            logger.debug(f"Art download failed for {url}: {e}")
            cls._done.put((url, None))

    @classmethod
    def _poll(cls):
        """Runs on the Tk thread: store finished downloads and wake their screens once each."""
        callbacks = []
        try:
            while True:
                url, image = cls._done.get_nowait()
                if url.startswith("set:"):
                    cls._indexed_sets.add(url[4:])
                    for name, art in (image or {}).items():
                        cls._by_name.setdefault(name, art)
                elif image is None:
                    cls._failed.add(url)
                else:
                    cls._sources[url] = image
                    while len(cls._sources) > cls.MAX_SOURCES:
                        cls._sources.popitem(last=False)
                for cb in cls._waiting.pop(url, []):
                    if cb not in callbacks:
                        callbacks.append(cb)
        except queue.Empty:
            pass
        for cb in callbacks:
            try:
                cb()
            except Exception as e:
                logger.debug(f"Art redraw failed: {e}")
        root = cls._polling
        try:
            if cls._waiting or not cls._done.empty():
                root.after(120, cls._poll)
            else:
                cls._polling = None
        except Exception:
            cls._polling = None
