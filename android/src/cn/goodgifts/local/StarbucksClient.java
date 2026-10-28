package cn.goodgifts.local;

import android.os.Build;
import android.os.SystemClock;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.InputStream;
import java.net.CookieManager;
import java.net.CookiePolicy;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

class StarbucksClient {
    static final String SMS = "/app-bff-api/sms/send", LOGIN = "/app-bff-api/login";
    static final String TOKEN = "/app-bff-lgc/default/login/access_token", ACCOUNT = "/app-bff-api/auth/v2/user/detail";
    static final String COUPONS = "/bff2/user/coupon/list", HOST = "https://bff.starbucks.com.cn";
    final Protocol protocol;
    final String installation;
    final CookieManager cookies = new CookieManager(null, CookiePolicy.ACCEPT_ORIGINAL_SERVER);
    String token, name = "我的星巴克账号", sentPhone;
    boolean confirmed;
    long smsAt;

    StarbucksClient(Protocol protocol, String installation) { this.protocol = protocol; this.installation = installation; }
    boolean authenticated() { return token != null && confirmed; }
    void logout() { token = null; confirmed = false; cookies.getCookieStore().removeAll(); }

    JSONObject device() throws Exception {
        return Protocol.object("os_name", "Android", "language", "zh", "timezone", "Asia/Shanghai",
            "id", installation, "app_version", Protocol.VERSION, "manufacturer", Build.MANUFACTURER,
            "model", Build.MODEL, "os_version", Build.VERSION.RELEASE,
            "user_agent", "Android com.starbucks.cn.platform/" + Protocol.VERSION + " (" + Build.MODEL + "; " + Build.VERSION.RELEASE + ")");
    }

    JSONObject request(String path, JSONObject payload, boolean form, Map<String, String> extra) throws Exception {
        Protocol.Frame frame = protocol.frame(path, payload, form);
        HttpURLConnection connection = (HttpURLConnection) new URL(HOST + path).openConnection();
        connection.setRequestMethod("POST"); connection.setConnectTimeout(8000); connection.setReadTimeout(25000);
        connection.setInstanceFollowRedirects(false); connection.setDoOutput(true);
        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("Accept", "application/json"); headers.put("X-API-Version", "2.0");
        headers.put("User-Agent", "com.starbucks.cn/" + Protocol.VERSION);
        headers.put("x-phone-type", "ANDROID"); headers.put("x-app-version", Protocol.VERSION);
        headers.put("x-device-id", installation); headers.put("x-language-type", "zh_CN"); headers.put("lang", "zh_CN");
        if (token != null) headers.put("Authorization", "Bearer " + token);
        headers.putAll(frame.headers); if (extra != null) headers.putAll(extra);
        for (Map.Entry<String, String> entry : headers.entrySet()) connection.setRequestProperty(entry.getKey(), entry.getValue());
        URI uri = URI.create(HOST + path);
        for (Map.Entry<String, List<String>> entry : cookies.get(uri, java.util.Collections.emptyMap()).entrySet()) {
            connection.setRequestProperty(entry.getKey(), android.text.TextUtils.join("; ", entry.getValue()));
        }
        byte[] body = frame.body.getBytes(StandardCharsets.UTF_8); connection.setFixedLengthStreamingMode(body.length);
        try {
            try (java.io.OutputStream output = connection.getOutputStream()) { output.write(body); }
            int status = connection.getResponseCode(); cookies.put(uri, connection.getHeaderFields());
            if (status == 401 || status == 403) { logout(); throw new ApiException("登录已失效或官方要求额外验证，请重新登录。", "starbucks_auth_required"); }
            if (status == 429) throw new ApiException("官方限制了请求频率，请稍后再试。", "starbucks_rate_limited");
            if (status < 200 || status >= 300 || status == 298) throw new ApiException("官方网关返回 HTTP " + status, "starbucks_gateway_error");
            try (InputStream input = connection.getInputStream()) {
                return protocol.decode(new String(Protocol.read(input), StandardCharsets.UTF_8), frame.encrypted);
            }
        } catch (java.io.IOException error) {
            throw new ApiException("无法连接官方接口，请检查网络。短信请求不会自动重试。", "starbucks_unreachable");
        } finally { connection.disconnect(); }
    }

    static Object unwrap(JSONObject response) throws Exception {
        Object raw = response.opt("code");
        if (!(raw instanceof Integer || raw instanceof Long) || ((Number) raw).longValue() < 200 || ((Number) raw).longValue() >= 300)
            throw new ApiException(response.optString("message", "官方接口未返回成功状态"), "starbucks_api_error");
        return response.opt("data");
    }

    synchronized void sendSms(String phone) throws Exception {
        unwrap(request(SMS, Protocol.object("device", device(), "phoneNumber", phone, "template", "create-cn", "token", ""), false, null));
        sentPhone = phone; smsAt = SystemClock.elapsedRealtime();
    }

