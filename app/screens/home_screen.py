from kivy.uix.screenmanager import Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label


class HomeScreen(Screen):
    """ホーム画面：モード選択"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        layout = BoxLayout(orientation="vertical", padding=30, spacing=20)

        title = Label(
            text="Altered TCG\n和訳スキャナー",
            font_name="NotoSansJP",
            font_size="26sp",
            bold=True,
            halign="center",
            size_hint=(1, 0.3),
        )
        layout.add_widget(title)

        normal_btn = Button(
            text="カードをスキャンして和訳を見る",
            font_name="NotoSansJP",
            font_size="18sp",
            size_hint=(1, 0.2),
            background_color=(0.2, 0.5, 1, 1),
        )
        normal_btn.bind(on_press=self.on_normal_mode)
        layout.add_widget(normal_btn)

        sticker_btn = Button(
            text="和訳シールを作成する",
            font_name="NotoSansJP",
            font_size="18sp",
            size_hint=(1, 0.2),
            background_color=(0.1, 0.7, 0.3, 1),
        )
        sticker_btn.bind(on_press=self.on_sticker_mode)
        layout.add_widget(sticker_btn)

        layout.add_widget(Label(size_hint=(1, 0.3)))  # spacer
        self.add_widget(layout)

    def on_normal_mode(self, instance):
        self.manager.current = "camera_normal"

    def on_sticker_mode(self, instance):
        self.manager.current = "camera_sticker"
