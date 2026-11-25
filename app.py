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
import threading
import time
import urllib.parse
import traceback
import webbrowser
import zipfile
from pathlib import Path
from PIL import Image
from generator import (
    DeckGenerator, to_file_uri, create_tier_table_image, create_dark_anime_skybox,
    get_tts_cache_filename, get_tts_mods_images_dir, get_deck_stable_id, sync_deck_assets_to_mod_cache
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


def sync_and_migrate_save_if_needed(fpath: Path, data: dict, assets_path: Path, saves_dir: Path) -> dict:
    """Проверяет сохранение на наличие локальных file:/// путей.
    Если найдены ассеты, регистрирует их в Mods/Images кэше и обновляет пути в JSON на виртуальные."""
    if not assets_path or not assets_path.exists():
        return data

    needs_migration = False
    def check_file_uris(obj):
        nonlocal needs_migration
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("FaceURL", "BackURL", "TableURL", "SkyURL") and isinstance(v, str) and v.startswith("file:///"):
                    needs_migration = True
                    return
                check_file_uris(v)
        elif isinstance(obj, list):
            for item in obj:
                check_file_uris(item)
                if needs_migration:
                    return

    check_file_uris(data)

    meta_file = assets_path / "deck_meta.json"
    stable_id = None
    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as mf:
                stable_id = json.load(mf).get("deck_id")
        except Exception:
            pass

    sample_sheet = assets_path / "deck_sheet_1.png"
    if not stable_id:
        deck_name = data.get("SaveName") or assets_path.name
        stable_id = get_deck_stable_id(deck_name, sample_sheet if sample_sheet.exists() else None)

    mods_images_dir = get_tts_mods_images_dir(saves_dir)
    url_map = sync_deck_assets_to_mod_cache(assets_path, stable_id, mods_images_dir)

    if not meta_file.exists() and url_map:
        try:
            with open(meta_file, "w", encoding="utf-8") as mf:
                json.dump({
                    "deck_id": stable_id,
                    "deck_title": data.get("SaveName") or assets_path.name,
                    "base_url": f"http://tts-card-generator.local/decks/{stable_id}",
                    "version": 2
                }, mf, ensure_ascii=False, indent=2)
        except Exception:
            pass

    if needs_migration and url_map:
        def replace_file_uris(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in ("FaceURL", "BackURL", "TableURL", "SkyURL") and isinstance(v, str) and v.startswith("file:///"):
                        parsed_path = urllib.request.url2pathname(urllib.parse.urlparse(v).path)
                        bf = os.path.basename(parsed_path)
                        if bf in url_map:
                            obj[k] = url_map[bf]
                    else:
                        replace_file_uris(v)
            elif isinstance(obj, list):
                for item in obj:
                    replace_file_uris(item)

        replace_file_uris(data)
        try:
            with open(fpath, "w", encoding="utf-8") as jf:
                json.dump(data, jf, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WARN] Failed to rewrite save {fpath}: {e}")

    return data


def get_tts_decks() -> list[dict]:
    """Получает список всех колод, созданных или импортированных в Tabletop Simulator."""
    saves_dir = find_tts_saves_dir()
    if not saves_dir or not saves_dir.exists():
        return []

    decks = []

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

                    objs = data.get("ObjectStates", [])

                    png_file = fname.replace(".json", ".png")
                    png_path = saves_dir / png_file
                    thumb_url = f"/api/tts-thumb?type=game&file={png_file}" if png_path.exists() else None

                    save_name = data.get("SaveName") or fname.replace(".json", "")
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

                    clean_name = re.sub(r'[\\/*?:"<>| ]', '_', save_name)
                    assets_path = saves_dir / "Deck_Assets" / clean_name
                    if not assets_path.exists():
                        clean_alt = clean_name.replace(" ", "_")
                        if (saves_dir / "Deck_Assets" / clean_alt).exists():
                            assets_path = saves_dir / "Deck_Assets" / clean_alt

                    if assets_path.exists():
                        data = sync_and_migrate_save_if_needed(fpath, data, assets_path, saves_dir)

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
                        "has_assets": assets_path.exists(),
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

                        mtime = os.path.getmtime(fpath)
                        date_str = data.get("Date") or time.strftime("%d.%m.%Y %H:%M", time.localtime(mtime))

                        card_count = 0
                        sheets_count = 0
                        for obj in objs:
                            if obj.get("Name") == "DeckCustom":
                                card_count += len(obj.get("DeckIDs", []))
                                sheets_count += len(obj.get("CustomDeck", {}))

                        clean_name = re.sub(r'[\\/*?:"<>| ]', '_', save_name)
                        assets_path = so_dir / "Deck_Assets" / clean_name
                        if not assets_path.exists():
                            clean_alt = clean_name.replace(" ", "_")
                            if (so_dir / "Deck_Assets" / clean_alt).exists():
                                assets_path = so_dir / "Deck_Assets" / clean_alt

                        if assets_path.exists():
                            data = sync_and_migrate_save_if_needed(fpath, data, assets_path, saves_dir)

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
                            "has_assets": assets_path.exists(),
                            "mtime": mtime
                        })
                    except Exception:
                        continue
        except Exception:
            pass

    decks.sort(key=lambda d: d.get("mtime", 0), reverse=True)
    return decks


