from kivy.uix.screenmanager import Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.camera import Camera
from kivy.uix.popup import Popup
from kivy.core.image import Image as CoreImage
from kivy.graphics.texture import Texture
import io
import threading


class CameraScreen(Screen):
    """カメラ画面（通常モード・シール作成モード共通）"""

    def __init__(self, mode="normal", **kwargs):
        super().__init__(**kwargs)
        self.mode = mode  # "normal" or "sticker"
        self._build_ui()

    def _build_ui(self):
        layout = BoxLayout(orientation="vertical")

        # モード表示
        mode_label = Label(
            text="[通常モード] カードを映してスキャン" if self.mode == "normal"
                 else "[シール作成モード] カードを映してスキャン",
            size_hint=(1, 0.08),
            font_name="NotoSansJP",
        )
        layout.add_widget(mode_label)

        # カメラプレビュー
        self.camera = Camera(play=True, resolution=(1280, 720), size_hint=(1, 0.75))
        layout.add_widget(self.camera)

        # ステータスラベル
        self.status_label = Label(
            text="スキャンボタンを押してください",
            size_hint=(1, 0.07),
            font_name="NotoSansJP",
        )
        layout.add_widget(self.status_label)

        # ボタン行
        btn_row = BoxLayout(size_hint=(1, 0.1), spacing=10, padding=5)

        scan_btn = Button(
            text="スキャン",
            font_name="NotoSansJP",
            background_color=(0.2, 0.6, 1, 1),
        )
        scan_btn.bind(on_press=self.on_scan)
        btn_row.add_widget(scan_btn)

        back_btn = Button(
            text="戻る",
            font_name="NotoSansJP",
            size_hint_x=0.3,
        )
        back_btn.bind(on_press=self.on_back)
        btn_row.add_widget(back_btn)

        layout.add_widget(btn_row)
        self.add_widget(layout)

    def on_scan(self, instance):
        """シャッターを切ってカードを認識する"""
        self.status_label.text = "認識中..."
        # カメラのフレームをキャプチャ
        texture = self.camera.texture
        if texture is None:
            self.status_label.text = "カメラの準備ができていません"
            return

        # テクスチャをJPEGバイトに変換
        buf = texture.pixels
        img_io = io.BytesIO()
        from PIL import Image
        pil_img = Image.frombytes("RGBA", texture.size, buf)
        pil_img = pil_img.convert("RGB")
        pil_img.save(img_io, format="JPEG", quality=85)
        image_bytes = img_io.getvalue()

        # 別スレッドでAPI呼び出し（UIをブロックしない）
        threading.Thread(
            target=self._process_image,
            args=(image_bytes,),
            daemon=True,
        ).start()

    def _process_image(self, image_bytes):
        from app.config import GEMINI_API_KEY, CSV_PATH, UNIQUES_CSV_PATH, OUTPUT_DIR, KEYWORDS_PATH
        from app.services.card_recognizer import recognize_card, translate_card
        from app.services.csv_manager import (
            find_translation, append_translation,
            find_unique_translation, append_unique_translation,
        )
        from app.services.docx_generator import add_card_to_docx, get_output_path
        from kivy.clock import Clock

        def update_status(text):
            Clock.schedule_once(lambda dt: setattr(self.status_label, "text", text))

        # 1. カード情報をGemini APIで認識
        update_status("カード番号を認識中...")
        card_info = recognize_card(GEMINI_API_KEY, image_bytes)
        if card_info is None:
            update_status("認識失敗。もう一度試してください。")
            return

        card_number = card_info.get("card_number", "")
        rarity = card_info.get("rarity", "")
        unique_number = card_info.get("unique_number")
        card_name = card_info.get("card_name", "")
        card_type = card_info.get("card_type", "")
        card_subtypes = card_info.get("card_subtypes", [])
        card_text = card_info.get("card_text", "")
        is_unique = (rarity == "U")

        update_status(f"認識: {card_number}-{rarity}" + (f"-{unique_number}" if is_unique else ""))

        # 2. CSVから翻訳を検索
        if is_unique:
            translation = find_unique_translation(UNIQUES_CSV_PATH, card_number, unique_number)
        else:
            translation = find_translation(CSV_PATH, card_number, rarity)

        if translation:
            name_jp = translation["日本語名"]
            ability_jp = translation["能力"]
            update_status(f"CSVから取得: {name_jp}")
        else:
            # 3. CSVになければGemini APIで翻訳生成
            update_status("翻訳を生成中...")
            result = translate_card(
                GEMINI_API_KEY, card_name, card_text,
                csv_path=CSV_PATH, keywords_path=KEYWORDS_PATH,
                card_type=card_type, card_subtypes=card_subtypes,
            )
            if result is None:
                update_status("翻訳失敗。もう一度試してください。")
                return
            name_jp = result["name_jp"]
            ability_jp = result["ability_jp"]
            if is_unique:
                append_unique_translation(UNIQUES_CSV_PATH, card_number, unique_number, name_jp, ability_jp)
            else:
                append_translation(CSV_PATH, card_number, rarity, name_jp, ability_jp)
            update_status(f"翻訳生成・CSV保存完了: {name_jp}")

        # 4. 結果を次の画面に渡す
        def go_to_result(dt):
            if self.mode == "normal":
                result_screen = self.manager.get_screen("result")
                result_screen.set_result(card_number, rarity, name_jp, ability_jp)
                self.manager.current = "result"
            else:
                # シール作成モード: docxに追記
                output_path = get_output_path(OUTPUT_DIR)
                add_card_to_docx(output_path, card_number, rarity, name_jp, ability_jp)
                sticker_screen = self.manager.get_screen("sticker")
                sticker_screen.add_card(card_number, rarity, name_jp)
                self.manager.current = "sticker"

        Clock.schedule_once(go_to_result)

    def on_back(self, instance):
        self.manager.current = "home"

    def on_leave(self):
        self.camera.play = False

    def on_enter(self):
        self.camera.play = True
        self.status_label.text = "スキャンボタンを押してください"
