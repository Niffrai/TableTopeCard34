import concurrent.futures
import hashlib
import json
import math
import os
import random
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional, Union
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter
import numpy as np

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

BOORU_SOURCES = {
    "xbooru": {
        "name": "Xbooru (Без API ключей / NSFW)",
        "api_url": "https://xbooru.com/index.php",
        "api_type": "gelbooru",
        "requires_auth": False
    },
    "yande": {
        "name": "Yande.re (Высокое качество / Аниме)",
        "api_url": "https://yande.re/post.json",
        "api_type": "moebooru",
        "requires_auth": False
    },
    "konachan": {
        "name": "Konachan (Обои и арт)",
        "api_url": "https://konachan.com/post.json",
        "api_type": "moebooru",
        "requires_auth": False
    },
    "safebooru": {
        "name": "Safebooru (SFW / Без 18+)",
        "api_url": "https://safebooru.org/index.php",
        "api_type": "gelbooru",
        "requires_auth": False
    },
    "rule34": {
        "name": "Rule34.xxx (Требует User ID и API Key)",
        "api_url": "https://api.rule34.xxx/index.php",
        "api_type": "gelbooru",
        "requires_auth": True
    }
}


def calculate_dhash(img: Image.Image, hash_size: int = 8) -> int:
    """Вычисляет 64-битный perceptual hash (dHash)."""
    resized = img.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.BOX)
    pixels = list(resized.getdata())
    diff = 0
    bit_pos = 0
    for row in range(hash_size):
        row_start = row * (hash_size + 1)
        for col in range(hash_size):
            left = pixels[row_start + col]
            right = pixels[row_start + col + 1]
            if left > right:
                diff |= (1 << bit_pos)
            bit_pos += 1
    return diff


def is_visual_duplicate(new_hash: int, existing_hashes: list[int], threshold: int = 4) -> bool:
    """Проверяет расстояние Хэмминга между хэшами (<= 4 бит разницы — визуальный дубликат)."""
    for h in existing_hashes:
        dist = bin(new_hash ^ h).count("1")
        if dist <= threshold:
            return True
    return False


def to_file_uri(file_path: str) -> str:
    """Преобразует локальный путь в корректный file:/// URI для Tabletop Simulator."""
    p = Path(file_path).resolve().as_posix()
    return f"file:///{p}"


def get_tts_cache_filename(url: str) -> str:
    """Вычисляет имя файла в кэше Tabletop Simulator (Mods/Images) по алгоритму TTS:
    все не-буквенно-цифровые символы удаляются, расширение добавляется через точку."""
    base, ext = os.path.splitext(url)
    ext = ext.lstrip(".")
    clean = re.sub(r'[^a-zA-Z0-9]', '', url)
    return f"{clean}.{ext}" if ext else clean


def get_tts_mods_images_dir(saves_dir: Optional[Union[str, Path]] = None) -> Path:
    """Возвращает путь к папке кэша Tabletop Simulator (Mods/Images)."""
    if saves_dir:
        p = Path(saves_dir)
        if p.name.lower() == "saves":
            mods_dir = p.parent / "Mods" / "Images"
        elif p.name.lower() == "saved objects":
            mods_dir = p.parent.parent / "Mods" / "Images"
        elif (p / "Mods" / "Images").exists() or (p.parent / "Mods" / "Images").exists():
            mods_dir = p / "Mods" / "Images" if (p / "Mods" / "Images").exists() else p.parent / "Mods" / "Images"
        else:
            mods_dir = Path.home() / "Documents" / "My Games" / "Tabletop Simulator" / "Mods" / "Images"
    else:
        mods_dir = Path.home() / "Documents" / "My Games" / "Tabletop Simulator" / "Mods" / "Images"
    mods_dir.mkdir(parents=True, exist_ok=True)
    return mods_dir


def get_deck_stable_id(deck_name: str, sample_sheet=None) -> str:
    """Генерирует стабильный идентификатор колоды, не зависящий от переименования ZIP-файла на диске."""
    clean_title = re.sub(r'[^a-zA-Z0-9_]', '', deck_name.replace(" ", "_")).strip("_") or "Deck"
    h = ""
    if sample_sheet:
        if isinstance(sample_sheet, bytes):
            h = hashlib.sha256(sample_sheet).hexdigest()[:10]
        elif isinstance(sample_sheet, (str, Path)) and os.path.exists(sample_sheet):
            try:
                with open(sample_sheet, "rb") as f:
                    h = hashlib.sha256(f.read(65536)).hexdigest()[:10]
            except Exception:
                pass
    if not h:
        h = hashlib.sha256(deck_name.encode("utf-8")).hexdigest()[:10]
    return f"{clean_title}_{h}"


def sync_deck_assets_to_mod_cache(assets_dir: Path, stable_id: str, mods_images_dir: Path) -> dict:
    """Копирует ассеты колоды в кэш Tabletop Simulator (Mods/Images) с виртуальными URL для мультиплеера."""
    base_url = f"http://tts-card-generator.local/decks/{stable_id}"
    url_map = {}
    if not assets_dir.exists():
        return url_map
    for f in os.listdir(assets_dir):
        if f.lower().endswith((".png", ".jpg", ".jpeg")):
            fpath = assets_dir / f
            vurl = f"{base_url}/{f}"
            cname = get_tts_cache_filename(vurl)
            target = mods_images_dir / cname
            try:
                if not target.exists() or target.stat().st_size != fpath.stat().st_size:
                    shutil.copy2(fpath, target)
            except Exception as e:
                print(f"[WARN] Failed to cache {f}: {e}")
            url_map[f] = vurl
    return url_map


