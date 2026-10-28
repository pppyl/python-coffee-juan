package cn.goodgifts.local;

import android.content.Context;
import android.os.SystemClock;
import org.json.JSONArray;
import org.json.JSONObject;
import java.net.URLDecoder;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

final class NativeBackend {
    final Context context;
    final Protocol protocol;
    final String installation;
    final AccountVault vault;
    final LinkedHashMap<String, Account> accounts = new LinkedHashMap<>();
    StarbucksClient pending;
    String active;
    long smsAt;
    final List<Long> attempts = new ArrayList<>();

    NativeBackend(Context context) throws Exception {
        this.context = context; protocol = new Protocol(context); vault = new AccountVault(context);
        android.content.SharedPreferences prefs = context.getSharedPreferences("installation", Context.MODE_PRIVATE);
        String saved = prefs.getString("id", null);
        if (saved == null) { saved = UUID.randomUUID().toString().replace("-", ""); prefs.edit().putString("id", saved).commit(); }
        installation = saved; pending = new StarbucksClient(protocol, installation);
        JSONArray stored = vault.load();
        for (int i = 0; i < stored.length(); i++) {
            JSONObject row = stored.getJSONObject(i);
            StarbucksClient client = new StarbucksClient(protocol, installation);
            String token = row.optString("token", ""); client.token = token.isEmpty() ? null : token;
            client.name = row.optString("name", "我的星巴克账号"); client.confirmed = client.token != null;
            Account account = new Account(row.getString("id"), row.getString("phone"), client);
            accounts.put(account.id, account); active = account.id;
        }
    }

    static final class Account {
        final String id, phone; final StarbucksClient client;
        final LinkedHashMap<String, JSONObject> coupons = new LinkedHashMap<>(); long couponsAt;
        Account(String id, String phone, StarbucksClient client) { this.id = id; this.phone = phone; this.client = client; }
        String masked() { return phone.substring(0,3) + "****" + phone.substring(7); }
        JSONObject summary() throws Exception { return Protocol.object("id", id, "phone", masked(), "name", client.name, "authenticated", client.authenticated()); }
    }

    void persist() throws Exception {
        JSONArray rows = new JSONArray();
        for (Account account : accounts.values()) rows.put(Protocol.object("id", account.id, "phone", account.phone,
            "token", account.client.token == null ? "" : account.client.token, "name", account.client.name));
        vault.save(rows);
    }

    JSONArray summaries() throws Exception { JSONArray rows = new JSONArray(); for (Account account : accounts.values()) rows.put(account.summary()); return rows; }
    Account owned(String id) throws Exception {
        Account account = accounts.get(id == null || id.isEmpty() ? active : id);
        if (account == null) throw new ApiException("请先添加并登录对应账号", "login_required"); return account;
    }

    static JSONObject ok(Object... values) throws Exception { JSONObject out = Protocol.object(values); out.put("ok", true); return out; }

    synchronized JSONObject handle(String path, JSONObject body) throws Exception {
        if (path.equals("/api/status")) {
            boolean logged = false; for (Account account : accounts.values()) logged |= account.client.authenticated();
            return ok("accounts", summaries(), "authenticated", logged, "active_account_id", active,
                "provider", Protocol.object("ready", true, "mode", "android", "message", "手机独立运行 · 账号加密保存在本机"));
        }
        if (path.equals("/api/auth/sms")) {
            String phone = body.optString("phone", ""); validatePhone(phone);
            for (Account account : accounts.values()) if (phone.equals(account.phone) && account.client.authenticated()) throw new ApiException("该账号已添加，可直接获取好礼券", "account_already_added");
            long wait = 60 - (SystemClock.elapsedRealtime() - smsAt) / 1000;
            if (smsAt != 0 && wait > 0) throw new ApiException("请在 " + wait + " 秒后重试", "sms_cooldown");
            pending.sendSms(phone); smsAt = SystemClock.elapsedRealtime();
            return ok("retry_after", 60, "message", "验证码已发送，请查看短信。");
        }
        if (path.equals("/api/auth/login")) {
            String phone = body.optString("phone", ""), pin = body.optString("code", ""); validatePhone(phone);
            long now = SystemClock.elapsedRealtime(); attempts.removeIf(value -> now - value > 600000);
            if (attempts.size() >= 5) throw new ApiException("登录尝试过多，请稍后重试", "login_rate_limited");
            attempts.add(now); pending.login(phone, pin); attempts.clear();
            Account previous = null; for (Account account : accounts.values()) if (phone.equals(account.phone)) previous = account;
            String id = previous == null ? UUID.randomUUID().toString() : previous.id;
            if (previous != null) previous.client.logout();
            Account account = new Account(id, phone, pending); accounts.put(id, account); active = id;
            pending = new StarbucksClient(protocol, installation); persist();
            return ok("accounts", summaries(), "account", account.summary(), "message", "账号已添加，可以继续添加其他账号。");
        }
        if (path.equals("/api/auth/logout")) {
            for (Account account : accounts.values()) account.client.logout();
            accounts.clear(); active = null; pending.logout(); pending.sentPhone = null; persist();
            return ok("accounts", summaries(), "message", "已退出全部账号。");
        }
        if (path.matches("/api/accounts/[^/]+/logout")) {
            String id = path.split("/")[3]; Account account = owned(id); account.client.logout(); accounts.remove(id);
            active = accounts.isEmpty() ? null : accounts.keySet().iterator().next(); persist();
            return ok("accounts", summaries(), "message", "该账号已退出。");
        }
        if (path.equals("/api/accounts/coupons")) return allCoupons();
        if (path.startsWith("/api/coupons?account_id=")) {
            String id = URLDecoder.decode(path.substring(path.indexOf('=') + 1), "UTF-8");
            Account account = owned(id);
            try { JSONObject result = read(account); persist(); return result; }
            catch (Exception error) { persist(); throw error; }
        }
        if (path.equals("/api/exports/copy") || path.equals("/api/exports/download")) {
            List<JSONObject> rows = selectedRows(body);
            if (path.endsWith("copy")) {
                StringBuilder text = new StringBuilder();
                for (JSONObject row : rows) { if (text.length() != 0) text.append("\n\n"); text.append(row.getString("title")).append("\n账号：").append(row.getString("account_phone")).append('\n').append(row.getString("qr_payload")); }
                return ok("text", text.toString(), "count", rows.size());
            }
            int saved = 0;
            for (JSONObject row : rows) { try { CouponImages.save(context, row); saved++; } catch (Exception error) {} }
            if (saved == 0) throw new ApiException("图片保存失败，请检查手机存储空间", "save_failed");
            return ok("count", saved, "message", "已保存 " + saved + "/" + rows.size() + " 张带券名的图片到相册「好礼随行」。");
        }
        throw new ApiException("不支持的操作", "unsupported_operation");
    }