    synchronized void login(String phone, String pin) throws Exception {
        if (!phone.equals(sentPhone) || SystemClock.elapsedRealtime() - smsAt > 600000) throw new ApiException("请先为当前号码获取验证码", "sms_required");
        if (!pin.matches("[0-9]{4}")) throw new ApiException("请输入四位验证码", "invalid_code");
        logout();
        Map<String, String> extra = new LinkedHashMap<>(); extra.put("loginType", "PIN_CODE");
        Object login = unwrap(request(LOGIN, Protocol.object("cellPhone", phone, "pin", pin, "device", device()), true, extra));
        String code = login instanceof JSONObject ? ((JSONObject) login).optString("token", "") : "";
        if (code.trim().isEmpty()) throw new ApiException("官方未返回授权码，请按提示完成验证", "login_challenge_required");
        extra.clear(); extra.put("x-msr-version", "2");
        Object result = unwrap(request(TOKEN, Protocol.object("code", code, "grant_type", "authorization_code"), false, extra));
        String access = result instanceof JSONObject ? ((JSONObject) result).optString("access_token", "") : "";
        if (access.trim().isEmpty()) throw new ApiException("官方未返回登录令牌", "access_token_missing");
        token = access;
        try {
            extra.clear(); extra.put("Accept", "application/vnd.api+json");
            JSONObject profile = request(ACCOUNT, Protocol.object("adCode", ""), false, extra).optJSONObject("data");
            if (profile == null || profile.isNull("extra")) throw new ApiException("官方尚未确认账号资料", "account_probe_failed");
            name = profile.optString("nickName", profile.optString("userName", "我的星巴克账号"));
            if (name.isEmpty()) name = "我的星巴克账号";
            confirmed = true; sentPhone = null;
        } catch (Exception error) { logout(); throw error; }
    }

    synchronized List<JSONObject> coupons() throws Exception {
        if (!authenticated()) throw new ApiException("请先重新登录此账号", "login_required");
        List<JSONObject> raw = new ArrayList<>();
        for (int page = 1; page <= 30; page++) {
            Object data = unwrap(request(COUPONS, Protocol.object("base", Protocol.object("memberLevel", "", "adCode", "", "birthday", ""),
                "params", Protocol.object("scene", "ME", "channel", "ALL", "pageNum", page)), false, null));
            if (!(data instanceof JSONObject)) throw new ApiException("官方券列表格式发生变化", "coupon_schema_changed");
            JSONObject item = (JSONObject) data; JSONArray list = item.optJSONArray("couponList");
            if (list == null || !(item.opt("hasNext") instanceof Boolean)) throw new ApiException("官方券列表或分页格式发生变化", "coupon_schema_changed");
            for (int i = 0; i < list.length(); i++) raw.add(list.getJSONObject(i));
            if (!item.getBoolean("hasNext")) return normalize(raw);
        }
        throw new ApiException("券列表超过支持的分页上限，未返回不完整列表", "coupon_pagination_limit");
    }

    static List<JSONObject> normalize(List<JSONObject> raw) throws Exception {
        List<JSONObject> output = new ArrayList<>(); Set<String> seen = new HashSet<>(), ids = new HashSet<>();
        for (JSONObject record : raw) flatten(record, output, seen, ids);
        return output;
    }

    static void flatten(JSONObject record, List<JSONObject> output, Set<String> seen, Set<String> ids) throws Exception {
        JSONObject entity = record.optJSONObject("couponEntity"), style = record.optJSONObject("couponStyle");
        if (entity != null) {
            JSONObject qr = style == null ? null : style.optJSONObject("qrcode");
            String status = string(entity, "status"), payload = qr == null ? "" : string(qr, "content");
            if (payload.trim().isEmpty() || "EXPIRED".equals(status) || "USED".equals(status)) payload = "";
            String id = string(entity, "memberBenefitId"); if (id.isEmpty()) id = string(entity, "id");
            String expiry = string(entity, "expiredDate"); if (expiry.isEmpty() && style != null) expiry = string(style, "couponExpiryTimeStr");
            String description = string(entity, "usageDescription"); if (description.isEmpty() && qr != null) description = string(qr, "reminder");
            JSONObject item = Protocol.object("id", id, "title", string(entity, "title"), "status", status,
                "expires_at", expiry, "description", description, "qr_payload", payload);
            String digest = hash(Protocol.json(item));
            if (!seen.contains(digest)) {
                seen.add(digest); if (id.isEmpty()) id = "coupon-" + digest.substring(0, 24);
                String unique = id; int index = 0;
                while (ids.contains(unique)) unique = id + "~" + digest.substring(0, 16) + (index++ == 0 ? "" : "-" + index);
                ids.add(unique); item.put("id", unique); output.add(item);
            }
        } else if (!record.has("couponSetList")) throw new ApiException("官方券字段结构发生变化", "coupon_schema_changed");
        if (record.has("couponSetList") && !record.isNull("couponSetList")) {
            JSONArray children = record.getJSONArray("couponSetList");
            for (int i = 0; i < children.length(); i++) flatten(children.getJSONObject(i), output, seen, ids);
        }
    }

    static String string(JSONObject object, String key) throws Exception {
        if (object.isNull(key)) return "";
        Object value = object.get(key);
        if (!(value instanceof String)) throw new ApiException("官方券字段类型发生变化", "coupon_schema_changed");
        return (String) value;
    }

    static String hash(String value) throws Exception {
        byte[] bytes = MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
        StringBuilder out = new StringBuilder(); for (byte b : bytes) out.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
        return out.toString();
    }
}

class ApiException extends Exception {
    final String code;
    ApiException(String message, String code) { super(message); this.code = code; }
}