def create_tier_table_image(output_path: str):
    """
    Генерирует стол с разметкой Tier List (S, A+, A, B+, B, C+, C)
    и зоной под колоду (Draw Pile) в высоком разрешении 2560x1440.
    """
    W, H = 2560, 1440
    img = Image.new("RGB", (W, H), color=(20, 23, 31))
    draw = ImageDraw.Draw(img)

    # Внешние рамки стола
    draw.rectangle([10, 10, W - 10, H - 10], outline=(48, 56, 75), width=3)
    draw.rectangle([16, 16, W - 16, H - 16], outline=(30, 35, 48), width=1)

    try:
        font_tier = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 68)
        font_sub = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 22)
        font_title = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 34)
    except Exception:
        font_tier = font_sub = font_title = ImageFont.load_default()

    tiers = [
        ("S", (255, 60, 100), (255, 255, 255)),
        ("A+", (255, 105, 50), (255, 255, 255)),
        ("A", (255, 160, 40), (255, 255, 255)),
        ("B+", (245, 210, 50), (20, 20, 20)),
        ("B", (160, 210, 60), (20, 20, 20)),
        ("C+", (20, 200, 200), (255, 255, 255)),
        ("C", (90, 110, 210), (255, 255, 255))
    ]

    margin_top = 45
    margin_left = 50
    deck_area_w = 420
    lanes_w = W - margin_left - deck_area_w - 90
    row_gap = 12
    n_rows = len(tiers)
    total_avail_h = H - (margin_top * 2) - ((n_rows - 1) * row_gap)
    row_h = total_avail_h // n_rows
    badge_w = 160

    for i, (label, bg_col, text_col) in enumerate(tiers):
        y1 = margin_top + i * (row_h + row_gap)
        y2 = y1 + row_h

        # Значок тира (слева)
        draw.rounded_rectangle([margin_left, y1, margin_left + badge_w, y2], radius=10, fill=bg_col)
        draw.text((margin_left + badge_w // 2, (y1 + y2) // 2), label, fill=text_col, font=font_tier, anchor="mm")

        # Полоса для размещения карт
        lane_x1 = margin_left + badge_w + 14
        lane_x2 = lane_x1 + lanes_w
        draw.rounded_rectangle([lane_x1, y1, lane_x2, y2], radius=10, fill=(28, 33, 44), outline=(48, 56, 75), width=2)

        # Сетки слотов для карт внутри ряда
        slot_w = int(row_h * 0.72)
        slot_h = row_h - 18
        slot_margin_y = y1 + 9
        for s_idx in range(12):
            sx1 = lane_x1 + 14 + s_idx * (slot_w + 14)
            sx2 = sx1 + slot_w
            if sx2 > lane_x2 - 10:
                break
            draw.rounded_rectangle([sx1, slot_margin_y, sx2, slot_margin_y + slot_h], radius=6, outline=(42, 49, 66), width=1)

    # Зона для колоды (справа)
    deck_x1 = W - margin_left - deck_area_w
    deck_x2 = W - margin_left
    deck_y1 = margin_top
    deck_y2 = H - margin_top
    draw.rounded_rectangle([deck_x1, deck_y1, deck_x2, deck_y2], radius=12, fill=(26, 30, 42), outline=(180, 140, 50), width=3)

    # Заголовки зоны колоды
    draw.text(((deck_x1 + deck_x2) // 2, deck_y1 + 45), "DECK / DRAW", fill=(240, 210, 120), font=font_title, anchor="mm")
    draw.text(((deck_x1 + deck_x2) // 2, deck_y1 + 80), "МЕСТО ДЛЯ КОЛОДЫ", fill=(160, 150, 120), font=font_sub, anchor="mm")

    # Контур слота под колоду (по центру правой зоны)
    deck_slot_w = 260
    deck_slot_h = int(deck_slot_w * 1.4)
    deck_center_y = (deck_y1 + deck_y2) // 2
    ds_x1 = (deck_x1 + deck_x2) // 2 - deck_slot_w // 2
    ds_x2 = ds_x1 + deck_slot_w
    ds_y1 = deck_center_y - deck_slot_h // 2
    ds_y2 = ds_y1 + deck_slot_h
    draw.rounded_rectangle([ds_x1, ds_y1, ds_x2, ds_y2], radius=10, fill=(34, 40, 56), outline=(220, 180, 70), width=2)
    draw.text(((ds_x1 + ds_x2) // 2, deck_center_y), "DRAW HERE", fill=(220, 180, 70), font=font_sub, anchor="mm")

    img.save(output_path, "PNG")


def create_dark_anime_skybox(output_path: str):
    """
    Генерирует стилизованный тёмный фон (Skybox / Panorama) в разрешении 2560x1280.
    Устраняет засветы и блики на карточках, создаёт атмосферное неоновое окружение
    в стиле тёмного аниме/манги (без 18+ откровенного контента).
    """
    W, H = 2560, 1280
    base = Image.new("RGBA", (W, H), (14, 15, 24, 255))
    draw = ImageDraw.Draw(base)

    # 1. Мягкий тёмный градиент (полночь -> глубокий индиго/фиолетовый горизонт -> тёмный пол)
    horizon_y = int(H * 0.52)
    for y in range(H):
        dist_from_horizon = abs(y - horizon_y) / (H * 0.5)
        dist_from_horizon = min(1.0, dist_from_horizon)
        t = 1.0 - (dist_from_horizon ** 0.8)
        r = int(12 + t * 24)
        g = int(13 + t * 14)
        b = int(22 + t * 38)
        draw.line([(0, y), (W, y)], fill=(r, g, b, 255))

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)

    # 2. Мягкая неоновая полоса на горизонте
    ov_draw.line([(0, horizon_y), (W, horizon_y)], fill=(120, 40, 160, 45), width=12)
    ov_draw.line([(0, horizon_y), (W, horizon_y)], fill=(180, 80, 220, 75), width=4)

    # 3. Шрифты для кандзи и надписей
    try:
        font_kanji = ImageFont.truetype(r"C:\Windows\Fonts\msmincho.ttc", 110)
    except Exception:
        try:
            font_kanji = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 90)
        except Exception:
            font_kanji = ImageFont.load_default()

    # 4. Стилизованные японские иероглифы по панораме (ночь, любовь, мечта, шарм, моэ...)
    kanjis = ["夜", "恋", "夢", "愛", "魅", "萌", "花", "星"]
    for idx, char in enumerate(kanjis):
        kx = int((idx + 0.5) * (W / len(kanjis)))
        ky = horizon_y - (60 if idx % 2 == 0 else 120)
        # Полупрозрачное свечение
        for offset in [(0, 1), (1, 0), (0, -1), (-1, 0), (2, 2), (-2, -2)]:
            ov_draw.text((kx + offset[0], ky + offset[1]), char, fill=(180, 70, 200, 25), font=font_kanji, anchor="mm")
        ov_draw.text((kx, ky), char, fill=(225, 145, 245, 55), font=font_kanji, anchor="mm")

    # 5. Манга-паттерн: мерцающие звёздочки, сердечки и боке
    rng = random.Random(42)
    for _ in range(140):
        sx = rng.randint(0, W)
        sy = rng.randint(int(H * 0.15), int(H * 0.85))
        s_size = rng.randint(3, 14)
        alpha = rng.randint(25, 80)
        color = rng.choice([
            (255, 105, 180, alpha),  # Hot pink
            (186, 85, 211, alpha),   # Orchid
            (147, 112, 219, alpha),  # Purple
            (100, 149, 237, alpha)   # Neon Blue
        ])
        if rng.random() < 0.4:
            # 4-конечная аниме-звезда
            ov_draw.line([(sx - s_size * 2, sy), (sx + s_size * 2, sy)], fill=color, width=1)
            ov_draw.line([(sx, sy - s_size * 2), (sx, sy + s_size * 2)], fill=color, width=1)
            ov_draw.ellipse([sx - 2, sy - 2, sx + 2, sy + 2], fill=color)
        else:
            # Боке-круги
            ov_draw.ellipse([sx - s_size, sy - s_size, sx + s_size, sy + s_size], fill=color)

    # 6. Неоновые силуэты в стиле манга-рамок (без 18+, силуэты девушек с кошачьими ушками)
    def draw_waifu_silhouette(x, y, scale=0.9):
        head_r = int(22 * scale)
        ov_draw.ellipse([x - head_r, y - head_r - int(25 * scale), x + head_r, y + head_r - int(25 * scale)], fill=(160, 60, 200, 35))
        # Кошачьи ушки
        ov_draw.polygon([(x - int(20 * scale), y - int(38 * scale)), (x - int(8 * scale), y - int(65 * scale)), (x, y - int(45 * scale))], fill=(210, 80, 220, 45))
        ov_draw.polygon([(x + int(20 * scale), y - int(38 * scale)), (x + int(8 * scale), y - int(65 * scale)), (x, y - int(45 * scale))], fill=(210, 80, 220, 45))
        # Тело/платье
        ov_draw.polygon([(x - int(18 * scale), y), (x + int(18 * scale), y), (x + int(32 * scale), y + int(80 * scale)), (x - int(32 * scale), y + int(80 * scale))], fill=(140, 50, 180, 30))
        # Рамка манга-панели вокруг силуэта
        fw, fh = int(105 * scale), int(165 * scale)
        ov_draw.rounded_rectangle([x - fw, y - int(75 * scale), x + fw, y + fh], radius=8, outline=(180, 70, 210, 35), width=2)

    for i in range(4):
        wx = int((i + 0.5) * (W / 4) + (rng.randint(-40, 40)))
        wy = horizon_y - 20
        draw_waifu_silhouette(wx, wy, scale=0.9)

    final_img = Image.alpha_composite(base, overlay).convert("RGB")
    final_img.save(output_path, "JPEG", quality=92)


def is_shock_content_tag(tag: str) -> bool:
    """Проверяет тег на шок-контент (гуро, расчленение, насилие, копро, рвота, беременность и т.д.)."""
    t = tag.lower()
    # 1. Беременность и оплодотворение
    if any(sub in t for sub in ("pregnant", "pregnancy", "impregnat")):
        return True
    # 2. Изнасилование, принуждение и недобровольное насилие
    if t in ("rape", "forced", "non-con", "non-consensual", "gang_rape", "molestation", "somnophilia", "mind_break"):
        return True
    if t.startswith("rape_") or t.endswith("_rape") or "_rape_" in t or "gangrape" in t:
        return True
    # 3. Расчленение, гуро, увечья, трупы, каннибализм, снафф
    if any(sub in t for sub in ("guro", "dismember", "decapitat", "amput", "mutilat", "necrophil", "snuff", "cannibal", "viscera", "corpse", "torture")):
        return True
    if t in ("gore", "dead", "blood", "bloody", "bloodshed", "internal_organs", "wound", "wounded"):
        return True
    if t.startswith("blood_") or t.endswith("_blood"):
        return True
    # 4. Копро, рвота, экскременты, золотой дождь
    if t in ("scat", "feces", "poop", "defecation", "coprophagia", "watersports", "urination", "enema", "soiled", "diaper"):
        return True
    if t.startswith("scat_") or t.endswith("_scat") or "_scat_" in t:
        return True
    if any(sub in t for sub in ("vomit", "fecal", "defecat", "coprophag", "watersport")):
        return True
    # 5. Зоофилия и животные
    if any(sub in t for sub in ("bestialit", "zoophil")):
        return True
    if t in ("bestiality", "zoophilia"):
        return True
    # 6. Отвратительные персонажи и паразиты
    if any(sub in t for sub in ("ugly_bastard", "parasite", "maggots")):
        return True
    return False


def is_weird_fetish_tag(tag: str) -> bool:
    """Проверяет тег на странные фетиши (vore/поедание людей, гиперболизация/гипер-пропорции, инфляция)."""
    t = tag.lower()
    # 1. Поедание человека / воре / поглощение / unbirth
    if t in ("vore", "soft_vore", "hard_vore", "cock_vore", "anal_vore", "tail_vore", "unbirth", "swallowed_whole", "digestion", "absorption"):
        return True
    if t.startswith("vore_") or t.endswith("_vore") or "_vore_" in t:
        return True

    # 2. Гиперболизация и гипер-пропорции
    if t == "hyper" or t.startswith("hyper_") or t.endswith("_hyper") or "_hyper_" in t:
        return True
    if any(sub in t for sub in (
        "implausible_proportions", "extreme_proportions", "gigantic_breasts",
        "gigantic_ass", "body_expansion", "belly_expansion", "stomach_deformation",
        "morbidly_obese", "inflation", "oviposition", "weight_gain", "extreme_muscles",
        "hyper_pregnancy"
    )):
        return True
    if t in ("giantess", "macro", "expansion", "hypertrophy"):
        return True

    return False


FULL_NUDITY_AND_SEX_TAGS = {
    "nude", "nudity", "topless", "bottomless", "completely_nude",
    "sex", "penetration", "vaginal", "anal", "oral", "fellatio", "cunnilingus",
    "pussy", "vagina", "penis", "testicles", "cock", "dick",
    "cum", "creampie", "ejaculation", "cum_inside", "cum_on_body",
    "nipples", "areola", "pussy_juice", "masturbation",
    "fingering", "dildo", "sex_toy", "uncensored", "no_clothes",
    "exposed_breasts", "naked"
}


def is_full_nudity_or_sex(post: dict, post_tags: set) -> bool:
    """Проверяет, содержит ли пост полную наготу или половые акты (гениталии, соски, половой акт)."""
    if post_tags.intersection(FULL_NUDITY_AND_SEX_TAGS):
        return True
    if any(
        t.startswith("cum_") or t.endswith("_cum") or "_cum_" in t or
        t.startswith("pussy_") or t.endswith("_pussy") or
        t.startswith("penis_") or t.endswith("_penis") or
        t.startswith("sex_") or t.endswith("_sex")
        for t in post_tags
    ):
        return True
    return False


def is_explicit_post(post: dict, post_tags: set) -> bool:
    """Определяет, является ли пост откровенным (18+ / нагота / половой акт)."""
    return is_full_nudity_or_sex(post, post_tags)


def is_empty_center_composition(img: Image.Image) -> bool:
    """
    Проверяет, является ли изображение пустым с объектом у самого края.
    Отсекает арты, где более 75-80% холста занимает пустой/гладкий фон,
    а объект прижат к крайней полосе шириной менее 22% у кромки.
    """
    try:
        thumb = img.convert("RGB").resize((100, 100))
        gray = ImageOps.grayscale(thumb)
        edges = np.array(gray.filter(ImageFilter.FIND_EDGES))

        # Обнуляем внешнюю кайму (артефакты сжатия по краям)
        edges[:3, :] = 0
        edges[-3:, :] = 0
        edges[:, :3] = 0
        edges[:, -3:] = 0

        # Значимые контуры персонажа (высокий контраст)
        sig = edges > 70
        cols_with_sig = np.where(sig.any(axis=0))[0]

        if len(cols_with_sig) == 0:
            # Вообще нет деталей (сплошной однотонный лист)
            return True

        x_min, x_max = int(cols_with_sig[0]), int(cols_with_sig[-1])
        span_width = x_max - x_min

        # Центральная зона по горизонтали (от 20% до 80%)
        center_has_details_x = bool(sig[:, 20:80].any())

        # 1. По горизонтали: в центре [20:80] пусто, а контент ютится у левого или правого края
        x_empty = (not center_has_details_x) and (span_width <= 22) and (x_max < 30 or x_min > 70)

        # 2. По вертикали: в центре [20:80] пусто, а контент ютится у верхнего или нижнего края
        rows_with_sig = np.where(sig.any(axis=1))[0]
        if len(rows_with_sig) == 0:
            return True
        y_min, y_max = int(rows_with_sig[0]), int(rows_with_sig[-1])
        span_height = y_max - y_min
        center_has_details_y = bool(sig[20:80, :].any())
        y_empty = (not center_has_details_y) and (span_height <= 20) and (y_max < 28 or y_min > 72)

        return bool(x_empty or y_empty)
    except Exception:
        return False


class DeckGenerator:
    def __init__(self, output_dir: str, history_file: Optional[str] = None):
        self.output_dir = output_dir
        self.history_file = history_file or os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")
        os.makedirs(self.output_dir, exist_ok=True)
        self.is_running = False
        self.should_stop = False
        self._load_history()

    def _load_history(self):
        """Загружает базу данных ранее скачанных карточек."""
        self.history = {
            "seen_post_ids": set(),
            "seen_api_hashes": set(),
            "seen_content_md5s": set(),
            "seen_visual_hashes": []
        }
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.history["seen_post_ids"] = set(data.get("seen_post_ids", []))
                    self.history["seen_api_hashes"] = set(data.get("seen_api_hashes", []))
                    self.history["seen_content_md5s"] = set(data.get("seen_content_md5s", []))
                    self.history["seen_visual_hashes"] = list(data.get("seen_visual_hashes", []))
            except Exception as e:
                print(f"Ошибка чтения истории: {e}")

    def _save_history(self):
        """Сохраняет базу истории на диск."""
        try:
            data = {
                "seen_post_ids": list(self.history["seen_post_ids"]),
                "seen_api_hashes": list(self.history["seen_api_hashes"]),
                "seen_content_md5s": list(self.history["seen_content_md5s"]),
                "seen_visual_hashes": self.history["seen_visual_hashes"],
                "total_cards_tracked": len(self.history["seen_content_md5s"])
            }
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Ошибка сохранения истории: {e}")

    def clear_history(self):
        """Очищает историю загрузок."""
        self.history = {
            "seen_post_ids": set(),
            "seen_api_hashes": set(),
            "seen_content_md5s": set(),
            "seen_visual_hashes": []
        }
        if os.path.exists(self.history_file):
            try:
                os.remove(self.history_file)
            except Exception:
                pass
        self.clean_old_sheets()

    def clean_old_sheets(self):
        """Удаляет все старые листы колод deck_sheet_*.png из папки вывода."""
        if not os.path.exists(self.output_dir):
            return
        for f in os.listdir(self.output_dir):
            if f.startswith("deck_sheet_") and f.endswith(".png"):
                try:
                    os.remove(os.path.join(self.output_dir, f))
                except Exception as e:
                    print(f"Ошибка удаления старого листа {f}: {e}")

    def get_history_stats(self) -> dict:
        tracked_count = max(
            len(self.history.get("seen_content_md5s", set())),
            len(self.history.get("seen_post_ids", set()))
        )
        return {
            "total_cards_tracked": tracked_count
        }

    def _http_get_json(self, url: str, params: dict) -> tuple[list | dict | str, int]:
        query_string = urllib.parse.urlencode(params)
        full_url = f"{url}?{query_string}"
        req = urllib.request.Request(full_url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                raw_text = response.read().decode("utf-8")
                try:
                    return json.loads(raw_text), response.status
                except json.JSONDecodeError:
                    return raw_text, response.status
        except urllib.error.HTTPError as he:
            try:
                body = he.read().decode("utf-8")
            except Exception:
                body = str(he)
            return body, he.code
        except Exception as e:
            return str(e), 500

    def _http_download_bytes(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read()

    def _normalize_file_url(self, file_url: str, base_domain: str = "xbooru.com") -> str:
        if file_url.startswith("//"):
            return "https:" + file_url
        if file_url.startswith("/"):
            return f"https://{base_domain}" + file_url
        return file_url

    def _parse_post_year(self, post: dict) -> Optional[int]:
        change = post.get("change")
        if change and isinstance(change, (int, float)):
            try:
                return datetime.fromtimestamp(change, tz=timezone.utc).year
            except Exception:
                pass
        created_at = post.get("created_at")
        if created_at and isinstance(created_at, str):
            match = re.search(r"\b(20\d\d)\b", created_at)
            if match:
                return int(match.group(1))
        return None

    def fetch_candidate_posts(
        self,
        character: Optional[str],
        positive_tags: list[str],
        negative_tags: list[str],
        year_from: Optional[int],
        year_to: Optional[int],
        needed_count: int,
        source: str,
        rule34_user_id: Optional[str],
        rule34_api_key: Optional[str],
        seen_post_ids: set,
        seen_api_hashes: set,
        seen_urls: set,
        log_callback: Callable[[str], None],
        filter_shock_content: bool = True,
        filter_weird_fetishes: bool = True,
        content_mode: str = "balanced",
        artist: Optional[str] = None,
        negative_artists: Optional[list[str]] = None
    ) -> list[dict]:
        """Ищет посты со случайной выборкой страниц и проверкой постоянной истории."""
        source_key = source if source in BOORU_SOURCES else "xbooru"
        source_info = BOORU_SOURCES[source_key]
        api_url = source_info["api_url"]

        api_type = source_info.get("api_type", "gelbooru")
        moebooru_ignored = {"highres", "solo", "1girl", "video", "-video"}

        api_query_tags = []
        if character:
            clean_char = character.strip().lower().replace(" ", "_")
            api_query_tags.append(clean_char)

        clean_artist = None
        if artist:
            clean_artist = artist.lstrip("@").strip().lower().replace(" ", "_")
            if clean_artist.startswith("artist:"):
                clean_artist = clean_artist[7:]
            if clean_artist and clean_artist not in api_query_tags and len(api_query_tags) < 3:
                api_query_tags.append(clean_artist)

        for tag in positive_tags:
            tag = tag.strip().lower().replace(" ", "_")
            if not tag:
                continue
            if api_type == "moebooru" and tag in moebooru_ignored:
                continue
            if tag not in api_query_tags and len(api_query_tags) < 3:
                api_query_tags.append(tag)

        # Рейтинги и служебные теги для Moebooru и Gelbooru
        if content_mode == "light":
            has_rating = any("rating:" in t for t in api_query_tags)
            if not has_rating:
                if len(api_query_tags) >= 3:
                    api_query_tags.pop()
                if api_type == "moebooru":
                    api_query_tags.append("-rating:e")
                else:
                    api_query_tags.append("-rating:explicit")
        elif content_mode == "explicit" and api_type == "moebooru":
            if not any("rating:" in t for t in api_query_tags) and len(api_query_tags) < 3:
                api_query_tags.append("rating:e")

        if api_type == "moebooru":
            if not character and not artist and len(api_query_tags) < 3 and "order:random" not in api_query_tags:
                api_query_tags.append("order:random")
        else:
            if len(api_query_tags) < 3 and "-video" not in api_query_tags:
                api_query_tags.append("-video")

        tags_query_str = " ".join(api_query_tags)
        if character and artist:
            char_label = f"{character} ({artist})"
        elif character:
            char_label = character
        elif artist:
            char_label = f"Художник: {artist}"
        else:
            char_label = "Случайный пул"

        log_callback(f"Поиск для '{char_label}' в {source_info['name']} (запрос: '{tags_query_str}')...")

        cleaned_negatives = set()
        for neg in negative_tags:
            neg_clean = neg.lstrip("-").strip().lower().replace(" ", "_")
            if neg_clean:
                cleaned_negatives.add(neg_clean)
        cleaned_negatives.update(["video", "animated", "webm", "mp4"])

        cleaned_negative_artists = set()
        if negative_artists:
            for neg_a in negative_artists:
                na = neg_a.lstrip("-").lstrip("@").strip().lower().replace(" ", "_")
                if na.startswith("artist:"):
                    na = na[7:]
                if na:
                    cleaned_negative_artists.add(na)
                    cleaned_negative_artists.add(f"artist:{na}")

        all_candidates = []
        target_buffer = max(int(needed_count * 1.5), needed_count + 30)

        # Рандомизация страниц: адаптивно определяем размер пула страниц в зависимости от нужного числа карт (до 1024)
        pages_needed_est = math.ceil(target_buffer / 50) + 10
        max_possible_pages = max(120, pages_needed_est + 50) if not character else max(50, pages_needed_est + 20)
        available_pages = list(range(max_possible_pages))
        random.shuffle(available_pages)

        # Первые свежие страницы + случайные глубокие страницы из пула
        initial_sample_count = max(pages_needed_est, 25)
        pages_to_check = [0, 1, 2] + available_pages[:initial_sample_count]
        pages_to_check = list(dict.fromkeys(pages_to_check))
        # Оставшиеся страницы держим в резерве на случай большого количества дубликатов/истории
        remaining_reserve = [p for p in available_pages if p not in pages_to_check]
        page_queue = pages_to_check + remaining_reserve

        switched_to_fallback = False
        skipped_from_history = 0
        consecutive_empty_pages = 0
        api_type = source_info.get("api_type", "gelbooru")
        base_netloc = urllib.parse.urlparse(api_url).netloc

        for pid in page_queue:
            if len(all_candidates) >= target_buffer or self.should_stop:
                break

            if api_type == "moebooru":
                params = {
                    "page": pid + 1,
                    "limit": 100,
                    "tags": tags_query_str
                }
            else:
                params = {
                    "page": "dapi",
                    "s": "post",
                    "q": "index",
                    "json": "1",
                    "tags": tags_query_str,
                    "limit": 100,
                    "pid": pid
                }

            if source_key == "rule34" and rule34_api_key and rule34_user_id:
                params["api_key"] = rule34_api_key.strip()
                params["user_id"] = rule34_user_id.strip()

            resp_data, status_code = self._http_get_json(api_url, params)

            is_auth_error = (
                isinstance(resp_data, str) and (
                    "Missing authentication" in resp_data or
                    "Authentication failed" in resp_data
                )
            ) or (status_code in [401, 403] and source_key == "rule34")

            if is_auth_error and not switched_to_fallback:
                log_callback("Rule34.xxx требует API Key. Автоматически переключаюсь на Xbooru...")
                source_key = "xbooru"
                source_info = BOORU_SOURCES["xbooru"]
                api_url = source_info["api_url"]
                api_type = source_info.get("api_type", "gelbooru")
                base_netloc = urllib.parse.urlparse(api_url).netloc
                switched_to_fallback = True
                params = {
                    "page": "dapi",
                    "s": "post",
                    "q": "index",
                    "json": "1",
                    "tags": tags_query_str,
                    "limit": 100,
                    "pid": pid
                }
                resp_data, status_code = self._http_get_json(api_url, params)

            if not isinstance(resp_data, list):
                break

            if len(resp_data) == 0:
                consecutive_empty_pages += 1
                if consecutive_empty_pages >= 6:
                    break
                continue
            else:
                consecutive_empty_pages = 0

            for post in resp_data:
                file_url = post.get("file_url") or post.get("jpeg_url")
                post_id = post.get("id")
                api_hash = post.get("hash") or post.get("md5")

                if not file_url:
                    continue

                file_url = self._normalize_file_url(file_url, base_domain=base_netloc)

                ext = os.path.splitext(file_url)[1].lower()
                if ext not in [".jpg", ".jpeg", ".png"]:
                    continue

                # 1. Проверка текущей сессии
                if post_id and post_id in seen_post_ids:
                    continue
                if api_hash and api_hash in seen_api_hashes:
                    continue
                if file_url in seen_urls:
                    continue

                # 2. Проверка постоянной базы истории (были ли в прошлых колодах)
                if post_id and post_id in self.history["seen_post_ids"]:
                    skipped_from_history += 1
                    continue
                if api_hash and api_hash in self.history["seen_api_hashes"]:
                    skipped_from_history += 1
                    continue

                # 3. Фильтрация по отрицательным тегам, художникам и активным щитам защиты
                post_tags = set(post.get("tags", "").lower().split())
                if post_tags.intersection(cleaned_negatives):
                    continue
                if cleaned_negative_artists and post_tags.intersection(cleaned_negative_artists):
                    continue

                # 3.1. Проверка авторства (если запрошен конкретный художник)
                if clean_artist:
                    has_artist_tag = (
                        clean_artist in post_tags
                        or f"artist:{clean_artist}" in post_tags
                        or any(t == clean_artist or t == f"artist:{clean_artist}" for t in post_tags)
                    )
                    post_author = str(post.get("author", "") or post.get("artist", "")).lower().replace(" ", "_")
                    if not has_artist_tag and post_author != clean_artist:
                        continue
                if filter_shock_content and any(is_shock_content_tag(t) for t in post_tags):
                    continue
                if filter_weird_fetishes and any(is_weird_fetish_tag(t) for t in post_tags):
                    continue

                # 4. Фильтр откровенности контента (полный откровенный 18+ vs горячий SFW без наготы)
                if content_mode == "light":
                    # Горячий контент, но строго без полной наготы и половых актов
                    if is_full_nudity_or_sex(post, post_tags):
                        continue
                elif content_mode == "explicit":
                    rating = str(post.get("rating", "")).lower()
                    if rating in ("safe", "s", "general") and not is_full_nudity_or_sex(post, post_tags):
                        continue

                # 4. Фильтрация по годам
                if year_from or year_to:
                    post_year = self._parse_post_year(post)
                    if post_year:
                        if year_from and post_year < year_from:
                            continue
                        if year_to and post_year > year_to:
                            continue

                if post_id:
                    seen_post_ids.add(post_id)
                if api_hash:
                    seen_api_hashes.add(api_hash)
                seen_urls.add(file_url)

                all_candidates.append(post)

            time.sleep(0.12)

        random.shuffle(all_candidates)
        if skipped_from_history > 0:
            log_callback(f"Отсеяно {skipped_from_history} постов, уже использованных в прошлых колодах.")

        log_callback(f"Собрано {len(all_candidates)} уникальных кандидатов для '{char_label}' (нужно: {needed_count}).")
        return all_candidates

    def generate_default_card_back(self, width: int, height: int, title: str = "CUSTOM DECK") -> str:
        back_path = os.path.join(self.output_dir, "card_back.png")
        img = Image.new("RGB", (width, height), color=(24, 24, 32))
        draw = ImageDraw.Draw(img)

        border_margin = int(width * 0.06)
        draw.rectangle(
            [border_margin, border_margin, width - border_margin, height - border_margin],
            outline=(180, 140, 50),
            width=max(2, int(width * 0.015))
        )
        inner_margin = int(width * 0.09)
        draw.rectangle(
            [inner_margin, inner_margin, width - inner_margin, height - inner_margin],
            outline=(100, 80, 30),
            width=1
        )

        center_x = width // 2
        center_y = height // 2
        diamond_size = int(width * 0.28)
        draw.polygon(
            [
                (center_x, center_y - diamond_size),
                (center_x + diamond_size, center_y),
                (center_x, center_y + diamond_size),
                (center_x - diamond_size, center_y),
            ],
            outline=(180, 140, 50),
            fill=(36, 36, 50)
        )

        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

        text = title.upper()
        draw.text((center_x, center_y), text, fill=(240, 210, 120), font=font, anchor="mm")
        img.save(back_path, "PNG")
        return back_path

    @staticmethod
    def prepare_card_image(raw_img: Image.Image, target_w: int, target_h: int, fit_mode: str = "cover") -> Image.Image:
        """
        Подгоняет арт под размеры карты:
        - 'cover': заполняет 100% карты, кадрируя лишние края по центру (без черных полос).
        - 'contain': размещает арт целиком на 100% без обрезки, а пустые поля
          заполняет размытым и мягко затемненным фоном из этого же арта.
        """
        if fit_mode == "contain":
            orig_w, orig_h = raw_img.size
            target_ratio = target_w / target_h
            orig_ratio = orig_w / orig_h

            # Если пропорции почти совпадают (погрешность < 2%)
            if abs(orig_ratio - target_ratio) < 0.02:
                return raw_img.resize((target_w, target_h), Image.Resampling.LANCZOS)

            # 1. Размытый фон из оригинального арта (заполняет всю карточку)
            bg = ImageOps.fit(raw_img, (target_w, target_h), Image.Resampling.BILINEAR)
            bg = bg.filter(ImageFilter.GaussianBlur(radius=18))

            # 2. Мягкое затемнение фона (35% черного), чтобы арт четко выделялся
            dark_layer = Image.new("RGB", (target_w, target_h), (0, 0, 0))
            bg = Image.blend(bg, dark_layer, 0.35)

            # 3. Масштабируем оригинальный арт так, чтобы он полностью влез без обрезки
            scale = min(target_w / orig_w, target_h / orig_h)
            new_w = max(1, int(round(orig_w * scale)))
            new_h = max(1, int(round(orig_h * scale)))
            main_art = raw_img.resize((new_w, new_h), Image.Resampling.LANCZOS)

            # 4. Центрируем и накладываем поверх размытого фона
            offset_x = (target_w - new_w) // 2
            offset_y = (target_h - new_h) // 2
            bg.paste(main_art, (offset_x, offset_y))
            return bg

        # Режим по умолчанию: "cover" (во весь размер с кадрированием по центру)
        return ImageOps.fit(raw_img, (target_w, target_h), Image.Resampling.LANCZOS)

    def generate_deck(
        self,
        positive_tags: list[str],
        negative_tags: list[str],
        characters: list[str],
        total_cards: int,
        year_from: Optional[int],
        year_to: Optional[int],
        card_width: int = 300,
        card_height: int = 420,
        deck_title: str = "Custom R34 Deck",
        source: str = "xbooru",
        rule34_user_id: Optional[str] = None,
        rule34_api_key: Optional[str] = None,
        threads: int = 8,
        progress_callback: Optional[Callable[[dict], None]] = None,
        filter_shock_content: bool = True,
        filter_weird_fetishes: bool = True,
        content_mode: str = "balanced",
        filter_empty_center: bool = True,
        fit_mode: str = "cover",
        artists: Optional[list[str]] = None,
        negative_artists: Optional[list[str]] = None
    ) -> dict:
        self.is_running = True
        self.should_stop = False

        def report(status: str, pct: int):
            if progress_callback:
                progress_callback({
                    "status": status,
                    "percent": pct,
                    "is_running": self.is_running
                })

        report("Инициализация генерации...", 0)

        # Очищаем старые листы колоды (deck_sheet_*.png), чтобы они не копились между генерациями
        self.clean_old_sheets()

        clean_characters = [c.strip() for c in characters if c.strip()]
        clean_artists = [a.strip() for a in (artists or []) if a.strip()]
        clean_neg_artists = [na.strip() for na in (negative_artists or []) if na.strip()]
        generation_targets = []

        if clean_characters and clean_artists:
            combos = [(c, a) for c in clean_characters for a in clean_artists]
            n_combos = len(combos)
            base_count = total_cards // n_combos
            remainder = total_cards % n_combos
            for i, (c, a) in enumerate(combos):
                count = base_count + (1 if i < remainder else 0)
                if count > 0:
                    generation_targets.append((c, a, count))
        elif clean_characters:
            n_chars = len(clean_characters)
            base_count = total_cards // n_chars
            remainder = total_cards % n_chars
            for i, char in enumerate(clean_characters):
                count = base_count + (1 if i < remainder else 0)
                if count > 0:
                    generation_targets.append((char, None, count))
        elif clean_artists:
            n_art = len(clean_artists)
            base_count = total_cards // n_art
            remainder = total_cards % n_art
            for i, art in enumerate(clean_artists):
                count = base_count + (1 if i < remainder else 0)
                if count > 0:
                    generation_targets.append((None, art, count))
        else:
            generation_targets.append((None, None, total_cards))

        seen_post_ids = set()
        seen_api_hashes = set()
        seen_urls = set()
        seen_content_md5s = set()
        seen_visual_hashes = []

        pools_by_target = []
        for char, art, target_count in generation_targets:
            if self.should_stop:
                break
            posts = self.fetch_candidate_posts(
                character=char,
                positive_tags=positive_tags,
                negative_tags=negative_tags,
                year_from=year_from,
                year_to=year_to,
                needed_count=target_count,
                source=source,
                rule34_user_id=rule34_user_id,
                rule34_api_key=rule34_api_key,
                seen_post_ids=seen_post_ids,
                seen_api_hashes=seen_api_hashes,
                seen_urls=seen_urls,
                log_callback=lambda msg: report(msg, 5),
                filter_shock_content=filter_shock_content,
                filter_weird_fetishes=filter_weird_fetishes,
                content_mode=content_mode,
                artist=art,
                negative_artists=clean_neg_artists
            )
            pools_by_target.append((char, art, target_count, posts))

        total_candidates_collected = sum(len(p[3]) for p in pools_by_target)
        if total_candidates_collected == 0:
            self.is_running = False
            report("Не удалось найти изображения по заданным критериям.", 0)
            return {"success": False, "error": "Изображения не найдены"}

        report(f"Начало скачивания {total_cards} уникальных карт...", 10)

        processed_cards: list[Image.Image] = []
        duplicate_count = 0
        empty_center_skipped = 0

        photos_dir = os.path.join(self.output_dir, "photos")
        os.makedirs(photos_dir, exist_ok=True)
        for old_f in os.listdir(photos_dir):
            try:
                os.remove(os.path.join(photos_dir, old_f))
            except Exception:
                pass

        new_post_ids = []
        new_api_hashes = []
        new_content_md5s = []
        new_visual_hashes = []

        max_workers = max(1, min(int(threads or 8), 16))

        def _fetch_candidate(post_item):
            if self.should_stop:
                return None
            f_url = post_item.get("file_url") or post_item.get("jpeg_url")
            if not f_url:
                return None
            try:
                b_data = self._http_download_bytes(f_url)
                c_md5 = hashlib.md5(b_data).hexdigest()
                r_img = Image.open(BytesIO(b_data)).convert("RGB")
                dh = calculate_dhash(r_img)
                return {
                    "post": post_item,
                    "content_md5": c_md5,
                    "raw_img": r_img,
                    "v_hash": dh
                }
            except Exception:
                return None

        report(f"Параллельное скачивание карт (потоков: {max_workers})...", 10)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for char_name, art_name, target_count, candidate_posts in pools_by_target:
                if self.should_stop or len(processed_cards) >= total_cards:
                    break

                if char_name and art_name:
                    char_label = f"{char_name} ({art_name})"
                elif char_name:
                    char_label = char_name
                elif art_name:
                    char_label = f"Художник: {art_name}"
                else:
                    char_label = "Колода"
                downloaded_for_char = 0
                post_iter = iter(candidate_posts)
                futures = set()

                # Наполняем начальное окно задач для текущего персонажа
                initial_batch = min(len(candidate_posts), max_workers * 2)
                for _ in range(initial_batch):
                    try:
                        p = next(post_iter)
                        futures.add(executor.submit(_fetch_candidate, p))
                    except StopIteration:
                        break

                while futures and downloaded_for_char < target_count and len(processed_cards) < total_cards:
                    if self.should_stop:
                        for f in futures:
                            f.cancel()
                        break

                    done, futures = concurrent.futures.wait(
                        futures,
                        return_when=concurrent.futures.FIRST_COMPLETED
                    )

                    for f in done:
                        if self.should_stop or downloaded_for_char >= target_count or len(processed_cards) >= total_cards:
                            break

                        res = f.result()
                        if not res:
                            continue

                        # 1. Проверка MD5 (текущая колода + история)
                        c_md5 = res["content_md5"]
                        if c_md5 in seen_content_md5s or c_md5 in self.history["seen_content_md5s"]:
                            duplicate_count += 1
                            continue

                        # 2. Проверка визуального хэша dHash (текущая колода + история)
                        dh = res["v_hash"]
                        all_visual_hashes = seen_visual_hashes + self.history["seen_visual_hashes"]
                        if is_visual_duplicate(dh, all_visual_hashes, threshold=4):
                            duplicate_count += 1
                            continue

                        # 3. Проверка композиции (пустой центр и смещение объекта к самому краю)
                        if filter_empty_center and is_empty_center_composition(res["raw_img"]):
                            empty_center_skipped += 1
                            continue

                        # Карта уникальна!
                        seen_content_md5s.add(c_md5)
                        seen_visual_hashes.append(dh)

                        post = res["post"]
                        if post.get("id"):
                            new_post_ids.append(post["id"])
                        if post.get("hash") or post.get("md5"):
                            new_api_hashes.append(post.get("hash") or post.get("md5"))
                        new_content_md5s.append(c_md5)
                        new_visual_hashes.append(dh)

                        card_img = self.prepare_card_image(res["raw_img"], card_width, card_height, fit_mode)
                        processed_cards.append(card_img)
                        downloaded_for_char += 1

                        # Сохраняем индивидуальное фото
                        card_num = len(processed_cards)
                        photo_file = os.path.join(photos_dir, f"photo_{card_num:03d}.jpg")
                        card_img.save(photo_file, "JPEG", quality=95)

                        pct = 10 + int((card_num / total_cards) * 78)
                        if card_num % 5 == 0 or card_num == total_cards or downloaded_for_char == target_count:
                            report(f"Скачано: {card_num}/{total_cards} карт ({char_label}: {downloaded_for_char}/{target_count}) [{max_workers} потоков]...", pct)

                    # Добавляем в окно новые задачи, если карточек для персонажа ещё не хватает
                    while len(futures) < (max_workers * 2) and downloaded_for_char < target_count and len(processed_cards) < total_cards and not self.should_stop:
                        try:
                            p = next(post_iter)
                            futures.add(executor.submit(_fetch_candidate, p))
                        except StopIteration:
                            break

                # Отменяем незавершённые задачи для текущего пула перед переходом к следующему
                for f in futures:
                    f.cancel()

            # Добор недостающих карт из неиспользованных кандидатов всех пулов, если квота ещё не заполнена
            if len(processed_cards) < total_cards and not self.should_stop:
                for char_name, art_name, target_count, candidate_posts in pools_by_target:
                    if len(processed_cards) >= total_cards or self.should_stop:
                        break
                    for p in candidate_posts:
                        if len(processed_cards) >= total_cards or self.should_stop:
                            break
                        res = _fetch_candidate(p)
                        if not res:
                            continue
                        c_md5 = res["content_md5"]
                        if c_md5 in seen_content_md5s or c_md5 in self.history["seen_content_md5s"]:
                            duplicate_count += 1
                            continue
                        dh = res["v_hash"]
                        all_visual_hashes = seen_visual_hashes + self.history["seen_visual_hashes"]
                        if is_visual_duplicate(dh, all_visual_hashes, threshold=4):
                            duplicate_count += 1
                            continue
                        if filter_empty_center and is_empty_center_composition(res["raw_img"]):
                            empty_center_skipped += 1
                            continue
                        seen_content_md5s.add(c_md5)
                        seen_visual_hashes.append(dh)
                        post = res["post"]
                        if post.get("id"):
                            new_post_ids.append(post["id"])
                        if post.get("hash") or post.get("md5"):
                            new_api_hashes.append(post.get("hash") or post.get("md5"))
                        new_content_md5s.append(c_md5)
                        new_visual_hashes.append(dh)
                        card_img = self.prepare_card_image(res["raw_img"], card_width, card_height, fit_mode)
                        processed_cards.append(card_img)
                        card_num = len(processed_cards)
                        photo_file = os.path.join(photos_dir, f"photo_{card_num:03d}.jpg")
                        card_img.save(photo_file, "JPEG", quality=95)

        if duplicate_count > 0 or empty_center_skipped > 0:
            msgs = []
            if duplicate_count > 0:
                msgs.append(f"дубликатов: {duplicate_count}")
            if empty_center_skipped > 0:
                msgs.append(f"пустых/смещённых: {empty_center_skipped}")
            report(f"Отфильтровано {', '.join(msgs)}", 89)

        if not processed_cards:
            self.is_running = False
            return {"success": False, "error": "Не удалось загрузить ни одного уникального изображения"}

        # Запоминаем новые карты в историю
        self.history["seen_post_ids"].update(new_post_ids)
        self.history["seen_api_hashes"].update(new_api_hashes)
        self.history["seen_content_md5s"].update(new_content_md5s)
        self.history["seen_visual_hashes"].extend(new_visual_hashes)
        self._save_history()

        report(f"Сборка листов колоды и тир-лист стола ({len(processed_cards)} карт)...", 90)

        random.shuffle(processed_cards)

        COLUMNS = 10
        MAX_PER_SHEET = 70
        generated_sheets = []

        total_cards_count = len(processed_cards)
        num_sheets = math.ceil(total_cards_count / MAX_PER_SHEET)

        # Гарантируем чистоту папки перед записью новой партии листов
        self.clean_old_sheets()

        for sheet_idx in range(num_sheets):
            start_i = sheet_idx * MAX_PER_SHEET
            end_i = min(start_i + MAX_PER_SHEET, total_cards_count)
            sheet_cards = processed_cards[start_i:end_i]
            sheet_card_count = len(sheet_cards)

            rows = math.ceil(sheet_card_count / COLUMNS)
            sheet_w = COLUMNS * card_width
            sheet_h = rows * card_height

            sheet_img = Image.new("RGB", (sheet_w, sheet_h), color=(0, 0, 0))

            for c_idx, card in enumerate(sheet_cards):
                c_col = c_idx % COLUMNS
                c_row = c_idx // COLUMNS
                sheet_img.paste(card, (c_col * card_width, c_row * card_height))

            sheet_filename = f"deck_sheet_{sheet_idx + 1}.png"
            sheet_filepath = os.path.join(self.output_dir, sheet_filename)
            sheet_img.save(sheet_filepath, "PNG")

            generated_sheets.append({
                "filename": sheet_filename,
                "path": sheet_filepath,
                "file_uri": to_file_uri(sheet_filepath),
                "width": COLUMNS,
                "height": rows,
                "card_count": sheet_card_count
            })

        back_path = self.generate_default_card_back(card_width, card_height, title=deck_title)
        back_uri = to_file_uri(back_path)

        # Генерируем текстуру стола с разметкой Tier List (S, A+, A, B+, B, C+, C)
        table_path = os.path.join(self.output_dir, "tier_table.png")
        create_tier_table_image(table_path)
        table_uri = to_file_uri(table_path)

        # Генерируем стилизованный тёмный фон (без 18+) от засветов на картах
        sky_path = os.path.join(self.output_dir, "custom_sky.jpg")
        create_dark_anime_skybox(sky_path)
        sky_uri = to_file_uri(sky_path)

        tts_json_path = os.path.join(self.output_dir, "tts_saved_deck.json")
        self.create_tts_deck_json(
            json_path=tts_json_path,
            sheets=generated_sheets,
            back_url=back_uri,
            deck_title=deck_title,
            table_url=table_uri,
            sky_url=sky_uri,
            is_saved_game=False
        )

        report(f"Готово! Собрано {total_cards_count} карт на {num_sheets} листах. В истории теперь: {len(self.history['seen_content_md5s'])} карт.", 100)
        self.is_running = False

        return {
            "success": True,
            "deck_title": deck_title,
            "total_cards": total_cards_count,
            "sheets": generated_sheets,
            "card_back": {
                "filename": "card_back.png",
                "path": back_path
            },
            "tier_table": {
                "filename": "tier_table.png",
                "path": table_path
            },
            "custom_sky": {
                "filename": "custom_sky.jpg",
                "path": sky_path
            },
            "saved_object_json": {
                "filename": "tts_saved_deck.json",
                "path": tts_json_path
            },
            "duplicates_filtered": duplicate_count,
            "empty_center_filtered": empty_center_skipped,
            "history_total": len(self.history["seen_content_md5s"])
        }

    def create_tts_deck_json(
        self,
        json_path: str,
        sheets: list[dict],
        back_url: str,
        deck_title: str,
        table_url: Optional[str] = None,
        sky_url: Optional[str] = None,
        is_saved_game: bool = False
    ):
        """Создаёт структуру JSON для TTS с разметкой Tier-стола и единой колодой."""
        deck_ids = []
        custom_deck_map = {}
        contained_objects = []

        card_counter = 0
        for sheet_idx, sheet in enumerate(sheets, start=1):
            face_url = sheet.get("file_uri", sheet["filename"])
            custom_deck_map[str(sheet_idx)] = {
                "FaceURL": face_url,
                "BackURL": back_url,
                "NumWidth": sheet["width"],
                "NumHeight": sheet["height"],
                "BackIsHidden": True,
                "UniqueBack": False,
                "Type": 0
            }

            for c_in_sheet in range(sheet["card_count"]):
                card_counter += 1
                card_id = sheet_idx * 100 + c_in_sheet
                deck_ids.append(card_id)
                contained_objects.append({
                    "Name": "Card",
                    "Transform": {
                        "posX": 16.5, "posY": 1.5, "posZ": 0.0,
                        "rotX": 0.0, "rotY": 180.0, "rotZ": 180.0,
                        "scaleX": 1.0, "scaleY": 1.0, "scaleZ": 1.0
                    },
                    "Nickname": f"Card {card_counter}",
                    "CardID": card_id,
                    "CustomDeck": {
                        str(sheet_idx): custom_deck_map[str(sheet_idx)]
                    }
                })

        # Колода появляется прямо в зоне "DECK / DRAW" на тир-столе (posX: 16.5)
        deck_object = {
            "Name": "DeckCustom",
            "Transform": {
                "posX": 16.5,
                "posY": 1.5,
                "posZ": 0.0,
                "rotX": 0.0,
                "rotY": 180.0,
                "rotZ": 180.0,
                "scaleX": 1.0,
                "scaleY": 1.0,
                "scaleZ": 1.0
            },
            "Nickname": deck_title,
            "Description": f"Колода из {card_counter} карт ({len(sheets)} листов).",
            "DeckIDs": deck_ids,
            "CustomDeck": custom_deck_map,
            "ContainedObjects": contained_objects
        }

        if is_saved_game:
            tts_save_data = {
                "SaveName": deck_title,
                "EpochTime": int(time.time()),
                "Date": datetime.now().strftime("%m/%d/%Y %I:%M:%S %p"),
                "VersionNumber": "v13.2.2",
                "GameMode": "Tier List Card Game",
                "GameType": "Card Game",
                "GameComplexity": "Low Complexity",
                "PlayingTime": [0, 60],
                "PlayerCounts": [1, 8],
                "Tags": ["Card Games", "Custom Deck", "Adult", "TTS_Deck_Builder"],
                "Creator": "TTS_Deck_Builder",
                "Gravity": 0.5,
                "PlayArea": 0.5,
                "Table": "Table_Custom",
                "TableURL": table_url or "",
                "Sky": "Sky_Custom" if sky_url else "Sky_Cathedral",
                "SkyURL": sky_url or "",
                "Note": f"Колода: {deck_title} ({card_counter} карт). Сгенерировано в Tabletop Simulator Deck Builder.",
                "Rules": "Разложите карточки по тирам: S, A+, A, B+, B, C+, C!",
                "TabStates": {},
                "Grid": {
                    "Type": 0,
                    "Lines": False,
                    "Color": {"r": 0.0, "g": 0.0, "b": 0.0},
                    "Opacity": 0.75,
                    "ThickLines": False,
                    "Snapping": False,
                    "Offset": False,
                    "BothSnapping": False,
                    "xSize": 0.5,
                    "ySize": 0.5,
                    "PosOffset": {"x": 0.0, "y": 1.0, "z": 0.0}
                },
                "Lighting": {
                    "LightIntensity": 0.44,
                    "LightColor": {"r": 0.96, "g": 0.94, "b": 1.0},
                    "AmbientIntensity": 0.85,
                    "AmbientType": 1,
                    "AmbientSkyColor": {"r": 0.28, "g": 0.24, "b": 0.38},
                    "AmbientEquatorColor": {"r": 0.20, "g": 0.18, "b": 0.28},
                    "AmbientGroundColor": {"r": 0.12, "g": 0.10, "b": 0.16},
                    "ReflectionIntensity": 0.15,
                    "LutIndex": 0,
                    "LutContribution": 1.0,
                    "LutURL": ""
                },
                "Hands": {
                    "Enable": True,
                    "DisableUnused": False,
                    "Hiding": 0,
                    "HandStates": []
                },
                "ObjectStates": [deck_object]
            }
        else:
            tts_save_data = {
                "SaveName": deck_title,
                "Date": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                "VersionNumber": "v13.2.2",
                "GameMode": "",
                "GameType": "",
                "GameComplexity": "",
                "Tags": ["Custom Deck", "Cards", "TTS_Deck_Builder"],
                "Creator": "TTS_Deck_Builder",
                "Gravity": 0.5,
                "PlayArea": 0.5,
                "DateSaved": datetime.now().isoformat(),
                "ObjectStates": [deck_object]
            }

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(tts_save_data, f, indent=2, ensure_ascii=False)
