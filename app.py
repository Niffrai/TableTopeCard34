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
import webbrowser
import zipfile
from pathlib import Path
from PIL import Image
from generator import DeckGenerator, to_file_uri, create_tier_table_image, create_dark_anime_skybox

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

def append_log(message: str):
    timestamp = time.strftime("%H:%M:%S")
    app_state["logs"].append(f"[{timestamp}] {message}")
    if len(app_state["logs"]) > 200:
        app_state["logs"].pop(0)

def generation_worker(params: dict):
    global app_state
    app_state["is_running"] = True
    app_state["percent"] = 0
    app_state["error"] = None
    app_state["result"] = None
    app_state["logs"] = []

    append_log("Запуск генерации колоды...")

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

        total_cards = int(params.get("total_cards", 456))
        year_from = int(params["year_from"]) if params.get("year_from") else None
        year_to = int(params["year_to"]) if params.get("year_to") else None
        card_w = int(params.get("card_width", 300))
        card_h = int(params.get("card_height", 420))
        deck_title = params.get("deck_title", "Custom Deck") or "Custom Deck"
        source = params.get("source", "xbooru")
        rule34_user_id = params.get("rule34_user_id", "")
        rule34_api_key = params.get("rule34_api_key", "")
        threads = int(params.get("threads", 8))

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
            progress_callback=on_progress
        )

        if res.get("success"):
            app_state["result"] = res
            app_state["status_message"] = "Генерация успешно завершена!"
            append_log(f"Успешно создано карт: {res.get('total_cards')}. Листов: {len(res.get('sheets', []))}")
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
    if sys.platform.startswith("win"):
        os.startfile(path)
    elif sys.platform.startswith("darwin"):
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


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

                    decks.append({
                        "id": f"game:{fname}",
                        "type": "game",
                        "type_label": "🎮 Игра (Save & Load)",
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

                        decks.append({
                            "id": f"saved_object:{fname}",
                            "type": "saved_object",
                            "type_label": "📦 Saved Object",
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


class RequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
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

            deck_title = "Custom_Deck"
            if app_state.get("result") and app_state["result"].get("deck_title"):
                deck_title = app_state["result"]["deck_title"]
            clean_title = re.sub(r'[\\/*?:"<>| ]', '_', deck_title)

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
                    "2. Нажмите кнопку «📥 Импортировать от друга» в верхней шапке.\r\n"
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
            self.send_header("Content-Disposition", f'attachment; filename="{clean_title}_TTS_Package.zip"')
            self.send_header("Content-Length", str(len(zip_bytes)))
            self.end_headers()
            self.wfile.write(zip_bytes)
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
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

            t = threading.Thread(target=generation_worker, args=(payload,), daemon=True)
            t.start()
            self._send_json({"success": True, "message": "Генерация запущена"})
            return

        if url_path == "/api/stop":
            generator.should_stop = True
            self._send_json({"success": True, "message": "Остановка запрошена"})
            return

        if url_path == "/api/open-folder":
            try:
                open_folder(OUTPUT_DIR)
                self._send_json({"success": True})
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)
            return

        if url_path == "/api/open-photos-folder":
            try:
                photos_dir = os.path.join(OUTPUT_DIR, "photos")
                os.makedirs(photos_dir, exist_ok=True)
                open_folder(photos_dir)
                self._send_json({"success": True})
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)
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
                deck_name = re.sub(r'(_TTS_Package)?\.zip$', '', raw_filename, flags=re.I).strip() or "Friend_Deck"
                clean_name = re.sub(r'[\\/*?:"<>| ]', '_', deck_name)

                zip_bytes = base64.b64decode(zip_b64)
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
                            "file_uri": to_file_uri(str(fpath)),
                            "width": cols,
                            "height": rows,
                            "card_count": cols * rows
                        })

                back_file = assets_dir / "card_back.png"
                back_uri = to_file_uri(str(back_file)) if back_file.exists() else ""

                table_file = assets_dir / "tier_table.png"
                if not table_file.exists():
                    create_tier_table_image(str(table_file))
                table_uri = to_file_uri(str(table_file))

                sky_file = assets_dir / "custom_sky.jpg"
                if not sky_file.exists():
                    create_dark_anime_skybox(str(sky_file))
                sky_uri = to_file_uri(str(sky_file))

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

                if back_file.exists():
                    shutil.copy2(back_file, save_png_file)

                self._send_json({
                    "success": True,
                    "save_num": next_num,
                    "save_file": str(save_json_file.name),
                    "save_name": deck_name
                })
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

        # --- ОТКРЫТИЕ ПАПКИ СОХРАНЕНИЙ TTS ---
        if url_path == "/api/open-tts-folder":
            saves_dir = find_tts_saves_dir()
            if saves_dir and saves_dir.exists():
                try:
                    open_folder(str(saves_dir))
                    self._send_json({"success": True})
                except Exception as e:
                    self._send_json({"success": False, "error": str(e)}, 500)
            else:
                self._send_json({"success": False, "error": "Папка Saves не найдена"}, 404)
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
