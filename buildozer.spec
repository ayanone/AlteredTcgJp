[app]
title = Altered TCG 和訳スキャナー
package.name = alteredtcgjp
package.domain = jp.alteredtcg
source.dir = .
source.include_exts = py,png,jpg,kv,ttf,csv,docx
source.include_patterns = app/assets/*,AlteredTcgJp.csv,和訳シールテンプレ.docx
version = 0.1

requirements = python3,kivy==2.3.0,pillow,lxml,python-docx

orientation = portrait
fullscreen = 0

android.permissions = CAMERA,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE,INTERNET
android.api = 33
android.minapi = 26
android.ndk = 25b
android.archs = arm64-v8a

[buildozer]
log_level = 2
