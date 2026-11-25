import base64
import http.server
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import traceback
import webbrowser
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from PIL import Image
from generator import (
    DeckGenerator, to_file_uri, create_tier_table_image, create_dark_anime_skybox,
    get_tts_cache_filename, get_tts_mods_images_dir, get_tts_mods_raw_dir, get_tts_meta_dir,
    get_deck_stable_id, sync_deck_assets_to_mod_cache, sync_files_to_mod_cache,
    natural_sheet_sort_key, detect_sheet_geometry
)

PORT = 5000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Глобальное состояние генерации
app_state = {
    "is_running": False,
    "status_message": "Готов к работе",
    "percent": 0,
    "result": None,
    "error": None,
    "logs": []
}

generator = DeckGenerator(OUTPUT_DIR)
LAST_RESULT_FILE = os.path.join(OUTPUT_DIR, "last_deck_result.json")


def append_log(message: str):
    timestamp = time.strftime("%H:%M:%S")
    app_state["logs"].append(f"[{timestamp}] {message}")
    if len(app_state["logs"]) > 200:
        app_state["logs"].pop(0)


def validate_current_result():
    """Проверяет, существуют ли ещё на диске файлы сгенерированных листов колоды."""
    global app_state
    if app_state.get("result"):
        sheets = app_state["result"].get("sheets", [])
        all_exist = bool(sheets) and all(
            os.path.exists(s.get("path", "")) and os.path.isfile(s.get("path", ""))
            for s in sheets
        )
        if not all_exist:
            app_state["result"] = None
            if not app_state.get("is_running"):
                app_state["status_message"] = "Готов к работе"
                app_state["percent"] = 0
            if os.path.exists(LAST_RESULT_FILE):
                try:
                    os.remove(LAST_RESULT_FILE)
                except Exception:
                    pass