def delete_single_deck(deck_type: str, filename: str) -> tuple[bool, str]:
    """Удаляет одну колоду (файл .json + .png и связанные ассеты)."""
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
    try:
        with open(json_file, "r", encoding="utf-8") as jf:
            d = json.load(jf)
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
            specific_assets = assets_base / clean_name
            if specific_assets.exists():
                shutil.rmtree(specific_assets, ignore_errors=True)

        return True, "Колода успешно удалена"
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

    meta_file = specific_assets / "deck_meta.json"
    stable_id = None
    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as mf:
                stable_id = json.load(mf).get("deck_id")
        except Exception:
            pass

    sample_sheet = specific_assets / "deck_sheet_1.png" if specific_assets.exists() else None
    if not stable_id:
        stable_id = get_deck_stable_id(clean_name, sample_sheet if (sample_sheet and sample_sheet.exists()) else None)

    mods_images_dir = get_tts_mods_images_dir(saves_dir)
    url_map = sync_deck_assets_to_mod_cache(specific_assets, stable_id, mods_images_dir) if specific_assets.exists() else {}

    def replace_file_uris(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("FaceURL", "BackURL", "TableURL", "SkyURL") and isinstance(v, str):
                    if v.startswith("file:///"):
                        parsed_path = urllib.request.url2pathname(urllib.parse.urlparse(v).path)
                        bf = os.path.basename(parsed_path)
                        if bf in url_map:
                            obj[k] = url_map[bf]
                else:
                    replace_file_uris(v)
        elif isinstance(obj, list):
            for item in obj:
                replace_file_uris(item)

    replace_file_uris(save_data)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("deck_save.json", json.dumps(save_data, ensure_ascii=False, indent=2))
        z.writestr("deck_meta.json", json.dumps({
            "deck_id": stable_id,
            "deck_title": save_name,
            "base_url": f"http://tts-card-generator.local/decks/{stable_id}",
            "version": 2
        }, ensure_ascii=False, indent=2))

        png_path = target_dir / clean_file.replace(".json", ".png")
        if png_path.exists():
            z.write(str(png_path), f"{clean_name}.png")

        added_files = {"deck_save.json", "deck_meta.json", f"{clean_name.lower()}.png"}
        if specific_assets.exists():
            for f in os.listdir(specific_assets):
                if f.lower() in added_files:
                    continue
                fp = specific_assets / f
                if fp.is_file() and fp.suffix.lower() in (".png", ".jpg", ".jpeg"):
                    z.write(str(fp), f)
                    added_files.add(f.lower())

        def extract_file_uris(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in ("FaceURL", "BackURL", "TableURL", "SkyURL") and isinstance(v, str) and v.startswith("file:///"):
                        parsed_path = urllib.request.url2pathname(urllib.parse.urlparse(v).path)
                        if os.path.exists(parsed_path) and os.path.isfile(parsed_path):
                            bf = os.path.basename(parsed_path)
                            if bf.lower() not in added_files:
                                z.write(parsed_path, bf)
                                added_files.add(bf.lower())
                    else:
                        extract_file_uris(v)
            elif isinstance(obj, list):
                for item in obj:
                    extract_file_uris(item)

        extract_file_uris(save_data)

    buf.seek(0)
    return buf, clean_name, None


def unpack_and_import_deck_zip(zip_bytes: bytes, raw_filename: str, saves_dir: Path) -> dict:
    """Распаковывает один ZIP-архив колоды, регистрирует ассеты в кэше TTS (Mods/Images) и Tabletop Simulator."""
    deck_name = re.sub(r'(_TTS_Package)?\.zip$', '', raw_filename, flags=re.I).strip() or "Friend_Deck"
    clean_name = re.sub(r'[\\/*?:"<>| ]', '_', deck_name)
    assets_dir = saves_dir / "Deck_Assets" / clean_name
    assets_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as z:
        for item in z.infolist():
            fname = os.path.basename(item.filename)
            if not fname:
                continue
            if fname.endswith((".png", ".jpg", ".jpeg", ".json")):
                target_file = assets_dir / fname
                with open(target_file, "wb") as out_f:
                    out_f.write(z.read(item.filename))

    meta_file = assets_dir / "deck_meta.json"
    stable_id = None
    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as mf:
                mdata = json.load(mf)
                stable_id = mdata.get("deck_id")
                if mdata.get("deck_title"):
                    deck_name = mdata["deck_title"]
        except Exception:
            pass

    sample_sheet = assets_dir / "deck_sheet_1.png"
    if not stable_id:
        stable_id = get_deck_stable_id(deck_name, sample_sheet if sample_sheet.exists() else None)

    mods_images_dir = get_tts_mods_images_dir(saves_dir)
    url_map = sync_deck_assets_to_mod_cache(assets_dir, stable_id, mods_images_dir)

    try:
        with open(assets_dir / "deck_meta.json", "w", encoding="utf-8") as mf:
            json.dump({
                "deck_id": stable_id,
                "deck_title": deck_name,
                "base_url": f"http://tts-card-generator.local/decks/{stable_id}",
                "version": 2
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
    if (assets_dir / "deck_save.json").exists():
        try:
            with open(assets_dir / "deck_save.json", "r", encoding="utf-8") as jf:
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
            save_data["SaveName"] = deck_name
            with open(save_json_file, "w", encoding="utf-8") as out_jf:
                json.dump(save_data, out_jf, ensure_ascii=False, indent=2)
            has_deck_save_json = True
        except Exception as e:
            print(f"[WARN] Failed to use deck_save.json: {e}")
            has_deck_save_json = False

    if not has_deck_save_json:
        sheets = []
        for fname in sorted(os.listdir(assets_dir)):
            if fname.startswith("deck_sheet_") and fname.endswith(".png"):
                fpath = assets_dir / fname
                with Image.open(fpath) as img:
                    cols = 10
                    rows = math.ceil(img.height / 420)
                sheets.append({
                    "filename": fname,
                    "path": str(fpath),
                    "file_uri": url_map.get(fname, to_file_uri(str(fpath))),
                    "width": cols,
                    "height": rows,
                    "card_count": cols * rows
                })

        back_file = assets_dir / "card_back.png"
        back_uri = url_map.get("card_back.png", to_file_uri(str(back_file)) if back_file.exists() else "")

        table_file = assets_dir / "tier_table.png"
        if not table_file.exists():
            create_tier_table_image(str(table_file))
            url_map.update(sync_deck_assets_to_mod_cache(assets_dir, stable_id, mods_images_dir))
        table_uri = url_map.get("tier_table.png", to_file_uri(str(table_file)))

        sky_file = assets_dir / "custom_sky.jpg"
        if not sky_file.exists():
            create_dark_anime_skybox(str(sky_file))
            url_map.update(sync_deck_assets_to_mod_cache(assets_dir, stable_id, mods_images_dir))
        sky_uri = url_map.get("custom_sky.jpg", to_file_uri(str(sky_file)))

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
        icon_path = assets_dir / icon_name
        if icon_path.exists():
            shutil.copy2(icon_path, save_png_file)
            break

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
        except Exception as exc:
            self.handle_uncaught_error(exc)

    def do_POST(self):
        try:
            self._handle_post()
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
                clean_name = re.sub(r'[\\/*?:"<>| ]', '_', deck_name)

                # Копируем изображения в папку Saves/Deck_Assets/<clean_name>
                assets_dir = saves_dir / "Deck_Assets" / clean_name
                assets_dir.mkdir(parents=True, exist_ok=True)

                sheets = []
                for fname in sorted(os.listdir(OUTPUT_DIR)):
                    if fname.startswith("deck_sheet_") and fname.endswith(".png"):
                        src = os.path.join(OUTPUT_DIR, fname)
                        dst = assets_dir / fname
                        shutil.copy2(src, dst)

                        with Image.open(src) as img:
                            cols = 10
                            rows = math.ceil(img.height / 420)

                        sheets.append({
                            "filename": fname,
                            "path": str(dst),
                            "file_uri": to_file_uri(str(dst)),
                            "width": cols,
                            "height": rows,
                            "card_count": cols * rows
                        })

                if app_state.get("result") and app_state["result"].get("sheets"):
                    for i, orig_sheet in enumerate(app_state["result"]["sheets"]):
                        if i < len(sheets):
                            sheets[i]["card_count"] = orig_sheet["card_count"]
                            sheets[i]["width"] = orig_sheet["width"]
                            sheets[i]["height"] = orig_sheet["height"]

                # Копируем рубашку
                src_back = os.path.join(OUTPUT_DIR, "card_back.png")
                dst_back = assets_dir / "card_back.png"
                if os.path.exists(src_back):
                    shutil.copy2(src_back, dst_back)
                back_uri = to_file_uri(str(dst_back))

                # Копируем текстуру стола Tier List
                src_table = os.path.join(OUTPUT_DIR, "tier_table.png")
                dst_table = assets_dir / "tier_table.png"
                if os.path.exists(src_table):
                    shutil.copy2(src_table, dst_table)
                else:
                    create_tier_table_image(str(dst_table))
                table_uri = to_file_uri(str(dst_table))

                # Копируем стилизованный тёмный фон (custom_sky.jpg)
                src_sky = os.path.join(OUTPUT_DIR, "custom_sky.jpg")
                dst_sky = assets_dir / "custom_sky.jpg"
                if os.path.exists(src_sky):
                    shutil.copy2(src_sky, dst_sky)
                else:
                    create_dark_anime_skybox(str(dst_sky))
                sky_uri = to_file_uri(str(dst_sky))

                sample_sheet = assets_dir / "deck_sheet_1.png"
                stable_id = get_deck_stable_id(deck_name, sample_sheet if sample_sheet.exists() else None)
                mods_images_dir = get_tts_mods_images_dir(saves_dir)
                url_map = sync_deck_assets_to_mod_cache(assets_dir, stable_id, mods_images_dir)

                with open(assets_dir / "deck_meta.json", "w", encoding="utf-8") as mf:
                    json.dump({
                        "deck_id": stable_id,
                        "deck_title": deck_name,
                        "base_url": f"http://tts-card-generator.local/decks/{stable_id}",
                        "version": 2
                    }, mf, ensure_ascii=False, indent=2)

                for sheet in sheets:
                    fname = sheet["filename"]
                    if fname in url_map:
                        sheet["file_uri"] = url_map[fname]

                back_uri = url_map.get("card_back.png", back_uri)
                table_uri = url_map.get("tier_table.png", table_uri)
                sky_uri = url_map.get("custom_sky.jpg", sky_uri)

                # Находим следующий свободный номер для TS_Save_X.json
                existing_numbers = []
                for f in os.listdir(saves_dir):
                    m = re.search(r"^TS_Save_(\d+)\.json$", f)
                    if m:
                        existing_numbers.append(int(m.group(1)))

                next_num = max(existing_numbers, default=0) + 1
                save_json_file = saves_dir / f"TS_Save_{next_num}.json"
                save_png_file = saves_dir / f"TS_Save_{next_num}.png"

                # Создаём файл игры Tabletop Simulator со столом Tier List и тёмным фоном
                generator.create_tts_deck_json(
                    json_path=str(save_json_file),
                    sheets=sheets,
                    back_url=back_uri,
                    deck_title=deck_name,
                    table_url=table_uri,
                    sky_url=sky_uri,
                    is_saved_game=True
                )

                # Иконка сохранения
                if os.path.exists(src_back):
                    shutil.copy2(src_back, save_png_file)

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

                assets_dir = saved_objects_dir / "Deck_Assets" / clean_name
                assets_dir.mkdir(parents=True, exist_ok=True)

                sheets = []
                for fname in sorted(os.listdir(OUTPUT_DIR)):
                    if fname.startswith("deck_sheet_") and fname.endswith(".png"):
                        src = os.path.join(OUTPUT_DIR, fname)
                        dst = assets_dir / fname
                        shutil.copy2(src, dst)

                        with Image.open(src) as img:
                            cols = 10
                            rows = math.ceil(img.height / 420)

                        sheets.append({
                            "filename": fname,
                            "path": str(dst),
                            "file_uri": to_file_uri(str(dst)),
                            "width": cols,
                            "height": rows,
                            "card_count": cols * rows
                        })

                if app_state.get("result") and app_state["result"].get("sheets"):
                    for i, orig_sheet in enumerate(app_state["result"]["sheets"]):
                        if i < len(sheets):
                            sheets[i]["card_count"] = orig_sheet["card_count"]
                            sheets[i]["width"] = orig_sheet["width"]
                            sheets[i]["height"] = orig_sheet["height"]

                src_back = os.path.join(OUTPUT_DIR, "card_back.png")
                dst_back = assets_dir / "card_back.png"
                if os.path.exists(src_back):
                    shutil.copy2(src_back, dst_back)
                back_uri = to_file_uri(str(dst_back))

                sample_sheet = assets_dir / "deck_sheet_1.png"
                stable_id = get_deck_stable_id(deck_name, sample_sheet if sample_sheet.exists() else None)
                mods_images_dir = get_tts_mods_images_dir(saved_objects_dir)
                url_map = sync_deck_assets_to_mod_cache(assets_dir, stable_id, mods_images_dir)

                with open(assets_dir / "deck_meta.json", "w", encoding="utf-8") as mf:
                    json.dump({
                        "deck_id": stable_id,
                        "deck_title": deck_name,
                        "base_url": f"http://tts-card-generator.local/decks/{stable_id}",
                        "version": 2
                    }, mf, ensure_ascii=False, indent=2)

                for sheet in sheets:
                    fname = sheet["filename"]
                    if fname in url_map:
                        sheet["file_uri"] = url_map[fname]

                back_uri = url_map.get("card_back.png", back_uri)

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

            try:
                # 1. Удаляем игры TS_Save_X, созданные генератором
                for fname in os.listdir(saves_dir):
                    if fname.startswith("TS_Save_") and fname.endswith(".json"):
                        fpath = saves_dir / fname
                        try:
                            with open(fpath, "r", encoding="utf-8") as jf:
                                data = json.load(jf)
                            if is_generator_deck(data):
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
                                    os.remove(fpath)
                                    png_path = saved_objects_dir / fname.replace(".json", ".png")
                                    if png_path.exists():
                                        os.remove(png_path)
                                    deleted_objects += 1
                            except Exception:
                                pass
                    # Очищаем папку ассетов Saved Objects/Deck_Assets
                    so_assets = saved_objects_dir / "Deck_Assets"
                    if so_assets.exists():
                        shutil.rmtree(so_assets, ignore_errors=True)

                self._send_json({
                    "success": True,
                    "deleted_games": deleted_games,
                    "deleted_objects": deleted_objects
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
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))


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
