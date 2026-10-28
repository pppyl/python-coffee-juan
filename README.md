# 星巴克好礼券本地服务

安卓安装包支持 Android 10+。手机直接请求官方接口，多账号登录加密保存在本机，不依赖本电脑服务。手机端每张券分别保存为带券名的 PNG 到系统相册，不生成 ZIP；界面已移除大图和宣传区。详见 `android/README.md`。安装包和私有构建材料不包含在源码提交中。

Flask + Waitress，访问 `http://127.0.0.1:5700/`。支持多个星巴克中国账号分别短信登录、一键读取全部账号好礼券，并按账号展示真实二维码。运行时直接发送 HTTP 请求，无需连接手机、ADB 或手机代理。

登录流程包含发送短信、四位验证码校验、授权码换取 token 和受保护的账号资料确认，再分页读取优惠券。项目测试只使用合成手机号、验证码和不可核销的测试券码；提交包不包含真实账号的联调记录。

## 启动

```powershell
python -m pip install -r requirements.txt
python app.py
```

运行前需要私有客户端协议配置，可通过 `STARBUCKS_CLIENT_CONFIG` 指定文件。结构参考 `protocol/client.example.json`，示例不包含可用密钥。默认从操作系统的本地数据目录 `GoodGifts/private/starbucks-local/protocol/client.private.json` 读取；也兼容被 Git 忽略的旧位置 `protocol/client.private.json`。该文件是传输签名/加密配置，不是账号 token，不应随源码上传。缺失时服务报告协议未就绪。安装标识自动保存到被忽略的 `runtime/installation-id`。

页面中逐个输入手机号、获取并填写四位验证码添加账号，再点击“一键获取全部账号”。也可以单独获取、退出某个账号。复制和下载支持 8/10/15/20/30 元及全部分类；复制包含券名、脱敏账号和原始二维码内容，下载图片带券名（多张自动打包 ZIP）。已登录的浏览器刷新页面后可继续使用；服务重启或会话过期后需重新登录。

## 实现

- `starbucks_client.py`：短信、验证码校验、token 交换、账号确认和好礼券分页请求。
- `app_crypto.py`、`app_identity.py`：客户端请求签名、加解密和请求模型。使用客户端已有的 v1 签名分支；当前官方接口已实测接受。
- `coupon_protocol.py`：读取 `couponEntity` 和 `couponStyle.qrcode.content`；二维码使用官方原始券码，不使用券 ID 或券面图片代替。
- `app.py`：浏览器独立会话、CSRF、短信冷却和登录限频、券码缓存与 PNG 接口。仅监听本机回环地址。
- `templates/`、`static/`：登录与好礼券页面。

账号 token 与券码保存在服务内存，浏览器 Cookie 只保存随机会话标识；退出登录会清除该会话的券码。二维码图片接口要求同一登录会话，并使用 5 分钟的本地缓存窗口；窗口结束后可重新获取好礼券。这一缓存窗口不代表官方券码有效期。上游接口变化或官方要求额外验证时，服务会显示错误，不会伪造登录状态或券码。

## HTTP 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/health` | 本地服务健康状态 |
| GET | `/api/status` | 协议就绪状态、登录状态、CSRF token |
| POST | `/api/auth/sms` | 发送短信，JSON `phone` |
| POST | `/api/auth/login` | 验证登录，JSON `phone`、`code` |
| POST | `/api/auth/logout` | 退出全部账号 |
| POST | `/api/accounts/{id}/logout` | 单独退出一个账号 |
| GET | `/api/accounts/coupons` | 并发获取全部账号，返回每个账号的成功/失败与券列表 |
| POST | `/api/exports/copy` | 按分类返回券名、脱敏账号、二维码原文 |
| POST | `/api/exports/download` | 按分类下载带券名的 PNG，多个券为 ZIP |
| GET | `/api/coupons?account_id={id}` | 获取指定账号好礼券（省略时为最近添加的账号） |
| GET | `/api/coupons/{id}/qr.png` | 将官方券码编码为 PNG |

调用 POST 接口前读取 `/api/status`，保留 Cookie，并附上 `X-CSRFToken`。券列表 JSON 中只给出受会话保护的 `qr_url`；仅主动调用复制接口时返回所选券的原始二维码内容。

## 验证与开发材料

```powershell
python -m unittest discover -s tests -v
node --check static/app.js
```

离线测试覆盖独立 Java 加密向量、DTO/业务成功条件、登录交换与身份确认、完整分页、券码解析、会话隔离和错误行为；不会发送短信。加密向量测试需要在本机配置私有协议材料。

`tools/` 为开发期分析工具，不参与在线请求。设备序列号和调试地址必须显式传入，源码不绑定个人设备。调试取证、日志、截图和运行资料不包含在源码提交中。`h5_crypto.py` 和 `http_provider.py` 是早期分析/通用适配器；默认运行使用 `StarbucksAppHttpProvider`。仅在显式配置 `STARBUCKS_PROTOCOL_FILE` 时才使用通用适配器，`protocol.example.json` 本身不是完整可用的配置。

导出请求 JSON 包含 category（8/10/15/20/30/all）与 account_ids（当前浏览器账号 ID 列表）；单张导出额外指定 coupon_id 且只选择一个账号。导出要求已获取且缓存未过期，不会自动重发短信。

多账号、部分失败隔离、相同券 ID 跨账号隔离、分类复制/下载由离线测试和隔离的合成数据页面验证；没有为新增账号自动发送真实短信。

## 源码提交

仅提交应用源码、模板、静态资源、测试、配置示例和文档。真实手机号、验证码、券码、账号 token、设备编号、截图、日志、APK、协议密钥和 APK 签名材料均不应上传；`.gitignore` 已覆盖这些运行与构建资料。

私有文件默认保存到项目外的操作系统本地数据目录 `GoodGifts/private/starbucks-local/`，也可通过 `GOODGIFTS_PRIVATE_DIR` 指定。移动源码项目不会同时移动私有文件。

