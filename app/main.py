import os
import sys

# Androidでのパス設定
if "ANDROID_ARGUMENT" in os.environ:
    from android.storage import primary_external_storage_path
    ext = primary_external_storage_path()
    base_dir = os.path.join(ext, "AlteredTcg")
    os.makedirs(base_dir, exist_ok=True)
    os.environ.setdefault("CSV_PATH", os.path.join(base_dir, "AlteredTcgJp.csv"))
    os.environ.setdefault("TEMPLATE_DOCX_PATH", os.path.join(base_dir, "和訳シールテンプレ.docx"))
    os.environ.setdefault("OUTPUT_DIR", base_dir)

from kivy.app import App
from kivy.uix.screenmanager import ScreenManager, FadeTransition
from kivy.core.text import LabelBase
from kivy.resources import resource_add_path

# 日本語フォントを登録（assetsフォルダに NotoSansJP-Regular.ttf を置く）
_font_path = os.path.join(os.path.dirname(__file__), "assets", "NotoSansJP-Regular.ttf")
if os.path.exists(_font_path):
    LabelBase.register(name="NotoSansJP", fn_regular=_font_path)
else:
    # フォントがなければデフォルトフォントにフォールバック
    LabelBase.register(name="NotoSansJP", fn_regular=LabelBase._fonts["Roboto"]["regular"])

from app.screens.home_screen import HomeScreen
from app.screens.camera_screen import CameraScreen
from app.screens.result_screen import ResultScreen
from app.screens.sticker_screen import StickerScreen


class AlteredTcgApp(App):
    def build(self):
        sm = ScreenManager(transition=FadeTransition())
        sm.add_widget(HomeScreen(name="home"))
        sm.add_widget(CameraScreen(name="camera_normal", mode="normal"))
        sm.add_widget(CameraScreen(name="camera_sticker", mode="sticker"))
        sm.add_widget(ResultScreen(name="result"))
        sm.add_widget(StickerScreen(name="sticker"))
        return sm


if __name__ == "__main__":
    AlteredTcgApp().run()
