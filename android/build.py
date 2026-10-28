"""Portable APK build using the official Android SDK tools, without Gradle.

python android/build.py [--test]
SDK downloads are kept in runtime/android-toolchain; signing material is local.
"""
from pathlib import Path
import base64
import json
import hashlib
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import zipfile

from cryptography.hazmat.primitives import serialization


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from private_paths import client_config_path, private_dir

ANDROID = ROOT / "android"
local_settings = private_dir() / "build-tools.json"
settings = json.loads(local_settings.read_text(encoding="utf-8-sig")) if local_settings.is_file() else {}
SDK = Path(os.environ.get("GOODGIFTS_SDK", settings.get("sdk", ROOT / "runtime/android-toolchain")))
jdk_location = os.environ.get("GOODGIFTS_JDK") or os.environ.get("JAVA_HOME") or settings.get("jdk")
if not jdk_location:
    raise SystemExit("请设置 GOODGIFTS_JDK 或 JAVA_HOME，指向 JDK 安装目录。")
JDK = Path(jdk_location)
TESTING = "--test" in sys.argv
build_root = Path(os.environ.get("GOODGIFTS_BUILD", Path(tempfile.gettempdir()) / ("goodgifts-android-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:12])))
BUILD = build_root / ("test" if TESTING else "release")
BUILD.mkdir(parents=True, exist_ok=True)
CLASSES = BUILD / "classes"
CLASSES.mkdir(exist_ok=True)
ASSETS = BUILD / "assets"
ASSETS.mkdir(exist_ok=True)
DEX = BUILD / "dex"
DEX.mkdir(exist_ok=True)
TOOLS = SDK / "build-tools/android-15"
PLATFORM = BUILD / "android.jar"
if not PLATFORM.exists():
    shutil.copyfile(SDK / "platform/android-35/android.jar", PLATFORM)
shutil.copytree(ANDROID / "res", BUILD / "res", dirs_exist_ok=True)


def run(args):
    subprocess.run([str(arg) for arg in args], check=True, cwd=ROOT)


index = (ROOT / "templates/index.html").read_text(encoding="utf-8-sig")
index = index.replace("{{ url_for('static', filename='app.css') }}", "/app.css")
index = index.replace("{{ url_for('static', filename='app.js') }}", "/app.js")
index = index.replace("下载图片", "保存到相册").replace("下载", "保存")
index = index.replace("图片带券名。", "每张图片单独保存到相册，带券名，不打包。")
(ASSETS / "index.html").write_text(index, encoding="utf-8")
for name in ("app.js", "app.css"):
    content = (ROOT / "static" / name).read_text(encoding="utf-8-sig")
    if name == "app.js":
        content = content.replace("'下载'", "'保存'")
    if name == "app.css":
        content += "\n.category-buttons button,.account-actions button,.coupon-actions button{min-height:34px}.hero{display:none}\n"
    (ASSETS / name).write_text(content, encoding="utf-8")

config = json.loads(client_config_path().read_text(encoding="utf-8"))
key = serialization.load_der_private_key(base64.b64decode(config["signing_private_key_b64"].replace("\\n", "")), password=None)
config["signing_private_key_b64"] = base64.b64encode(key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())).decode()
(ASSETS / "client.private.json").write_text(json.dumps(config), encoding="utf-8")
(ASSETS / "third-party.txt").write_text("QR generator: Project Nayuki, MIT License.\nhttps://github.com/nayuki/QR-Code-generator\n\n" + (ANDROID / "vendor/io/nayuki/qrcodegen/QrCode.java").read_text(encoding="utf-8").split("package io.nayuki")[0], encoding="utf-8")

manifest = (ANDROID / "AndroidManifest.xml").read_text(encoding="utf-8")
if TESTING:
    manifest = manifest.replace('<application ', '<application android:debuggable="true" ')
    manifest = manifest.replace('</manifest>', '<instrumentation android:name=".SelfTestInstrumentation" android:targetPackage="cn.goodgifts.local" />\n</manifest>')
(BUILD / "AndroidManifest.xml").write_text(manifest, encoding="utf-8")
run([TOOLS / "aapt2.exe", "compile", "--dir", BUILD / "res", "-o", BUILD / "resources.zip"])
run([TOOLS / "aapt2.exe", "link", "-I", PLATFORM, "--manifest", BUILD / "AndroidManifest.xml", "-A", ASSETS,
     "--min-sdk-version", "29", "--target-sdk-version", "35", "-o", BUILD / "resources.apk", BUILD / "resources.zip"])

sources = list((ANDROID / "src").rglob("*.java")) + list((ANDROID / "vendor").rglob("*.java"))
if TESTING:
    sources = [path for path in sources if path.name != "BuildConfig.java"] + list((ANDROID / "testsrc").rglob("*.java"))
argfile = BUILD / "javac.args"
argfile.write_text("\n".join('"' + str(path).replace('\\', '/') + '"' for path in sources), encoding="utf-8")
run([JDK / "bin/javac.exe", "-encoding", "UTF-8", "-source", "8", "-target", "8", "-Xlint:-options", "-classpath", PLATFORM,
     "-d", CLASSES, "@" + str(argfile)])
bytecode = BUILD / "bytecode.jar"
with zipfile.ZipFile(bytecode, "w") as archive:
    for path in CLASSES.rglob("*.class"):
        archive.write(path, path.relative_to(CLASSES).as_posix())
run([JDK / "bin/java.exe", "-cp", TOOLS / "lib/d8.jar", "com.android.tools.r8.D8", "--min-api", "29", "--lib", PLATFORM,
     "--output", DEX, bytecode])
unsigned = BUILD / "unsigned.apk"
unsigned.write_bytes((BUILD / "resources.apk").read_bytes())
with zipfile.ZipFile(unsigned, "a") as archive:
    for path in DEX.glob("*.dex"):
        archive.write(path, path.name, compress_type=zipfile.ZIP_STORED)
run([TOOLS / "zipalign.exe", "-f", "4", unsigned, BUILD / "aligned.apk"])

signing = Path(os.environ.get("GOODGIFTS_SIGNING_DIR", private_dir() / "android-signing"))
signing.mkdir(parents=True, exist_ok=True)
password_file = signing / "password"
if not password_file.exists():
    password_file.write_text(secrets.token_urlsafe(32), encoding="ascii")
keystore = signing / "release.jks"
if not keystore.exists():
    run([JDK / "bin/keytool.exe", "-genkeypair", "-keystore", keystore, "-storepass:file", password_file, "-keypass:file", password_file,
         "-alias", "goodgifts", "-keyalg", "RSA", "-keysize", "3072", "-validity", "10000", "-dname", "CN=GoodGifts Local"])
output = ROOT / "dist" / ("goodgifts-selftest.apk" if TESTING else "好礼随行-1.0.0.apk")
output.parent.mkdir(exist_ok=True)
run([JDK / "bin/java.exe", "-jar", TOOLS / "lib/apksigner.jar", "sign", "--ks", keystore, "--ks-key-alias", "goodgifts",
     "--ks-pass", "file:" + str(password_file), "--out", output, BUILD / "aligned.apk"])
run([JDK / "bin/java.exe", "-jar", TOOLS / "lib/apksigner.jar", "verify", "--verbose", output])
print("APK:", output)