def load_cached_last_result():
    global app_state
    if os.path.exists(LAST_RESULT_FILE):
        try:
            with open(LAST_RESULT_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if saved and saved.get("success"):
                sheets = saved.get("sheets", [])
                all_exist = bool(sheets) and all(
                    os.path.exists(s.get("path", "")) and os.path.isfile(s.get("path", ""))
                    for s in sheets
                )
                if all_exist:
                    if not saved.get("deck_title"):
                        saved_deck_json = os.path.join(OUTPUT_DIR, "tts_saved_deck.json")
                        if os.path.exists(saved_deck_json):
                            try:
                                with open(saved_deck_json, "r", encoding="utf-8") as f:
                                    sdata = json.load(f)
                                    saved["deck_title"] = sdata.get("SaveName") or (sdata.get("ObjectStates") and sdata["ObjectStates"][0].get("Nickname"))
                            except Exception:
                                pass
                    app_state["result"] = saved
                    app_state["percent"] = 100
                    app_state["status_message"] = "Предыдущая колода готова"
                    total = saved.get("total_cards", 0)
                    sheets_cnt = len(sheets)
                    append_log(f"Восстановлена предыдущая колода: {total} карт ({sheets_cnt} листов)")
                else:
                    try:
                        os.remove(LAST_RESULT_FILE)
                    except Exception:
                        pass
        except Exception as e:
            print(f"[Warning] Failed to load {LAST_RESULT_FILE}: {e}")


load_cached_last_result()

def generation_worker(params: dict):
    global app_state
    app_state["is_running"] = True

    def on_progress(p_data: dict):
        app_state["status_message"] = p_data.get("status", "")
        app_state["percent"] = p_data.get("percent", 0)
        append_log(p_data.get("status", ""))

    try:
        pos_raw = params.get("positive_tags", "")
        if isinstance(pos_raw, str):
            pos_tags = [t.strip() for t in pos_raw.replace(",", " ").split() if t.strip()]
        else:
            pos_tags = pos_raw

        neg_raw = params.get("negative_tags", "")
        if isinstance(neg_raw, str):
            neg_tags = [t.strip() for t in neg_raw.replace(",", " ").split() if t.strip()]
        else:
            neg_tags = neg_raw

        char_raw = params.get("characters", "")
        if isinstance(char_raw, str):
            chars = [c.strip() for c in char_raw.replace("\n", ",").split(",") if c.strip()]
        else:
            chars = char_raw

        artists_raw = params.get("positive_artists", params.get("artists", ""))
        if isinstance(artists_raw, str):
            artists = [a.strip() for a in artists_raw.replace("\n", ",").split(",") if a.strip()]
        else:
            artists = artists_raw

        neg_artists_raw = params.get("negative_artists", "")
        if isinstance(neg_artists_raw, str):
            neg_artists = [a.strip() for a in neg_artists_raw.replace("\n", ",").split(",") if a.strip()]
        else:
            neg_artists = neg_artists_raw

        total_cards = max(1, min(int(params.get("total_cards", 456)), 1024))
        year_from = int(params["year_from"]) if params.get("year_from") else None
        year_to = int(params["year_to"]) if params.get("year_to") else None
        card_w = int(params.get("card_width", 300))
        card_h = int(params.get("card_height", 420))
        deck_title = params.get("deck_title", "Custom Deck") or "Custom Deck"
        source = params.get("source", "xbooru")
        rule34_user_id = params.get("rule34_user_id", "")
        rule34_api_key = params.get("rule34_api_key", "")
        threads = int(params.get("threads", 8))

        filter_shock = params.get("filter_shock_content", True)
        if isinstance(filter_shock, str):
            filter_shock = filter_shock.lower() in ("true", "1", "yes")

        filter_fetishes = params.get("filter_weird_fetishes", True)
        if isinstance(filter_fetishes, str):
            filter_fetishes = filter_fetishes.lower() in ("true", "1", "yes")

        filter_empty_center = params.get("filter_empty_center", True)
        if isinstance(filter_empty_center, str):
            filter_empty_center = filter_empty_center.lower() in ("true", "1", "yes")

        content_mode = params.get("content_mode", "balanced")
        fit_mode = params.get("fit_mode", "cover")
        if fit_mode not in ("cover", "contain"):
            fit_mode = "cover"

        res = generator.generate_deck(
            positive_tags=pos_tags,
            negative_tags=neg_tags,
            characters=chars,
            total_cards=total_cards,
            year_from=year_from,
            year_to=year_to,
            card_width=card_w,
            card_height=card_h,
            deck_title=deck_title,
            source=source,
            rule34_user_id=rule34_user_id,
            rule34_api_key=rule34_api_key,
            threads=threads,
            progress_callback=on_progress,
            filter_shock_content=filter_shock,
            filter_weird_fetishes=filter_fetishes,
            content_mode=content_mode,
            filter_empty_center=filter_empty_center,
            fit_mode=fit_mode,
            artists=artists,
            negative_artists=neg_artists
        )

        if res.get("success"):
            if not res.get("deck_title"):
                res["deck_title"] = deck_title
            app_state["result"] = res
            app_state["status_message"] = "Генерация успешно завершена!"
            append_log(f"Успешно создано карт: {res.get('total_cards')}. Листов: {len(res.get('sheets', []))}")
            try:
                with open(LAST_RESULT_FILE, "w", encoding="utf-8") as rf:
                    json.dump(res, rf, ensure_ascii=False, indent=2)
            except Exception as e:
                append_log(f"Предупреждение: не удалось сохранить кэш результата: {e}")
        else:
            app_state["error"] = res.get("error", "Произошла неизвестная ошибка")
            app_state["status_message"] = f"Ошибка: {app_state['error']}"
            append_log(f"Ошибка: {app_state['error']}")

    except Exception as exc:
        app_state["error"] = str(exc)
        app_state["status_message"] = f"Ошибка генерации: {exc}"
        append_log(f"Критическая ошибка: {exc}")
    finally:
        app_state["is_running"] = False


def open_folder(path: str):
    """Кроссплатформенное открытие папки в проводнике / файловом менеджере."""
    abs_path = os.path.normpath(os.path.abspath(path))
    if not os.path.exists(abs_path):
        os.makedirs(abs_path, exist_ok=True)
    if sys.platform.startswith("win"):
        try:
            # Запуск explorer.exe с абсолютным путем
            subprocess.Popen(f'explorer.exe "{abs_path}"', shell=True)
        except Exception:
            try:
                subprocess.Popen(["explorer.exe", abs_path])
            except Exception:
                os.startfile(abs_path)
    elif sys.platform.startswith("darwin"):
        subprocess.Popen(["open", abs_path])
    else:
        subprocess.Popen(["xdg-open", abs_path])


def find_tts_saves_dir() -> Path | None:
    """Определяет стандартный путь к папке Saves в Tabletop Simulator (Windows, Linux, macOS)."""
    home = Path(os.path.expanduser("~"))
    candidates = [
        # Windows
        home / "Documents" / "My Games" / "Tabletop Simulator" / "Saves",
        home / "OneDrive" / "Documents" / "My Games" / "Tabletop Simulator" / "Saves",
        # Linux Native Steam
        home / ".local" / "share" / "Tabletop Simulator" / "Saves",
        # Linux Flatpak Steam
        home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Tabletop Simulator" / "Saves",
        # Linux Proton / Steam Play
        home / ".steam" / "steam" / "steamapps" / "compatdata" / "286160" / "pfx" / "drive_c" / "users" / "steamuser" / "Documents" / "My Games" / "Tabletop Simulator" / "Saves",
        home / ".local" / "share" / "Steam" / "steamapps" / "compatdata" / "286160" / "pfx" / "drive_c" / "users" / "steamuser" / "Documents" / "My Games" / "Tabletop Simulator" / "Saves",
        # macOS
        home / "Library" / "Tabletop Simulator" / "Saves",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def is_generator_deck(data: dict) -> bool:
    """Проверяет, создана ли колода или игра данным генератором."""
    if data.get("Creator") == "TTS_Deck_Builder" or "TTS_Deck_Builder" in data.get("Tags", []):
        return True
    objs = data.get("ObjectStates", [])
    for obj in objs:
        desc = obj.get("Description", "")
        if "Колода из" in desc:
            return True
        cdeck = obj.get("CustomDeck", {})
        for deck_info in cdeck.values():
            face = deck_info.get("FaceURL", "")
            if "Deck_Assets" in face or "deck_sheet_" in face or "tts-card-generator" in face:
                return True
    return False


def extract_all_urls_from_save(data: dict) -> list[str]:
    """Собирает все URL изображений (FaceURL, BackURL, TableURL, SkyURL) из JSON сохранения TTS."""
    urls = []
    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("FaceURL", "BackURL", "TableURL", "SkyURL") and isinstance(v, str) and v:
                    urls.append(v)
                else:
                    _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)
    _walk(data)
    return urls


def get_all_used_urls_in_tts(saves_dir: Path, exclude_file: Optional[Path] = None) -> set[str]:
    """Возвращает множество всех URL, используемых во всех оставшихся сохранениях и Saved Objects TTS."""
    used = set()
    candidates = []
    for p in saves_dir.glob("*.json"):
        if p.name.lower() == "savefileinfos.json":
            continue
        candidates.append(p)
    so_dir = saves_dir / "Saved Objects"
    if so_dir.exists():
        for p in so_dir.glob("*.json"):
            candidates.append(p)

    ex_res = exclude_file.resolve() if exclude_file else None
    for p in candidates:
        if ex_res and p.resolve() == ex_res:
            continue
        try:
            with open(p, "r", encoding="utf-8") as jf:
                d = json.load(jf)
            used.update(extract_all_urls_from_save(d))
        except Exception:
            pass
    return used


def cleanup_unused_cache_files(urls_to_check: list[str], saves_dir: Path, exclude_file: Optional[Path] = None) -> tuple[int, int]:
    """Удаляет файлы кэша (Mods/Images и Mods/Images Raw) для URL генератора, если они больше нигде не используются."""
    if not urls_to_check:
        return 0, 0
    mods_img = get_tts_mods_images_dir(saves_dir)
    mods_raw = get_tts_mods_raw_dir(saves_dir)
    meta_dir = get_tts_meta_dir(saves_dir)

    used_urls = get_all_used_urls_in_tts(saves_dir, exclude_file=exclude_file)
    used_cnames = {get_tts_cache_filename(u).lower() for u in used_urls if u.startswith(("http://", "https://"))}
    used_ids = set()
    for u in used_urls:
        m = re.search(r"tts-card-generator\.local/decks/([^/]+)/", u)
        if m:
            used_ids.add(m.group(1))

    deleted = 0
    freed = 0
    checked_cnames = set()
    checked_ids = set()

    for u in urls_to_check:
        if not isinstance(u, str) or "tts-card-generator.local" not in u:
            continue
        m = re.search(r"tts-card-generator\.local/decks/([^/]+)/", u)
        if m:
            did = m.group(1)
            if did not in checked_ids and did not in used_ids:
                checked_ids.add(did)
                mf = meta_dir / f"{did}.json"
                if mf.exists():
                    try:
                        os.remove(mf)
                    except Exception:
                        pass

        cname = get_tts_cache_filename(u)
        clow = cname.lower()
        if clow in checked_cnames or clow in used_cnames:
            continue
        checked_cnames.add(clow)

        img_f = mods_img / cname
        if img_f.exists() and img_f.is_file():
            try:
                freed += img_f.stat().st_size
                os.remove(img_f)
                deleted += 1
            except Exception:
                pass

        raw_stem = os.path.splitext(cname)[0]
        raw_f = mods_raw / f"{raw_stem}.rawt"
        if raw_f.exists() and raw_f.is_file():
            try:
                freed += raw_f.stat().st_size
                os.remove(raw_f)
                deleted += 1
            except Exception:
                pass

    return deleted, freed


def clean_save_file_infos(saves_dir: Path):
    """Удаляет несуществующие и служебные записи (например, из Deck_Assets) из SaveFileInfos.json."""
    info_path = saves_dir / "SaveFileInfos.json"
    if not info_path.exists():
        return
    try:
        with open(info_path, "r", encoding="utf-8") as f:
            entries = json.load(f)
        if not isinstance(entries, list):
            return
        cleaned = []
        changed = False
        for item in entries:
            dpath = item.get("Directory", "")
            norm = dpath.replace("//", "\\").replace("/", "\\")
            if "\\Deck_Assets\\" in norm or not os.path.exists(norm):
                changed = True
                continue
            cleaned.append(item)
        if changed:
            with open(info_path, "w", encoding="utf-8") as f:
                json.dump(cleaned, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def repair_deck_json_geometry(data: dict, file_map: dict[str, Path]) -> bool:
    """Проверяет и исправляет NumWidth/NumHeight/DeckIDs/ContainedObjects на основе реальных размеров листов."""
    back_path = file_map.get("card_back.png")
    objs = data.get("ObjectStates", [])
    changed = False

    for obj in objs:
        if obj.get("Name") not in ("DeckCustom", "Deck"):
            continue
        cdeck = obj.get("CustomDeck", {})
        if not cdeck:
            continue

        sheet_entries = []
        file_map_lower = {k.lower(): v for k, v in file_map.items()}
        for key, info in cdeck.items():
            face = info.get("FaceURL", "")
            if face.startswith("file:///"):
                fname = os.path.basename(urllib.request.url2pathname(urllib.parse.urlparse(face).path))
            else:
                fname = os.path.basename(face)
            unquoted = urllib.parse.unquote(fname)
            fpath = file_map.get(fname) or file_map.get(unquoted) or file_map_lower.get(fname.lower()) or file_map_lower.get(unquoted.lower())
            sheet_entries.append((key, info, fname, fpath))

        if not sheet_entries or any(e[3] is None or not Path(e[3]).exists() for e in sheet_entries):
            continue

        sheet_entries.sort(key=lambda e: natural_sheet_sort_key(e[2]))

        geoms = []
        mismatch = False
        total_expected_cards = 0
        for idx, (old_key, info, fname, fpath) in enumerate(sheet_entries, start=1):
            g = detect_sheet_geometry(fpath, back_path)
            geoms.append((idx, old_key, info, fname, g))
            total_expected_cards += g["card_count"]
            if info.get("NumWidth") != g["width"] or info.get("NumHeight") != g["height"] or str(old_key) != str(idx):
                mismatch = True

        deck_ids = obj.get("DeckIDs", [])
        if len(deck_ids) != total_expected_cards:
            mismatch = True

        if not mismatch:
            continue

        template_transform = {
            "posX": 16.5, "posY": 1.5, "posZ": 0.0,
            "rotX": 0.0, "rotY": 180.0, "rotZ": 180.0,
            "scaleX": 1.0, "scaleY": 1.0, "scaleZ": 1.0
        }
        if obj.get("ContainedObjects"):
            t0 = obj["ContainedObjects"][0].get("Transform")
            if isinstance(t0, dict):
                template_transform = t0

        new_cdeck = {}
        new_deck_ids = []
        new_contained = []
        card_counter = 0

        for idx, old_key, info, fname, g in geoms:
            entry = dict(info)
            entry["NumWidth"] = g["width"]
            entry["NumHeight"] = g["height"]
            new_cdeck[str(idx)] = entry

            for c_in_sheet in range(g["card_count"]):
                card_counter += 1
                cid = idx * 100 + c_in_sheet
                new_deck_ids.append(cid)
                new_contained.append({
                    "Name": "Card",
                    "Transform": dict(template_transform),
                    "Nickname": f"Card {card_counter}",
                    "CardID": cid,
                    "CustomDeck": {str(idx): entry}
                })

        obj["CustomDeck"] = new_cdeck
        obj["DeckIDs"] = new_deck_ids
        obj["ContainedObjects"] = new_contained
        obj["Description"] = f"Колода из {card_counter} карт ({len(geoms)} листов)."
        if data.get("Note") and "Сгенерировано" in data["Note"]:
            sname = data.get("SaveName") or obj.get("Nickname") or "Deck"
            data["Note"] = f"Колода: {sname} ({card_counter} карт). Сгенерировано в Tabletop Simulator Deck Builder."
        changed = True

    return changed


def sync_and_migrate_save_if_needed(fpath: Path, data: dict, assets_path: Optional[Path], saves_dir: Path) -> dict:
    """Переводит старые сохранения на кэш Mods/Images (без дублирования в Deck_Assets),
    дедуплицирует по контенту и исправляет геометрию листов (квадратные/пустые карты)."""
    mods_images_dir = get_tts_mods_images_dir(saves_dir)
    meta_dir = get_tts_meta_dir(saves_dir)

    file_map: dict[str, Path] = {}
    if assets_path and assets_path.exists():
        for f in os.listdir(assets_path):
            fp = assets_path / f
            if fp.is_file() and f.lower().endswith((".png", ".jpg", ".jpeg")):
                file_map[f] = fp

    old_urls = extract_all_urls_from_save(data)
    for u in old_urls:
        if u.startswith(("http://", "https://")):
            fname = os.path.basename(u)
            if fname not in file_map and fname.lower().endswith((".png", ".jpg", ".jpeg")):
                cached = mods_images_dir / get_tts_cache_filename(u)
                if cached.exists() and cached.is_file():
                    file_map[fname] = cached

    if not file_map:
        return data

    sheet_paths = [
        file_map[fn] for fn in sorted(file_map.keys(), key=natural_sheet_sort_key)
        if fn.lower().startswith("deck_sheet_")
    ]
    deck_name = data.get("SaveName") or (data.get("ObjectStates", [{}])[0].get("Nickname") if data.get("ObjectStates") else "") or fpath.stem
    stable_id = get_deck_stable_id(deck_name, sheet_paths if sheet_paths else list(file_map.values())[0])

    expected_prefix = f"http://tts-card-generator.local/decks/{stable_id}/"
    needs_url_update = any(
        u.startswith("file:///") or ("tts-card-generator.local" in u and not u.startswith(expected_prefix))
        for u in old_urls
    )
    needs_cache_copy = any(
        not (mods_images_dir / get_tts_cache_filename(f"{expected_prefix}{fn}")).exists()
        for fn in file_map
    )

    if needs_cache_copy or needs_url_update:
        url_map = sync_files_to_mod_cache(file_map, stable_id, mods_images_dir)
    else:
        url_map = {fn: f"{expected_prefix}{fn}" for fn in file_map}

    meta_file = meta_dir / f"{stable_id}.json"
    if not meta_file.exists():
        try:
            with open(meta_file, "w", encoding="utf-8") as mf:
                json.dump({
                    "deck_id": stable_id,
                    "deck_title": deck_name,
                    "base_url": f"http://tts-card-generator.local/decks/{stable_id}",
                    "version": 3
                }, mf, ensure_ascii=False, indent=2)
        except Exception:
            pass

    file_changed = False
    if needs_url_update and url_map:
        def replace_uris(obj):
            nonlocal file_changed
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in ("FaceURL", "BackURL", "TableURL", "SkyURL") and isinstance(v, str):
                        if v.startswith("file:///"):
                            bf = os.path.basename(urllib.request.url2pathname(urllib.parse.urlparse(v).path))
                        else:
                            bf = os.path.basename(v)
                        if bf in url_map and obj[k] != url_map[bf]:
                            obj[k] = url_map[bf]
                            file_changed = True
                    else:
                        replace_uris(v)
            elif isinstance(obj, list):
                for item in obj:
                    replace_uris(item)
        replace_uris(data)

    if repair_deck_json_geometry(data, file_map):
        file_changed = True

    if file_changed:
        try:
            with open(fpath, "w", encoding="utf-8") as jf:
                json.dump(data, jf, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WARN] Failed to rewrite save {fpath}: {e}")

    if needs_url_update:
        cleanup_unused_cache_files(old_urls, saves_dir)

    if assets_path and assets_path.exists():
        all_cached = all(
            (mods_images_dir / get_tts_cache_filename(url_map[fn])).exists()
            for fn in file_map if fn in url_map
        )
        if all_cached and not any(u.startswith("file:///") for u in extract_all_urls_from_save(data)):
            try:
                shutil.rmtree(assets_path, ignore_errors=True)
                parent_da = assets_path.parent
                if parent_da.exists() and not any(parent_da.iterdir()):
                    parent_da.rmdir()
                clean_save_file_infos(saves_dir)
            except Exception:
                pass

    return data


def get_tts_decks() -> list[dict]:
    """Получает список всех колод, созданных или импортированных в Tabletop Simulator."""
    saves_dir = find_tts_saves_dir()
    if not saves_dir or not saves_dir.exists():
        return []

    decks = []
    mods_images_dir = get_tts_mods_images_dir(saves_dir)

    # 1. Games (TS_Save_X.json в папке Saves/)
    try:
        for fname in os.listdir(saves_dir):
            if fname.startswith("TS_Save_") and fname.endswith(".json"):
                fpath = saves_dir / fname
                try:
                    with open(fpath, "r", encoding="utf-8") as jf:
                        data = json.load(jf)

                    if not is_generator_deck(data):
                        continue

                    save_name = data.get("SaveName") or fname.replace(".json", "")
                    clean_name = re.sub(r'[\\/*?:"<>| ]', '_', save_name)
                    assets_path = saves_dir / "Deck_Assets" / clean_name
                    if not assets_path.exists():
                        clean_alt = clean_name.replace(" ", "_")
                        if (saves_dir / "Deck_Assets" / clean_alt).exists():
                            assets_path = saves_dir / "Deck_Assets" / clean_alt

                    data = sync_and_migrate_save_if_needed(fpath, data, assets_path if assets_path.exists() else None, saves_dir)

                    objs = data.get("ObjectStates", [])
                    png_file = fname.replace(".json", ".png")
                    png_path = saves_dir / png_file
                    thumb_url = f"/api/tts-thumb?type=game&file={png_file}" if png_path.exists() else None

                    mtime = os.path.getmtime(fpath)
                    date_str = data.get("Date") or time.strftime("%d.%m.%Y %H:%M", time.localtime(mtime))

                    card_count = 0
                    sheets_count = 0
                    for obj in objs:
                        if obj.get("Name") == "DeckCustom":
                            card_count += len(obj.get("DeckIDs", []))
                            sheets_count += len(obj.get("CustomDeck", {}))
                        elif obj.get("Name") == "Card":
                            card_count += 1

                    if card_count == 0 and objs:
                        m = re.search(r"Колода из (\d+) карт", objs[0].get("Description", ""))
                        if m:
                            card_count = int(m.group(1))

                    has_tier_table = data.get("Table") == "Table_Custom" and bool(data.get("TableURL"))
                    has_sky = bool(data.get("SkyURL"))

                    urls = extract_all_urls_from_save(data)
                    has_cached = assets_path.exists() or any(
                        (mods_images_dir / get_tts_cache_filename(u)).exists()
                        for u in urls if u.startswith(("http://", "https://"))
                    )

                    decks.append({
                        "id": f"game:{fname}",
                        "type": "game",
                        "type_label": "Игра (Save & Load)",
                        "filename": fname,
                        "name": save_name,
                        "date": date_str,
                        "card_count": card_count,
                        "sheets_count": sheets_count,
                        "has_tier_table": has_tier_table,
                        "has_sky": has_sky,
                        "thumbnail_url": thumb_url,
                        "has_assets": has_cached,
                        "mtime": mtime
                    })
                except Exception:
                    continue
    except Exception:
        pass

    # 2. Saved Objects (папка Saves/Saved Objects/*.json)
    so_dir = saves_dir / "Saved Objects"
    if so_dir.exists():
        try:
            for fname in os.listdir(so_dir):
                if fname.endswith(".json"):
                    fpath = so_dir / fname
                    try:
                        with open(fpath, "r", encoding="utf-8") as jf:
                            data = json.load(jf)

                        if not is_generator_deck(data):
                            continue

                        objs = data.get("ObjectStates", [])
                        png_file = fname.replace(".json", ".png")
                        png_path = so_dir / png_file
                        thumb_url = f"/api/tts-thumb?type=saved_object&file={png_file}" if png_path.exists() else None

                        save_name = data.get("SaveName") or (objs[0].get("Nickname") if objs else fname.replace(".json", ""))
                        if not save_name:
                            save_name = fname.replace(".json", "")

                        clean_name = re.sub(r'[\\/*?:"<>| ]', '_', save_name)
                        assets_path = so_dir / "Deck_Assets" / clean_name
                        if not assets_path.exists():
                            clean_alt = clean_name.replace(" ", "_")
                            if (so_dir / "Deck_Assets" / clean_alt).exists():
                                assets_path = so_dir / "Deck_Assets" / clean_alt

                        data = sync_and_migrate_save_if_needed(fpath, data, assets_path if assets_path.exists() else None, saves_dir)
                        objs = data.get("ObjectStates", [])

                        mtime = os.path.getmtime(fpath)
                        date_str = data.get("Date") or time.strftime("%d.%m.%Y %H:%M", time.localtime(mtime))

                        card_count = 0
                        sheets_count = 0
                        for obj in objs:
                            if obj.get("Name") == "DeckCustom":
                                card_count += len(obj.get("DeckIDs", []))
                                sheets_count += len(obj.get("CustomDeck", {}))

                        urls = extract_all_urls_from_save(data)
                        has_cached = assets_path.exists() or any(
                            (mods_images_dir / get_tts_cache_filename(u)).exists()
                            for u in urls if u.startswith(("http://", "https://"))
                        )

                        decks.append({
                            "id": f"saved_object:{fname}",
                            "type": "saved_object",
                            "type_label": "Saved Object",
                            "filename": fname,
                            "name": save_name,
                            "date": date_str,
                            "card_count": card_count,
                            "sheets_count": sheets_count,
                            "has_tier_table": False,
                            "has_sky": False,
                            "thumbnail_url": thumb_url,
                            "has_assets": has_cached,
                            "mtime": mtime
                        })
                    except Exception:
                        continue
        except Exception:
            pass

    decks.sort(key=lambda d: d.get("mtime", 0), reverse=True)
    return decks


def delete_single_deck(deck_type: str, filename: str) -> tuple[bool, str]:
    """Удаляет одну колоду (файл .json + .png, Deck_Assets и неиспользуемый кэш Mods/Images + Images Raw)."""
    saves_dir = find_tts_saves_dir()
    if not saves_dir or not saves_dir.exists():
        return False, "Папка Saves не найдена"

    clean_file = os.path.basename(filename)
    if not clean_file.endswith(".json"):
        return False, "Недопустимый файл"

    if deck_type == "game":
        target_dir = saves_dir
        assets_base = saves_dir / "Deck_Assets"
    elif deck_type == "saved_object":
        target_dir = saves_dir / "Saved Objects"
        assets_base = target_dir / "Deck_Assets"
    else:
        return False, "Неизвестный тип колоды"

    json_file = target_dir / clean_file
    png_file = target_dir / clean_file.replace(".json", ".png")

    if not json_file.exists():
        return False, f"Файл {clean_file} не найден"

    clean_name = ""
    urls_in_deck = []
    extra_asset_dirs = set()
    try:
        with open(json_file, "r", encoding="utf-8") as jf:
            d = json.load(jf)
        urls_in_deck = extract_all_urls_from_save(d)
        for u in urls_in_deck:
            if u.startswith("file:///") and "Deck_Assets" in u:
                p = Path(urllib.request.url2pathname(urllib.parse.urlparse(u).path))
                if p.parent.parent.name == "Deck_Assets":
                    extra_asset_dirs.add(p.parent)
        name = d.get("SaveName") or ""
        if not name and d.get("ObjectStates"):
            name = d["ObjectStates"][0].get("Nickname") or ""
        if name:
            clean_name = re.sub(r'[\\/*?:"<>| ]', '_', name)
    except Exception:
        pass

    try:
        os.remove(json_file)
        if png_file.exists():
            os.remove(png_file)

        if clean_name and assets_base.exists():
            for cand in (clean_name, clean_name.replace(" ", "_")):
                specific_assets = assets_base / cand
                if specific_assets.exists():
                    shutil.rmtree(specific_assets, ignore_errors=True)
        for e_dir in extra_asset_dirs:
            if e_dir.exists():
                shutil.rmtree(e_dir, ignore_errors=True)
        if assets_base.exists() and not any(assets_base.iterdir()):
            try:
                assets_base.rmdir()
            except Exception:
                pass

        deleted_cache, _ = cleanup_unused_cache_files(urls_in_deck, saves_dir)
        clean_save_file_infos(saves_dir)

        msg = f"Колода успешно удалена (очищено файлов кэша: {deleted_cache})" if deleted_cache else "Колода успешно удалена"
        return True, msg
    except Exception as e:
        return False, str(e)


def export_single_tts_deck(deck_type: str, filename: str) -> tuple[io.BytesIO | None, str | None, str | None]:
    """Создает ZIP-архив отдельной колоды из Tabletop Simulator для отправки другу."""
    saves_dir = find_tts_saves_dir()
    if not saves_dir or not saves_dir.exists():
        return None, None, "Папка сохранений Tabletop Simulator не найдена"

    clean_file = os.path.basename(filename)
    if not clean_file.endswith(".json"):
        return None, None, "Недопустимый файл колоды"

    if deck_type == "game":
        target_dir = saves_dir
        assets_base = saves_dir / "Deck_Assets"
    elif deck_type == "saved_object":
        target_dir = saves_dir / "Saved Objects"
        assets_base = target_dir / "Deck_Assets"
    else:
        return None, None, "Неизвестный тип колоды"

    json_path = target_dir / clean_file
    if not json_path.exists():
        return None, None, f"Файл {clean_file} не найден"

    try:
        with open(json_path, "r", encoding="utf-8") as jf:
            save_data = json.load(jf)
    except Exception as e:
        return None, None, f"Ошибка чтения файла сохранения: {e}"

    save_name = save_data.get("SaveName") or ""
    if not save_name and save_data.get("ObjectStates"):
        save_name = save_data["ObjectStates"][0].get("Nickname") or ""
    if not save_name:
        save_name = clean_file.replace(".json", "")

    clean_name = re.sub(r'[\\/*?:"<>|\r\n\t]', '_', save_name).strip() or "Custom_Deck"

    specific_assets = assets_base / clean_name
    if not specific_assets.exists():
        clean_name_underscores = clean_name.replace(" ", "_")
        if (assets_base / clean_name_underscores).exists():
            specific_assets = assets_base / clean_name_underscores
        elif (saves_dir / "Deck_Assets" / clean_name).exists():
            specific_assets = saves_dir / "Deck_Assets" / clean_name
        elif (saves_dir / "Deck_Assets" / clean_name_underscores).exists():
            specific_assets = saves_dir / "Deck_Assets" / clean_name_underscores

    mods_images_dir = get_tts_mods_images_dir(saves_dir)
    meta_dir = get_tts_meta_dir(saves_dir)

    # Собираем все файлы колоды: из Mods/Images по ссылкам и из Deck_Assets (fallback)
    file_map: dict[str, Path] = {}
    if specific_assets.exists():
        for f in os.listdir(specific_assets):
            fp = specific_assets / f
            if fp.is_file() and f.lower().endswith((".png", ".jpg", ".jpeg")):
                file_map[f] = fp

    for u in extract_all_urls_from_save(save_data):
        if u.startswith(("http://", "https://")):
            bf = os.path.basename(u)
            if bf not in file_map and bf.lower().endswith((".png", ".jpg", ".jpeg")):
                cached = mods_images_dir / get_tts_cache_filename(u)
                if cached.exists() and cached.is_file():
                    file_map[bf] = cached
        elif u.startswith("file:///"):
            parsed_path = urllib.request.url2pathname(urllib.parse.urlparse(u).path)
            if os.path.exists(parsed_path) and os.path.isfile(parsed_path):
                bf = os.path.basename(parsed_path)
                if bf not in file_map:
                    file_map[bf] = Path(parsed_path)

    sheet_paths = [
        file_map[fn] for fn in sorted(file_map.keys(), key=natural_sheet_sort_key)
        if fn.lower().startswith("deck_sheet_")
    ]
    stable_id = get_deck_stable_id(clean_name, sheet_paths if sheet_paths else (list(file_map.values())[0] if file_map else None))

    url_map = sync_files_to_mod_cache(file_map, stable_id, mods_images_dir) if file_map else {}

    def replace_file_uris(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("FaceURL", "BackURL", "TableURL", "SkyURL") and isinstance(v, str):
                    bf = os.path.basename(urllib.request.url2pathname(urllib.parse.urlparse(v).path)) if v.startswith("file:///") else os.path.basename(v)
                    if bf in url_map:
                        obj[k] = url_map[bf]
                else:
                    replace_file_uris(v)
        elif isinstance(obj, list):
            for item in obj:
                replace_file_uris(item)

    replace_file_uris(save_data)
    if file_map:
        repair_deck_json_geometry(save_data, file_map)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("deck_save.json", json.dumps(save_data, ensure_ascii=False, indent=2))
        z.writestr("deck_meta.json", json.dumps({
            "deck_id": stable_id,
            "deck_title": save_name,
            "base_url": f"http://tts-card-generator.local/decks/{stable_id}",
            "version": 3
        }, ensure_ascii=False, indent=2))

        png_path = target_dir / clean_file.replace(".json", ".png")
        if png_path.exists():
            z.write(str(png_path), f"{clean_name}.png")

        added_files = {"deck_save.json", "deck_meta.json", f"{clean_name.lower()}.png"}
        for fname, fpath in sorted(file_map.items(), key=lambda kv: natural_sheet_sort_key(kv[0])):
            if fname.lower() in added_files:
                continue
            if fpath.exists() and fpath.is_file():
                z.write(str(fpath), fname)
                added_files.add(fname.lower())

    buf.seek(0)
    return buf, clean_name, None


def unpack_and_import_deck_zip(zip_bytes: bytes, raw_filename: str, saves_dir: Path) -> dict:
    """Распаковывает один ZIP-архив колоды, регистрирует ассеты только в кэше TTS (Mods/Images) и чинит геометрию."""
    deck_name = re.sub(r'(_TTS_Package)?\.zip$', '', raw_filename, flags=re.I).strip() or "Friend_Deck"
    clean_name = re.sub(r'[\\/*?:"<>| ]', '_', deck_name)
    mods_images_dir = get_tts_mods_images_dir(saves_dir)
    meta_dir = get_tts_meta_dir(saves_dir)

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as z:
            for item in z.infolist():
                fname = os.path.basename(item.filename)
                if not fname:
                    continue
                if fname.lower().endswith((".png", ".jpg", ".jpeg", ".json")):
                    target_file = tmp_dir / fname
                    with open(target_file, "wb") as out_f:
                        out_f.write(z.read(item.filename))

        meta_file = tmp_dir / "deck_meta.json"
        if meta_file.exists():
            try:
                with open(meta_file, "r", encoding="utf-8") as mf:
                    mdata = json.load(mf)
                    if mdata.get("deck_title"):
                        deck_name = mdata["deck_title"]
                        clean_name = re.sub(r'[\\/*?:"<>| ]', '_', deck_name)
            except Exception:
                pass

        # Гарантируем наличие стола и неба
        table_file = tmp_dir / "tier_table.png"
        if not table_file.exists():
            create_tier_table_image(str(table_file))
        sky_file = tmp_dir / "custom_sky.jpg"
        if not sky_file.exists():
            create_dark_anime_skybox(str(sky_file))

        referenced_names = set()
        if (tmp_dir / "deck_save.json").exists():
            try:
                with open(tmp_dir / "deck_save.json", "r", encoding="utf-8") as jf:
                    for u in extract_all_urls_from_save(json.load(jf)):
                        bf = os.path.basename(urllib.request.url2pathname(urllib.parse.urlparse(u).path)) if u.startswith("file:///") else os.path.basename(u)
                        if bf:
                            referenced_names.add(bf.lower())
            except Exception:
                pass

        known_assets = {"card_back.png", "tier_table.png", "custom_sky.jpg"}
        file_map = {
            f: tmp_dir / f
            for f in os.listdir(tmp_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg"))
            and (tmp_dir / f).is_file()
            and (f.lower().startswith("deck_sheet_") or f.lower() in known_assets or f.lower() in referenced_names)
        }
        sheet_paths = [
            file_map[fn] for fn in sorted(file_map.keys(), key=natural_sheet_sort_key)
            if fn.lower().startswith("deck_sheet_")
        ]
        stable_id = get_deck_stable_id(deck_name, sheet_paths if sheet_paths else (list(file_map.values())[0] if file_map else None))

        url_map = sync_files_to_mod_cache(file_map, stable_id, mods_images_dir)

        try:
            with open(meta_dir / f"{stable_id}.json", "w", encoding="utf-8") as mf:
                json.dump({
                    "deck_id": stable_id,
                    "deck_title": deck_name,
                    "base_url": f"http://tts-card-generator.local/decks/{stable_id}",
                    "version": 3
                }, mf, ensure_ascii=False, indent=2)
        except Exception:
            pass

        existing_numbers = []
        for f in os.listdir(saves_dir):
            m = re.search(r"^TS_Save_(\d+)\.json$", f)
            if m:
                existing_numbers.append(int(m.group(1)))

        next_num = max(existing_numbers, default=0) + 1
        save_json_file = saves_dir / f"TS_Save_{next_num}.json"
        save_png_file = saves_dir / f"TS_Save_{next_num}.png"

        has_deck_save_json = False
        if (tmp_dir / "deck_save.json").exists():
            try:
                with open(tmp_dir / "deck_save.json", "r", encoding="utf-8") as jf:
                    save_data = json.load(jf)

                def replace_file_uris(obj):
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if k in ("FaceURL", "BackURL", "TableURL", "SkyURL") and isinstance(v, str):
                                bf = os.path.basename(urllib.request.url2pathname(urllib.parse.urlparse(v).path)) if v.startswith("file:///") else os.path.basename(v)
                                if bf in url_map:
                                    obj[k] = url_map[bf]
                            else:
                                replace_file_uris(v)
                    elif isinstance(obj, list):
                        for item in obj:
                            replace_file_uris(item)

                replace_file_uris(save_data)
                repair_deck_json_geometry(save_data, file_map)
                save_data["SaveName"] = deck_name
                with open(save_json_file, "w", encoding="utf-8") as out_jf:
                    json.dump(save_data, out_jf, ensure_ascii=False, indent=2)
                has_deck_save_json = True
            except Exception as e:
                print(f"[WARN] Failed to use deck_save.json: {e}")
                has_deck_save_json = False

        if not has_deck_save_json:
            back_file = tmp_dir / "card_back.png"
            sheets = []
            sheet_names = sorted(
                [fn for fn in file_map if fn.lower().startswith("deck_sheet_") and fn.lower().endswith(".png")],
                key=natural_sheet_sort_key
            )
            for fname in sheet_names:
                fpath = file_map[fname]
                geom = detect_sheet_geometry(fpath, back_file if back_file.exists() else None)
                sheets.append({
                    "filename": fname,
                    "path": str(fpath),
                    "file_uri": url_map.get(fname, to_file_uri(str(fpath))),
                    "width": geom["width"],
                    "height": geom["height"],
                    "card_count": geom["card_count"]
                })

            back_uri = url_map.get("card_back.png", "")
            table_uri = url_map.get("tier_table.png", "")
            sky_uri = url_map.get("custom_sky.jpg", "")

            generator.create_tts_deck_json(
                json_path=str(save_json_file),
                sheets=sheets,
                back_url=back_uri,
                deck_title=deck_name,
                table_url=table_uri,
                sky_url=sky_uri,
                is_saved_game=True
            )

        for icon_name in (f"{clean_name}.png", "card_back.png", "deck_sheet_1.png"):
            icon_path = tmp_dir / icon_name
            if icon_path.exists():
                shutil.copy2(icon_path, save_png_file)
                break

    clean_save_file_infos(saves_dir)

    return {
        "success": True,
        "save_num": next_num,
        "save_file": str(save_json_file.name),
        "save_name": deck_name
    }


class RequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def handle_uncaught_error(self, exc: Exception):
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        tb = traceback.format_exc()
        print(f"[ERROR] Uncaught exception in request handler:\n{tb}", file=sys.stderr)
        try:
            self._send_json({
                "success": False,
                "error": f"Внутренняя ошибка сервера: {exc}",
                "traceback": tb
            }, 500)
        except Exception:
            pass

    def do_GET(self):
        try:
            self._handle_get()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass
        except Exception as exc:
            self.handle_uncaught_error(exc)

    def do_POST(self):
        try:
            self._handle_post()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass
        except Exception as exc:
            self.handle_uncaught_error(exc)

    def _handle_get(self):
        url_path = self.path.split("?")[0]

        if url_path == "/" or url_path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                self.wfile.write(f.read())
            return

        if url_path.startswith("/static/"):
            rel_file = url_path.replace("/static/", "", 1)
            full_file = os.path.join(STATIC_DIR, rel_file)
            if os.path.exists(full_file) and os.path.isfile(full_file):
                return self._serve_file(full_file)

        if url_path.startswith("/output/"):
            rel_file = url_path.replace("/output/", "", 1)
            full_file = os.path.join(OUTPUT_DIR, rel_file)
            if os.path.exists(full_file) and os.path.isfile(full_file):
                return self._serve_file(full_file)

        if url_path == "/api/status":
            validate_current_result()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(app_state, ensure_ascii=False).encode("utf-8"))
            return

        if url_path == "/api/history-stats":
            self._send_json(generator.get_history_stats())
            return

        if url_path == "/api/tts-decks":
            self._send_json({"success": True, "decks": get_tts_decks()})
            return

        if url_path == "/api/export-tts-deck":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            d_type = qs.get("type", ["game"])[0]
            d_file = qs.get("file", [""])[0] or qs.get("filename", [""])[0]
            if not d_file:
                self.send_error(400, "Параметр filename обязателен")
                return

            buf, clean_name, err = export_single_tts_deck(d_type, d_file)
            if err or not buf:
                self.send_error(404, err or "Ошибка экспорта колоды")
                return

            zip_bytes = buf.getvalue()
            clean_name = re.sub(r'[\\/*?:"<>|\r\n\t]', '_', clean_name).strip() or "Custom_Deck"
            zip_filename = f"{clean_name}.zip"
            filename_enc = urllib.parse.quote(zip_filename, encoding="utf-8")
            ascii_fallback = re.sub(r'[^\x20-\x7E]', '_', zip_filename).replace('"', '')
            if not ascii_fallback.endswith(".zip"):
                ascii_fallback = "deck.zip"
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{filename_enc}')
            self.send_header("Content-Length", str(len(zip_bytes)))
            self.send_header("Access-Control-Expose-Headers", "Content-Disposition, Content-Length")
            self.end_headers()
            CHUNK_SIZE = 128 * 1024
            for i in range(0, len(zip_bytes), CHUNK_SIZE):
                self.wfile.write(zip_bytes[i:i + CHUNK_SIZE])
            return

        if url_path == "/api/tts-thumb":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            d_type = qs.get("type", ["game"])[0]
            fname = os.path.basename(qs.get("file", [""])[0])
            if not fname.endswith(".png"):
                self.send_error(404, "Invalid image")
                return
            saves_dir = find_tts_saves_dir()
            if not saves_dir:
                self.send_error(404, "Saves dir not found")
                return
            target_dir = saves_dir if d_type == "game" else (saves_dir / "Saved Objects")
            img_file = target_dir / fname
            if img_file.exists() and img_file.is_file():
                return self._serve_file(str(img_file))
            self.send_error(404, "Image not found")
            return

        if url_path == "/api/export-zip":
            sheets = [f for f in os.listdir(OUTPUT_DIR) if f.startswith("deck_sheet_") and f.endswith(".png")]
            if not sheets:
                self.send_error(404, "Колода ещё не сгенерирована")
                return

            table_path = os.path.join(OUTPUT_DIR, "tier_table.png")
            if not os.path.exists(table_path):
                create_tier_table_image(table_path)

            sky_path = os.path.join(OUTPUT_DIR, "custom_sky.jpg")
            if not os.path.exists(sky_path):
                create_dark_anime_skybox(sky_path)

            deck_title = None
            if app_state.get("result") and app_state["result"].get("deck_title"):
                deck_title = app_state["result"]["deck_title"]
            if not deck_title:
                saved_deck_json = os.path.join(OUTPUT_DIR, "tts_saved_deck.json")
                if os.path.exists(saved_deck_json):
                    try:
                        with open(saved_deck_json, "r", encoding="utf-8") as f:
                            sdata = json.load(f)
                            deck_title = sdata.get("SaveName")
                            if not deck_title and sdata.get("ObjectStates"):
                                deck_title = sdata["ObjectStates"][0].get("Nickname")
                    except Exception:
                        pass
            if not deck_title:
                deck_title = "Custom_Deck"

            clean_title = re.sub(r'[\\/*?:"<>|\r\n\t]', '_', deck_title).strip() or "Custom_Deck"
            zip_filename = f"{clean_title}.zip"
            filename_enc = urllib.parse.quote(zip_filename, encoding="utf-8")
            ascii_fallback = re.sub(r'[^\x20-\x7E]', '_', zip_filename).replace('"', '')
            if not ascii_fallback.endswith(".zip"):
                ascii_fallback = "deck.zip"

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for fname in sorted(os.listdir(OUTPUT_DIR)):
                    if fname.endswith((".png", ".jpg")) and not fname.startswith("thumb"):
                        fpath = os.path.join(OUTPUT_DIR, fname)
                        if os.path.isfile(fpath):
                            z.write(fpath, fname)

                json_path = os.path.join(OUTPUT_DIR, "tts_saved_deck.json")
                if os.path.exists(json_path):
                    z.write(json_path, "deck_save.json")

                instruction_text = (
                    "========================================================================\r\n"
                    "ИНСТРУКЦИЯ ПО УСТАНОВКЕ КОЛОДЫ В TABLETOP SIMULATOR\r\n"
                    "========================================================================\r\n\r\n"
                    "СПОСОБ 1 (САМЫЙ ПРОСТОЙ — ЕСЛИ У ВАС ЕСТЬ ЭТО ПРИЛОЖЕНИЕ):\r\n"
                    "1. Запустите программу Tabletop Simulator Deck Builder (run.bat).\r\n"
                    "2. Нажмите кнопку «Импорт архива» в верхней шапке.\r\n"
                    "3. Выберите этот ZIP-архив.\r\n"
                    "   Приложение САМО распакует все файлы в ваш Tabletop Simulator,\r\n"
                    "   пропишет пути к текстурам стола Tier List, колоды и стилизованного тёмного фона.\r\n"
                    "4. Откройте в Tabletop Simulator: Games -> Save & Load -> загрузите сохранение!\r\n\r\n"
                    "------------------------------------------------------------------------\r\n"
                    "СПОСОБ 2 (ВРУЧНУЮ БЕЗ ПРИЛОЖЕНИЯ В ИГРЕ):\r\n"
                    "1. Распакуйте этот архив в любую удобную папку на компьютере.\r\n"
                    "2. Запустите Tabletop Simulator и создайте стол.\r\n"
                    "3. Установка стола Tier List (S, A+, A, B+, B, C+, C):\r\n"
                    "   В верхнем меню игры выберите: Objects -> Tables -> Custom Table.\r\n"
                    "   В появившемся окне укажите файл 'tier_table.png' из распакованной папки.\r\n"
                    "4. Установка тёмного стилизованного фона (устраняет засветы карт):\r\n"
                    "   В верхнем меню выберите: Objects -> Backgrounds -> Custom Background.\r\n"
                    "   В появившемся окне укажите файл 'custom_sky.jpg' из распакованной папки.\r\n"
                    "5. Установка колоды карт:\r\n"
                    "   В меню выберите: Objects -> Components -> Cards -> Custom Deck.\r\n"
                    "   - Face (Лицо): выберите 'deck_sheet_1.png'\r\n"
                    "   - Back (Рубашка): выберите 'card_back.png'\r\n"
                    "   - Width (Ширина): 10\r\n"
                    "   - Height (Высота): 7 (или число рядов)\r\n"
                    "   - Number (Количество): 70\r\n"
                    "   Нажмите Import.\r\n"
                    "6. Если в архиве несколько листов (deck_sheet_2.png и т.д.), повторите пункт 5\r\n"
                    "   для каждого листа, а затем сложите стопки вместе клавишей 'G'.\r\n"
                    "========================================================================\r\n"
                )
                z.writestr("ИНСТРУКЦИЯ_ДЛЯ_ДРУГА.txt", instruction_text.encode("utf-8"))

            zip_bytes = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{filename_enc}')
            self.send_header("Content-Length", str(len(zip_bytes)))
            self.send_header("Access-Control-Expose-Headers", "Content-Disposition, Content-Length")
            self.end_headers()
            CHUNK_SIZE = 128 * 1024
            for i in range(0, len(zip_bytes), CHUNK_SIZE):
                self.wfile.write(zip_bytes[i:i + CHUNK_SIZE])
            return

        self.send_error(404, "Not Found")

    def _handle_post(self):
        url_path = self.path.split("?")[0]
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        payload = {}
        if body:
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                pass

        if url_path == "/api/generate":
            if app_state["is_running"]:
                self._send_json({"success": False, "error": "Генерация уже идёт"}, 400)
                return

            # Мгновенно очищаем состояние логов и прогресса перед стартом потока
            app_state["is_running"] = True
            app_state["percent"] = 0
            app_state["error"] = None
            app_state["result"] = None
            app_state["logs"] = []
            app_state["status_message"] = "Запуск генерации колоды..."
            append_log("Запуск генерации колоды...")

            t = threading.Thread(target=generation_worker, args=(payload,), daemon=True)
            t.start()
            self._send_json({"success": True, "message": "Генерация запущена"})
            return

        if url_path == "/api/stop":
            generator.should_stop = True
            self._send_json({"success": True, "message": "Остановка запрошена"})
            return

        # --- УДАЛЕНИЕ ФАЙЛОВ ТЕКУЩЕЙ СГЕНЕРИРОВАННОЙ КОЛОДЫ ---
        if url_path == "/api/delete-current-deck":
            deleted_sheets = 0
            for fname in os.listdir(OUTPUT_DIR):
                if fname.startswith("deck_sheet_") and fname.endswith(".png"):
                    try:
                        os.remove(os.path.join(OUTPUT_DIR, fname))
                        deleted_sheets += 1
                    except Exception:
                        pass

            photos_dir = os.path.join(OUTPUT_DIR, "photos")
            if os.path.exists(photos_dir):
                for pf in os.listdir(photos_dir):
                    try:
                        os.remove(os.path.join(photos_dir, pf))
                    except Exception:
                        pass

            if os.path.exists(LAST_RESULT_FILE):
                try:
                    os.remove(LAST_RESULT_FILE)
                except Exception:
                    pass

            tts_json = os.path.join(OUTPUT_DIR, "tts_saved_deck.json")
            if os.path.exists(tts_json):
                try:
                    os.remove(tts_json)
                except Exception:
                    pass

            app_state["result"] = None
            app_state["status_message"] = "Готов к работе"
            app_state["percent"] = 0
            append_log("Файлы текущей колоды успешно удалены пользователем.")
            self._send_json({
                "success": True,
                "message": "Файлы текущей колоды успешно удалены",
                "deleted_sheets": deleted_sheets
            })
            return

        if url_path in ("/api/open-folder", "/api/open-res-folder"):
            try:
                abs_p = os.path.normpath(os.path.abspath(OUTPUT_DIR))
                open_folder(abs_p)
                self._send_json({"success": True, "path": abs_p})
            except Exception as e:
                self._send_json({"success": False, "error": str(e), "path": OUTPUT_DIR}, 500)
            return

        if url_path == "/api/open-project-folder":
            try:
                abs_p = os.path.normpath(os.path.abspath(BASE_DIR))
                open_folder(abs_p)
                self._send_json({"success": True, "path": abs_p})
            except Exception as e:
                self._send_json({"success": False, "error": str(e), "path": BASE_DIR}, 500)
            return

        if url_path == "/api/open-photos-folder":
            try:
                photos_dir = os.path.join(OUTPUT_DIR, "photos")
                abs_p = os.path.normpath(os.path.abspath(photos_dir))
                os.makedirs(abs_p, exist_ok=True)
                open_folder(abs_p)
                self._send_json({"success": True, "path": abs_p})
            except Exception as e:
                self._send_json({"success": False, "error": str(e), "path": OUTPUT_DIR}, 500)
            return

        if url_path == "/api/clear-history":
            generator.clear_history()
            self._send_json({"success": True, "message": "История очищена"})
            return

        # --- СОХРАНЕНИЕ КАК ИГРА С ТИР-ЛИСТ СТОЛОМ (GAMES -> SAVE & LOAD) ---
        if url_path == "/api/save-as-game":
            saves_dir = find_tts_saves_dir()
            if not saves_dir:
                self._send_json({
                    "success": False,
                    "error": "Папка Tabletop Simulator Saves не найдена в Документах."
                }, 404)
                return

            try:
                deck_name = payload.get("deck_name", "Custom_Deck").strip() or "Custom_Deck"
                mods_images_dir = get_tts_mods_images_dir(saves_dir)
                meta_dir = get_tts_meta_dir(saves_dir)

                src_back = os.path.join(OUTPUT_DIR, "card_back.png")
                src_table = os.path.join(OUTPUT_DIR, "tier_table.png")
                src_sky = os.path.join(OUTPUT_DIR, "custom_sky.jpg")

                if not os.path.exists(src_table):
                    create_tier_table_image(src_table)
                if not os.path.exists(src_sky):
                    create_dark_anime_skybox(src_sky)

                # Пытаемся взять точные метаданные листов из app_state или output/last_deck_result.json
                saved_sheets_meta = None
                if app_state.get("result") and app_state["result"].get("sheets"):
                    saved_sheets_meta = app_state["result"]["sheets"]
                else:
                    last_res_path = os.path.join(OUTPUT_DIR, "last_deck_result.json")
                    if os.path.exists(last_res_path):
                        try:
                            with open(last_res_path, "r", encoding="utf-8") as lf:
                                saved_sheets_meta = json.load(lf).get("sheets")
                        except Exception:
                            pass

                sheet_filenames = sorted(
                    [f for f in os.listdir(OUTPUT_DIR) if f.startswith("deck_sheet_") and f.endswith(".png")],
                    key=natural_sheet_sort_key
                )
                if not sheet_filenames:
                    self._send_json({"success": False, "error": "Нет сгенерированных листов колоды"}, 400)
                    return

                file_map: dict[str, Path] = {}
                sheet_paths = []
                for fname in sheet_filenames:
                    sp = Path(os.path.join(OUTPUT_DIR, fname))
                    file_map[fname] = sp
                    sheet_paths.append(sp)
                if os.path.exists(src_back):
                    file_map["card_back.png"] = Path(src_back)
                if os.path.exists(src_table):
                    file_map["tier_table.png"] = Path(src_table)
                if os.path.exists(src_sky):
                    file_map["custom_sky.jpg"] = Path(src_sky)

                stable_id = get_deck_stable_id(deck_name, sheet_paths)
                url_map = sync_files_to_mod_cache(file_map, stable_id, mods_images_dir)

                sheets = []
                for i, fname in enumerate(sheet_filenames):
                    src = str(file_map[fname])
                    if saved_sheets_meta and i < len(saved_sheets_meta) and saved_sheets_meta[i].get("filename") == fname:
                        cols = saved_sheets_meta[i]["width"]
                        rows = saved_sheets_meta[i]["height"]
                        ccount = saved_sheets_meta[i]["card_count"]
                    else:
                        geom = detect_sheet_geometry(src, src_back if os.path.exists(src_back) else None)
                        cols = geom["width"]
                        rows = geom["height"]
                        ccount = geom["card_count"]

                    sheets.append({
                        "filename": fname,
                        "path": src,
                        "file_uri": url_map.get(fname, to_file_uri(src)),
                        "width": cols,
                        "height": rows,
                        "card_count": ccount
                    })

                back_uri = url_map.get("card_back.png", to_file_uri(src_back) if os.path.exists(src_back) else "")
                table_uri = url_map.get("tier_table.png", to_file_uri(src_table))
                sky_uri = url_map.get("custom_sky.jpg", to_file_uri(src_sky))

                with open(meta_dir / f"{stable_id}.json", "w", encoding="utf-8") as mf:
                    json.dump({
                        "deck_id": stable_id,
                        "deck_title": deck_name,
                        "base_url": f"http://tts-card-generator.local/decks/{stable_id}",
                        "version": 3
                    }, mf, ensure_ascii=False, indent=2)

                existing_numbers = []
                for f in os.listdir(saves_dir):
                    m = re.search(r"^TS_Save_(\d+)\.json$", f)
                    if m:
                        existing_numbers.append(int(m.group(1)))

                next_num = max(existing_numbers, default=0) + 1
                save_json_file = saves_dir / f"TS_Save_{next_num}.json"
                save_png_file = saves_dir / f"TS_Save_{next_num}.png"

                generator.create_tts_deck_json(
                    json_path=str(save_json_file),
                    sheets=sheets,
                    back_url=back_uri,
                    deck_title=deck_name,
                    table_url=table_uri,
                    sky_url=sky_uri,
                    is_saved_game=True
                )

                if os.path.exists(src_back):
                    shutil.copy2(src_back, save_png_file)

                clean_save_file_infos(saves_dir)

                self._send_json({
                    "success": True,
                    "save_num": next_num,
                    "save_file": str(save_json_file.name),
                    "save_name": deck_name
                })
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)
            return

        # --- СОХРАНЕНИЕ КАК ОБЪЕКТ (OBJECTS -> SAVED OBJECTS) ---
        if url_path == "/api/save-as-object":
            saves_dir = find_tts_saves_dir()
            if not saves_dir:
                self._send_json({
                    "success": False,
                    "error": "Папка Tabletop Simulator Saves не найдена."
                }, 404)
                return

            try:
                saved_objects_dir = saves_dir / "Saved Objects"
                saved_objects_dir.mkdir(parents=True, exist_ok=True)

                deck_name = payload.get("deck_name", "Custom_Deck").strip() or "Custom_Deck"
                clean_name = re.sub(r'[\\/*?:"<>| ]', '_', deck_name)
                mods_images_dir = get_tts_mods_images_dir(saves_dir)
                meta_dir = get_tts_meta_dir(saves_dir)

                src_back = os.path.join(OUTPUT_DIR, "card_back.png")

                saved_sheets_meta = None
                if app_state.get("result") and app_state["result"].get("sheets"):
                    saved_sheets_meta = app_state["result"]["sheets"]
                else:
                    last_res_path = os.path.join(OUTPUT_DIR, "last_deck_result.json")
                    if os.path.exists(last_res_path):
                        try:
                            with open(last_res_path, "r", encoding="utf-8") as lf:
                                saved_sheets_meta = json.load(lf).get("sheets")
                        except Exception:
                            pass

                sheet_filenames = sorted(
                    [f for f in os.listdir(OUTPUT_DIR) if f.startswith("deck_sheet_") and f.endswith(".png")],
                    key=natural_sheet_sort_key
                )
                if not sheet_filenames:
                    self._send_json({"success": False, "error": "Нет сгенерированных листов колоды"}, 400)
                    return

                file_map: dict[str, Path] = {}
                sheet_paths = []
                for fname in sheet_filenames:
                    sp = Path(os.path.join(OUTPUT_DIR, fname))
                    file_map[fname] = sp
                    sheet_paths.append(sp)
                if os.path.exists(src_back):
                    file_map["card_back.png"] = Path(src_back)

                stable_id = get_deck_stable_id(deck_name, sheet_paths)
                url_map = sync_files_to_mod_cache(file_map, stable_id, mods_images_dir)

                sheets = []
                for i, fname in enumerate(sheet_filenames):
                    src = str(file_map[fname])
                    if saved_sheets_meta and i < len(saved_sheets_meta) and saved_sheets_meta[i].get("filename") == fname:
                        cols = saved_sheets_meta[i]["width"]
                        rows = saved_sheets_meta[i]["height"]
                        ccount = saved_sheets_meta[i]["card_count"]
                    else:
                        geom = detect_sheet_geometry(src, src_back if os.path.exists(src_back) else None)
                        cols = geom["width"]
                        rows = geom["height"]
                        ccount = geom["card_count"]

                    sheets.append({
                        "filename": fname,
                        "path": src,
                        "file_uri": url_map.get(fname, to_file_uri(src)),
                        "width": cols,
                        "height": rows,
                        "card_count": ccount
                    })

                back_uri = url_map.get("card_back.png", to_file_uri(src_back) if os.path.exists(src_back) else "")

                with open(meta_dir / f"{stable_id}.json", "w", encoding="utf-8") as mf:
                    json.dump({
                        "deck_id": stable_id,
                        "deck_title": deck_name,
                        "base_url": f"http://tts-card-generator.local/decks/{stable_id}",
                        "version": 3
                    }, mf, ensure_ascii=False, indent=2)

                obj_json_file = saved_objects_dir / f"{clean_name}.json"
                obj_png_file = saved_objects_dir / f"{clean_name}.png"

                generator.create_tts_deck_json(
                    json_path=str(obj_json_file),
                    sheets=sheets,
                    back_url=back_uri,
                    deck_title=deck_name,
                    is_saved_game=False
                )

                if os.path.exists(src_back):
                    shutil.copy2(src_back, obj_png_file)

                self._send_json({
                    "success": True,
                    "object_file": str(obj_json_file.name),
                    "object_name": deck_name
                })
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)
            return

        # --- ОЧИСТКА ВСЕХ СОХРАНЕНИЙ ИЗ TABLETOP SIMULATOR ---
        if url_path in ["/api/clear-tts", "/api/delete-all-tts-decks"]:
            saves_dir = find_tts_saves_dir()
            if not saves_dir:
                self._send_json({"success": False, "error": "Папка Saves не найдена"}, 404)
                return

            deleted_games = 0
            deleted_objects = 0
            urls_to_clean = []

            try:
                # 1. Удаляем игры TS_Save_X, созданные генератором
                for fname in os.listdir(saves_dir):
                    if fname.startswith("TS_Save_") and fname.endswith(".json"):
                        fpath = saves_dir / fname
                        try:
                            with open(fpath, "r", encoding="utf-8") as jf:
                                data = json.load(jf)
                            if is_generator_deck(data):
                                urls_to_clean.extend(extract_all_urls_from_save(data))
                                os.remove(fpath)
                                png_path = saves_dir / fname.replace(".json", ".png")
                                if png_path.exists():
                                    os.remove(png_path)
                                deleted_games += 1
                        except Exception:
                            pass

                # Очищаем папку ассетов игр Saves/Deck_Assets
                deck_assets = saves_dir / "Deck_Assets"
                if deck_assets.exists():
                    shutil.rmtree(deck_assets, ignore_errors=True)

                # 2. Удаляем Saved Objects, созданные генератором
                saved_objects_dir = saves_dir / "Saved Objects"
                if saved_objects_dir.exists():
                    for fname in os.listdir(saved_objects_dir):
                        if fname.endswith(".json"):
                            fpath = saved_objects_dir / fname
                            try:
                                with open(fpath, "r", encoding="utf-8") as jf:
                                    data = json.load(jf)
                                if is_generator_deck(data):
                                    urls_to_clean.extend(extract_all_urls_from_save(data))
                                    os.remove(fpath)
                                    png_path = saved_objects_dir / fname.replace(".json", ".png")
                                    if png_path.exists():
                                        os.remove(png_path)
                                    deleted_objects += 1
                            except Exception:
                                pass
                    so_assets = saved_objects_dir / "Deck_Assets"
                    if so_assets.exists():
                        shutil.rmtree(so_assets, ignore_errors=True)

                deleted_cache, freed_bytes = cleanup_unused_cache_files(urls_to_clean, saves_dir)

                # Также удаляем любые осиротевшие httpttscardgenerator* файлы из Mods/Images и Images Raw
                mods_img = get_tts_mods_images_dir(saves_dir)
                mods_raw = get_tts_mods_raw_dir(saves_dir)
                for folder in (mods_img, mods_raw):
                    for item in folder.iterdir():
                        if item.is_file() and "ttscardgenerator" in item.name.lower():
                            try:
                                freed_bytes += item.stat().st_size
                                os.remove(item)
                                deleted_cache += 1
                            except Exception:
                                pass

                meta_dir = get_tts_meta_dir(saves_dir)
                if meta_dir.exists():
                    shutil.rmtree(meta_dir, ignore_errors=True)

                clean_save_file_infos(saves_dir)

                self._send_json({
                    "success": True,
                    "deleted_games": deleted_games,
                    "deleted_objects": deleted_objects,
                    "deleted_cache_files": deleted_cache,
                    "freed_mb": round(freed_bytes / (1024 * 1024), 2)
                })
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)
            return

        # --- ОЧИСТКА КЭША MODS/IMAGES И RAW TEXTURES TTS ---
        if url_path == "/api/cleanup-tts-cache":
            saves_dir = find_tts_saves_dir()
            if not saves_dir:
                self._send_json({"success": False, "error": "Папка Saves не найдена"}, 404)
                return

            clear_raw = bool(payload.get("clear_raw", False))
            mods_img = get_tts_mods_images_dir(saves_dir)
            mods_raw = get_tts_mods_raw_dir(saves_dir)
            meta_dir = get_tts_meta_dir(saves_dir)

            try:
                used_urls = get_all_used_urls_in_tts(saves_dir)
                used_cnames = {get_tts_cache_filename(u).lower() for u in used_urls if u.startswith(("http://", "https://"))}
                used_stems = {os.path.splitext(c)[0] for c in used_cnames}
                used_ids = set()
                for u in used_urls:
                    m = re.search(r"tts-card-generator\.local/decks/([^/]+)/", u)
                    if m:
                        used_ids.add(m.group(1))

                removed_files = 0
                freed_bytes = 0

                # 1. Удаляем неиспользуемые файлы нашего генератора в Mods/Images
                for item in mods_img.iterdir():
                    if item.is_file() and "ttscardgenerator" in item.name.lower():
                        if item.name.lower() not in used_cnames:
                            try:
                                freed_bytes += item.stat().st_size
                                os.remove(item)
                                removed_files += 1
                            except Exception:
                                pass

                # 2. Удаляем неиспользуемые .rawt нашего генератора (или весь Images Raw если clear_raw=True)
                for item in mods_raw.iterdir():
                    if not item.is_file():
                        continue
                    stem = os.path.splitext(item.name)[0].lower()
                    should_remove = clear_raw or ("ttscardgenerator" in item.name.lower() and stem not in used_stems)
                    if should_remove:
                        try:
                            freed_bytes += item.stat().st_size
                            os.remove(item)
                            removed_files += 1
                        except Exception:
                            pass

                # 3. Удаляем неиспользуемые мета-файлы
                if meta_dir.exists():
                    for item in meta_dir.iterdir():
                        if item.is_file() and item.stem not in used_ids:
                            try:
                                os.remove(item)
                            except Exception:
                                pass

                clean_save_file_infos(saves_dir)

                self._send_json({
                    "success": True,
                    "removed_files": removed_files,
                    "freed_mb": round(freed_bytes / (1024 * 1024), 2)
                })
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)
            return

        # --- ИМПОРТ АРХИВА КОЛОДЫ ОТ ДРУГА (.ZIP) ---
        if url_path == "/api/import-zip":
            saves_dir = find_tts_saves_dir()
            if not saves_dir:
                self._send_json({"success": False, "error": "Папка Tabletop Simulator Saves не найдена."}, 404)
                return

            zip_b64 = payload.get("data")
            raw_filename = payload.get("filename", "Friend_Deck.zip")
            if not zip_b64:
                self._send_json({"success": False, "error": "Данные архива отсутствуют."}, 400)
                return

            try:
                zip_bytes = base64.b64decode(zip_b64)
                # Проверим, не является ли архив коллекцией нескольких ZIP-файлов
                with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as z_check:
                    nested_zips = [item for item in z_check.infolist() if item.filename.lower().endswith(".zip")]

                if nested_zips:
                    results = []
                    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as z_coll:
                        for n_item in nested_zips:
                            n_bytes = z_coll.read(n_item.filename)
                            res = unpack_and_import_deck_zip(n_bytes, os.path.basename(n_item.filename), saves_dir)
                            results.append(res)
                    self._send_json({
                        "success": True,
                        "is_collection": True,
                        "count": len(results),
                        "decks": results,
                        "message": f"Успешно импортировано {len(results)} колод из архива-коллекции!"
                    })
                    return
                else:
                    res = unpack_and_import_deck_zip(zip_bytes, raw_filename, saves_dir)
                    self._send_json(res)
                    return
            except Exception as e:
                self._send_json({"success": False, "error": f"Ошибка распаковки архива: {e}"}, 500)
                return

        # --- УДАЛЕНИЕ ОДНОЙ ВЫБРАННОЙ КОЛОДЫ ИЗ TTS ---
        if url_path == "/api/delete-tts-deck":
            d_type = payload.get("type", "game")
            d_file = payload.get("filename") or payload.get("file", "")
            ok, msg = delete_single_deck(d_type, d_file)
            self._send_json({"success": ok, "message": msg}, 200 if ok else 400)
            return

        # --- МАССОВОЕ ВЫБОРОЧНОЕ УДАЛЕНИЕ КОЛОД ИЗ TTS ---
        if url_path == "/api/delete-selected-tts-decks":
            decks_to_delete = payload.get("decks", [])
            if not decks_to_delete:
                self._send_json({"success": False, "error": "Список колод пуст"}, 400)
                return

            deleted_count = 0
            errors = []
            for d in decks_to_delete:
                d_type = d.get("type", "game")
                d_file = d.get("filename") or d.get("file", "")
                ok, msg = delete_single_deck(d_type, d_file)
                if ok:
                    deleted_count += 1
                else:
                    errors.append(f"{d_file}: {msg}")

            self._send_json({
                "success": True,
                "deleted": deleted_count,
                "total": len(decks_to_delete),
                "errors": errors,
                "message": f"Успешно удалено {deleted_count} из {len(decks_to_delete)} колод"
            })
            return

        # --- МАССОВЫЙ ВЫБОРОЧНЫЙ ЭКСПОРТ КОЛОД ИЗ TTS ДЛЯ ДРУГА ---
        if url_path == "/api/export-selected-tts-decks":
            decks_to_export = payload.get("decks", [])
            if not decks_to_export:
                self._send_json({"success": False, "error": "Список колод пуст"}, 400)
                return

            if len(decks_to_export) == 1:
                d = decks_to_export[0]
                d_type = d.get("type", "game")
                d_file = d.get("filename") or d.get("file", "")
                buf, clean_name, err = export_single_tts_deck(d_type, d_file)
                if err or not buf:
                    self._send_json({"success": False, "error": err or "Ошибка экспорта"}, 400)
                    return
                zip_bytes = buf.getvalue()
                clean_name = re.sub(r'[\\/*?:"<>|\r\n\t]', '_', clean_name).strip() or "Custom_Deck"
                zip_filename = f"{clean_name}.zip"
            else:
                master_buf = io.BytesIO()
                with zipfile.ZipFile(master_buf, "w", zipfile.ZIP_DEFLATED) as master_zip:
                    exported_count = 0
                    for d in decks_to_export:
                        d_type = d.get("type", "game")
                        d_file = d.get("filename") or d.get("file", "")
                        buf, clean_name, err = export_single_tts_deck(d_type, d_file)
                        if buf and not err:
                            clean_name = re.sub(r'[\\/*?:"<>|\r\n\t]', '_', clean_name).strip() or "Custom_Deck"
                            master_zip.writestr(f"{clean_name}.zip", buf.getvalue())
                            exported_count += 1

                if exported_count == 0:
                    self._send_json({"success": False, "error": "Не удалось экспортировать ни одной колоды"}, 400)
                    return

                zip_bytes = master_buf.getvalue()
                zip_filename = f"TTS_Decks_Collection_{time.strftime('%Y%m%d_%H%M%S')}.zip"

            filename_enc = urllib.parse.quote(zip_filename, encoding="utf-8")
            ascii_fallback = re.sub(r'[^\x20-\x7E]', '_', zip_filename).replace('"', '')
            if not ascii_fallback.endswith(".zip"):
                ascii_fallback = "decks.zip"
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{filename_enc}')
            self.send_header("Content-Length", str(len(zip_bytes)))
            self.send_header("Access-Control-Expose-Headers", "Content-Disposition, Content-Length")
            self.end_headers()
            CHUNK_SIZE = 128 * 1024
            for i in range(0, len(zip_bytes), CHUNK_SIZE):
                self.wfile.write(zip_bytes[i:i + CHUNK_SIZE])
            return

        # --- ОТКРЫТИЕ ПАПКИ СОХРАНЕНИЙ TTS ---
        if url_path == "/api/open-tts-folder":
            saves_dir = find_tts_saves_dir()
            if saves_dir and saves_dir.exists():
                try:
                    abs_p = os.path.normpath(os.path.abspath(str(saves_dir)))
                    open_folder(abs_p)
                    self._send_json({"success": True, "path": abs_p})
                except Exception as e:
                    self._send_json({"success": False, "error": str(e), "path": str(saves_dir)}, 500)
            else:
                self._send_json({"success": False, "error": "Папка сохранений Tabletop Simulator не найдена на компьютере"}, 404)
            return

        self.send_error(404, "Not Found")

    def _serve_file(self, filepath: str):
        content_type = "application/octet-stream"
        ext = os.path.splitext(filepath)[1].lower()
        if ext in [".html", ".htm"]:
            content_type = "text/html; charset=utf-8"
        elif ext == ".css":
            content_type = "text/css; charset=utf-8"
        elif ext == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif ext in [".png"]:
            content_type = "image/png"
        elif ext in [".jpg", ".jpeg"]:
            content_type = "image/jpeg"
        elif ext == ".json":
            content_type = "application/json; charset=utf-8"

        try:
            with open(filepath, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Error reading file: {e}")

    def _send_json(self, data: dict, status: int = 200):
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass


def main():
    server_address = ("127.0.0.1", PORT)
    httpd = http.server.ThreadingHTTPServer(server_address, RequestHandler)
    url = f"http://127.0.0.1:{PORT}"

    print("=" * 60)
    print("  Tabletop Simulator Custom Deck Builder (Web UI)")
    print(f"  Сервер запущен: {url}")
    print(f"  Выходная папка: {OUTPUT_DIR}")
    print("=" * 60)

    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка сервера...")
        httpd.server_close()


if __name__ == "__main__":
    main()
