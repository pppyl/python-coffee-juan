# 好礼随行 Android

构建后安装包输出到 `../dist/`。支持 Android 10 及以上版本，安装包不包含在源码提交中。

手机直接使用原生 HTTPS 客户端请求星巴克接口；界面、协议加密签名、登录、多账号会话、券码与图片处理全部在手机内运行。不连接电脑、不使用 Flask 或手机代理，也不需要安装官方客户端作为中转。

## 功能

- 多个账号分别通过四位短信验证码登录；逐账号获取、退出，或一键获取全部账号。
- 批量获取中，一个账号失败不影响其他账号；券按账号分组，避免串号。
- 复制、保存支持 8 元、10 元、15 元、20 元、30 元及全部分类，也支持单张券。
- 复制内容包括券名、脱敏账号与二维码扫描原文。
- 保存时每张券单独生成一张带券名的 PNG，写入系统相册 `Pictures/好礼随行`，不会生成压缩包。图片还包含脱敏账号，便于区分同名券。
- 去掉大幅背景和宣传区，直接展示账号和操作按钮。
- 账号 token 使用 Android Keystore 的 AES-GCM 加密保存在应用私有目录，关闭后仍可保留账号；不保存短信验证码。重启后券列表需重新获取；官方 token 失效后重新登录对应账号。

只有联网权限，不申请短信读取、相册读取、相机或定位权限。Android 10 起，应用可以通过 MediaStore 写入自己创建的相册图片，无需广泛存储权限。

## 验证范围

构建流程包含 Java 编译、DEX 打包、v3 签名校验。正式包排除测试入口与测试券码。Python 网页版本的离线测试涵盖 HTTP 登录合约、多账号隔离、分类复制、图片导出，并可用独立识别器验证导出二维码的原文。

真机登录、剪贴板和相册写入仍需验证。Android 原生测试代码位于 `testsrc/`，仅包含合成数据，不包含在正式安装包中；可使用 `--test` 构建和执行。

## 构建

```powershell
python android/build.py
```

构建需要 JDK 21、Android SDK Platform 35 和 Build Tools 35.0.0。通过 `GOODGIFTS_SDK`、`GOODGIFTS_JDK`（或 `JAVA_HOME`）指定路径，也可在项目外的私有目录中保存 `build-tools.json`，键为 `sdk`、`jdk`。源码不包含个人机器的工具路径。

Windows 的 aapt2 不支持部分中文路径，因此构建中间文件放在用户临时目录下的 `goodgifts-android-*`，最终 APK 输出到本项目 `dist/`。客户端协议材料通过 `STARBUCKS_CLIENT_CONFIG` 或项目外的私有目录读取，构建时将 RSA 编码转换为 PKCS8 后打包；其中不应包含用户账号 token。

APK 签名密钥默认保存在项目外的私有目录 `GoodGifts/private/starbucks-local/android-signing/`，也可通过 `GOODGIFTS_SIGNING_DIR` 指定。应保留这些私有文件以便以后升级安装，切勿随源码上传。

二维码库使用 Project Nayuki 的 MIT 实现，源码与许可证头位于 `vendor/`，上游提交和文件摘要记录在 `vendor/source.json`；安装包保留许可证。
