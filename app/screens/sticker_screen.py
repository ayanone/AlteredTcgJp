from kivy.uix.screenmanager import Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.gridlayout import GridLayout
import os


class StickerScreen(Screen):
    """シール作成モード管理画面"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.scanned_cards = []
        self._output_path = None
        self._build_ui()

    def _build_ui(self):
        layout = BoxLayout(orientation="vertical", padding=10, spacing=8)

        title = Label(
            text="シール作成モード",
            size_hint=(1, 0.08),
            font_name="NotoSansJP",
            font_size="18sp",
            bold=True,
        )
        layout.add_widget(title)

        self.count_label = Label(
            text="スキャン済み: 0枚",
            size_hint=(1, 0.07),
            font_name="NotoSansJP",
        )
        layout.add_widget(self.count_label)

        # スキャン済みカード一覧
        scroll = ScrollView(size_hint=(1, 0.6))
        self.card_list = GridLayout(cols=1, size_hint_y=None, spacing=4, padding=4)
        self.card_list.bind(minimum_height=self.card_list.setter("height"))
        scroll.add_widget(self.card_list)
        layout.add_widget(scroll)

        self.status_label = Label(
            text="",
            size_hint=(1, 0.07),
            font_name="NotoSansJP",
            font_size="12sp",
            color=(0.3, 1, 0.3, 1),
        )
        layout.add_widget(self.status_label)

        # ボタン行
        btn_row = BoxLayout(size_hint=(1, 0.18), spacing=8)

        scan_btn = Button(
            text="次のカードをスキャン",
            font_name="NotoSansJP",
            background_color=(0.2, 0.6, 1, 1),
        )
        scan_btn.bind(on_press=self.on_scan_next)
        btn_row.add_widget(scan_btn)

        save_btn = Button(
            text="docxを保存して終了",
            font_name="NotoSansJP",
            background_color=(0.1, 0.7, 0.3, 1),
        )
        save_btn.bind(on_press=self.on_save)
        btn_row.add_widget(save_btn)

        layout.add_widget(btn_row)

        home_btn = Button(
            text="ホームへ戻る（保存せず）",
            font_name="NotoSansJP",
            size_hint=(1, 0.08),
            background_color=(0.5, 0.5, 0.5, 1),
        )
        home_btn.bind(on_press=self.on_home)
        layout.add_widget(home_btn)

        self.add_widget(layout)

    def add_card(self, card_number, rarity, name_jp):
        """スキャン済みカードをリストに追加"""
        from app.config import OUTPUT_DIR
        from app.services.docx_generator import get_output_path
        self._output_path = get_output_path(OUTPUT_DIR)

        self.scanned_cards.append((card_number, rarity, name_jp))
        self.count_label.text = f"スキャン済み: {len(self.scanned_cards)}枚"
        self.status_label.text = f"追加: {card_number}-{rarity} {name_jp}"

        lbl = Label(
            text=f"・{card_number}-{rarity}  {name_jp}",
            font_name="NotoSansJP",
            size_hint_y=None,
            height=32,
            halign="left",
        )
        lbl.bind(size=lambda inst, val: setattr(inst, "text_size", (val[0], None)))
        self.card_list.add_widget(lbl)

    def on_scan_next(self, instance):
        self.manager.current = "camera_sticker"

    def on_save(self, instance):
        if not self._output_path:
            self.status_label.text = "保存するカードがありません"
            return
        self.status_label.text = f"保存完了:\n{self._output_path}"

    def on_home(self, instance):
        self.manager.current = "home"