    JSONObject read(Account account) throws Exception {
        if (!account.client.authenticated()) throw new ApiException("此账号需要重新登录", "login_required");
        try {
            List<JSONObject> values = account.client.coupons(); account.coupons.clear(); JSONArray publicRows = new JSONArray();
            for (JSONObject row : values) {
                account.coupons.put(row.getString("id"), row);
                JSONObject publicRow = new JSONObject(row.toString()); publicRow.remove("qr_payload");
                if (!row.optString("qr_payload", "").isEmpty()) publicRow.put("qr_url", CouponImages.dataUrl(row.getString("qr_payload")));
                publicRows.put(publicRow);
            }
            account.couponsAt = SystemClock.elapsedRealtime();
            return ok("account", account.summary(), "coupons", publicRows, "message", "已读取该账号的好礼券。");
        } catch (Exception error) { account.coupons.clear(); throw error; }
    }

    JSONObject allCoupons() throws Exception {
        if (accounts.isEmpty()) throw new ApiException("请先添加账号", "login_required");
        ExecutorService pool = Executors.newFixedThreadPool(Math.min(4, accounts.size()));
        List<Callable<JSONObject>> tasks = new ArrayList<>();
        for (Account account : accounts.values()) tasks.add(() -> {
            try { return read(account); }
            catch (Exception error) { return Protocol.object("ok", false, "account", account.summary(), "coupons", new JSONArray(), "message", error instanceof ApiException ? error.getMessage() : "该账号获取失败，请重试"); }
        });
        JSONArray results = new JSONArray(); int succeeded = 0, total = 0;
        try {
            for (Future<JSONObject> future : pool.invokeAll(tasks)) {
                JSONObject result = future.get(); results.put(result);
                if (result.getBoolean("ok")) { succeeded++; total += result.getJSONArray("coupons").length(); }
            }
        } finally { pool.shutdown(); }
        persist();
        return ok("results", results, "accounts", summaries(), "total_accounts", accounts.size(), "succeeded", succeeded,
            "failed", accounts.size() - succeeded, "total_coupons", total, "message", "已获取 " + succeeded + "/" + accounts.size() + " 个账号的好礼券。");
    }

    List<JSONObject> selectedRows(JSONObject body) throws Exception {
        String category = body.optString("category", "all");
        CouponImages.matches(new JSONObject(), category);
        JSONArray ids = body.optJSONArray("account_ids");
        if (ids == null || ids.length() == 0) throw new ApiException("请先获取好礼券", "export_accounts_required");
        String couponId = body.optString("coupon_id", "");
        if (!couponId.isEmpty() && ids.length() != 1) throw new ApiException("单张券导出参数无效", "invalid_coupon_selection");
        List<JSONObject> rows = new ArrayList<>(); HashSet<String> seen = new HashSet<>();
        for (int i = 0; i < ids.length(); i++) {
            String id = ids.getString(i); if (!seen.add(id)) continue;
            Account account = owned(id);
            if (!account.client.authenticated()) throw new ApiException("请重新登录该账号", "login_required");
            if (account.couponsAt == 0 || SystemClock.elapsedRealtime() - account.couponsAt > 300000) throw new ApiException("券码缓存已过期，请重新获取好礼券", "qr_expired");
            for (JSONObject row : account.coupons.values()) {
                if (!couponId.isEmpty() && !row.getString("id").equals(couponId)) continue;
                if (!row.optString("qr_payload", "").isEmpty() && CouponImages.matches(row, category)) {
                    JSONObject copy = new JSONObject(row.toString()); copy.put("account_phone", account.masked());
                    if (copy.optString("title", "").isEmpty()) copy.put("title", "好礼券"); rows.add(copy);
                }
            }
        }
        if (rows.isEmpty()) throw new ApiException("此分类没有可导出的二维码", "no_matching_coupons");
        return rows;
    }

    static void validatePhone(String phone) throws Exception { if (!phone.matches("1[3-9][0-9]{9}")) throw new ApiException("请输入正确的11位手机号", "invalid_phone"); }
}
