from kivy.uix.screenmanager import Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView


class ResultScreen(Screen):
    """翻訳結果表示画面"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        layout = BoxLayout(orientation="vertical", padding=10, spacing=10)

        # カード番号・レアリティ
        self.header_label = Label(
            text="",
            size_hint=(1, 0.1),
            font_name="NotoSansJP",
            bold=True,
            font_size="18sp",
        )
        layout.add_widget(self.header_label)

        # 日本語名
        self.name_label = Label(
            text="",
            size_hint=(1, 0.12),
            font_name="NotoSansJP",
            font_size="20sp",
            color=(0.2, 0.7, 1, 1),
        )
        layout.add_widget(self.name_label)

        # 能力テキスト（スクロール可能）
        scroll = ScrollView(size_hint=(1, 0.65))
        self.ability_label = Label(
            text="",
            font_name="NotoSansJP",
            font_size="14sp",
            text_size=(None, None),
            halign="left",
            valign="top",
            size_hint_y=None,
        )
        self.ability_label.bind(texture_size=self.ability_label.setter("size"))
        scroll.add_widget(self.ability_label)
        layout.add_widget(scroll)

        # ボタン行
        btn_row = BoxLayout(size_hint=(1, 0.13), spacing=10)

        scan_again_btn = Button(
            text="続けてスキャン",
            font_name="NotoSansJP",
            background_color=(0.2, 0.6, 1, 1),
        )
        scan_again_btn.bind(on_press=self.on_scan_again)
        btn_row.add_widget(scan_again_btn)

        home_btn = Button(
            text="ホームへ",
            font_name="NotoSansJP",
            size_hint_x=0.35,
        )
        home_btn.bind(on_press=self.on_home)
        btn_row.add_widget(home_btn)

        layout.add_widget(btn_row)
        self.add_widget(layout)

    def set_result(self, card_number, rarity, name_jp, ability_jp):
        self.header_label.text = f"{card_number} - {rarity}"
        self.name_label.text = name_jp
        self.ability_label.text = ability_jp
        # テキストの折り返し幅を設定
        from kivy.clock import Clock
        def update_text_size(dt):
            self.ability_label.text_size = (self.ability_label.width, None)
        Clock.schedule_once(update_text_size, 0.1)

    def on_scan_again(self, instance):
        self.manager.current = "camera_normal"

    def on_home(self, instance):
        self.manager.current = "home"
