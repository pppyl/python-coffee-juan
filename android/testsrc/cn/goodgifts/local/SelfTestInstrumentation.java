package cn.goodgifts.local;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.ContentUris;
import android.content.Context;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Bundle;
import android.provider.MediaStore;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class SelfTestInstrumentation extends Instrumentation {
    int passed;
    void check(boolean condition, String label) throws Exception { if (!condition) throw new Exception("Assertion failed: " + label); passed++; }
    @Override public void onCreate(Bundle arguments) { super.onCreate(arguments); start(); }
    @Override public void onStart() {
        JSONObject report = new JSONObject(); Bundle results = new Bundle();
        try {
            runTests(); report.put("ok", true).put("passed", passed); results.putString("result", report.toString());
            write(report); finish(Activity.RESULT_OK, results);
        } catch (Exception error) {
            try { report.put("ok", false).put("passed", passed).put("message", error.toString()); write(report); } catch (Exception ignored) {}
            results.putString("result", report.toString()); finish(Activity.RESULT_CANCELED, results);
        }
    }

    void write(JSONObject report) throws Exception {
        try (FileOutputStream output = getTargetContext().openFileOutput("selftest.json", Context.MODE_PRIVATE)) { output.write(report.toString().getBytes(StandardCharsets.UTF_8)); }
    }

    void runTests() throws Exception {
        Context context = getTargetContext(); Protocol p = new Protocol(context);
        String plain = "{\"cellPhone\":\"test-phone\",\"pin\":\"0000\"}";
        String nonce = "0123456789ABCDEFGHIJKLMNOPQRSTUV";
        check(p.encrypt(plain, "transport", false).equals("Xe7B5LOYV/MDGzU79NnwRH2n7/Pzz8IG28eWPu4NR+lz7Q98gh1EEPJ8FjCf0Gya"), "AES independent Java oracle");
        check(p.decrypt(p.encrypt(plain, "transport", false)).equals(plain), "transport roundtrip");
        String login = Base64.getEncoder().encodeToString(p.encrypt(plain, "login", true).getBytes(StandardCharsets.UTF_8));
        check(login.equals("YldmemFSbnJJTGZUbnZUZFB6UHdSaFhaTVczTjlXSnBsOGJvLzh5QktVd2VKaDdLR3hjUENjU0ZlVjRmVnF6ego="), "Android login Base64 exact LF");
        String signature = p.signature(StarbucksClient.SMS, plain, nonce).split(",sign=", 2)[1];
        check(StarbucksClient.hash(signature).equals("4f22a4d3c6555953e5c991c39de9e5b4d3d214bd4ca953aa0c19204cbb359afc"), "RSA independent oracle");
        check(Protocol.json(Protocol.object("x", JSONObject.NULL, "y", new JSONArray().put(JSONObject.NULL).put("<&='\u2028")))
            .equals("{\"y\":[null,\"\\u003c\\u0026\\u003d\\u0027\\u2028\"]}"), "Gson null and HTML escaping");
        Protocol.Frame loginFrame = p.frame(StarbucksClient.LOGIN, new JSONObject(plain), true);
        check(!loginFrame.encrypted && loginFrame.body.contains("%0A"), "legacy form bypass");
        FlowClient flow = new FlowClient(p); flow.sendSms("13900000000"); flow.login("13900000000", "1234");
        check(flow.authenticated() && flow.calls.equals(Arrays.asList(StarbucksClient.SMS, StarbucksClient.LOGIN, StarbucksClient.TOKEN, StarbucksClient.ACCOUNT)), "four step real contract with fake transport");
        check(CouponImages.matches(Protocol.object("title", "8元电子代金券"), "8") && !CouponImages.matches(Protocol.object("title", "80元券"), "8"), "exact denomination");
        check(!CouponImages.matches(Protocol.object("title", "8.5元券"), "8") && CouponImages.matches(Protocol.object("title", "无面额券"), "all"), "decimal and all categories");
        AccountVault vault = new AccountVault(context); JSONArray original = vault.load();
        Set<Long> before = testImageIds(context);
        try {
            JSONArray sample = new JSONArray().put(Protocol.object("token", "TEST-ONLY-PRIVATE")); vault.save(sample);
            byte[] encrypted = java.nio.file.Files.readAllBytes(new File(context.getFilesDir(), "accounts.enc").toPath());
            check(!new String(encrypted, StandardCharsets.ISO_8859_1).contains("TEST-ONLY-PRIVATE") && vault.load().getJSONObject(0).getString("token").equals("TEST-ONLY-PRIVATE"), "Android Keystore authenticated encryption");
            vault.save(new JSONArray());
            NativeBackend backend = new NativeBackend(context);
            FakeClient one = new FakeClient(p, false), two = new FakeClient(p, false);
            backend.accounts.put("test-one", new NativeBackend.Account("test-one", "13900000000", one));
            backend.accounts.put("test-two", new NativeBackend.Account("test-two", "13800000000", two));
            JSONObject all = backend.handle("/api/accounts/coupons", new JSONObject());
            check(all.getInt("succeeded") == 2 && all.getInt("total_coupons") == 4, "multi account batch");
            JSONArray ids = new JSONArray().put("test-one").put("test-two");
            JSONObject selection = Protocol.object("category", "8", "account_ids", ids);
            JSONObject copied = backend.handle("/api/exports/copy", selection);
            check(copied.getInt("count") == 2 && copied.getString("text").contains("8元SELFTEST") && !copied.getString("text").contains("15元SELFTEST"), "copy category across accounts");
            JSONObject saved = backend.handle("/api/exports/download", selection);
            Set<Long> after = testImageIds(context); after.removeAll(before);
            check(saved.getInt("count") == 2 && after.size() == 2, "batch saves two independent PNG images to MediaStore");
            JSONObject row = backend.accounts.get("test-one").coupons.get("shared-eight");
            row = new JSONObject(row.toString()); row.put("account_phone", "139****0000");
            Bitmap image = CouponImages.named(row);
            try (FileOutputStream output = context.openFileOutput("selftest-image.png", Context.MODE_PRIVATE)) { image.compress(Bitmap.CompressFormat.PNG, 100, output); } image.recycle();
            two.fail = true;
            JSONObject partial = backend.handle("/api/accounts/coupons", new JSONObject());
            check(partial.getInt("succeeded") == 1 && partial.getInt("failed") == 1, "failed account isolation");
            JSONObject removed = backend.handle("/api/accounts/test-one/logout", new JSONObject());
            check(removed.getJSONArray("accounts").length() == 1, "single logout isolation");
        } finally {
            Set<Long> cleanup = testImageIds(context); cleanup.removeAll(before);
            for (Long id : cleanup) context.getContentResolver().delete(ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id), null, null);
            vault.save(original);
        }
    }

    Set<Long> testImageIds(Context context) throws Exception {
        Set<Long> ids = new HashSet<>();
        try (Cursor cursor = context.getContentResolver().query(MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            new String[]{MediaStore.Images.Media._ID}, MediaStore.Images.Media.DISPLAY_NAME + " LIKE ?", new String[]{"%SELFTEST%"}, null)) {
            if (cursor != null) while (cursor.moveToNext()) ids.add(cursor.getLong(0));
        }
        return ids;
    }

    static final class FlowClient extends StarbucksClient {
        final List<String> calls = new ArrayList<>();
        FlowClient(Protocol p) { super(p, "0123456789abcdef0123456789abcdef"); }
        @Override JSONObject request(String path, JSONObject body, boolean form, Map<String,String> extra) throws Exception {
            calls.add(path);
            if (path.equals(SMS)) return Protocol.object("code", 200, "data", new JSONObject());
            if (path.equals(LOGIN)) { if (!form || !extra.get("loginType").equals("PIN_CODE")) throw new Exception("login contract"); return Protocol.object("code", 200, "data", Protocol.object("token", "TEST-ONLY-CODE")); }
            if (path.equals(TOKEN)) { if (!extra.get("x-msr-version").equals("2")) throw new Exception("gateway header"); return Protocol.object("code", 200, "data", Protocol.object("access_token", "TEST-ONLY-ACCESS")); }
            return Protocol.object("code", "200", "data", Protocol.object("extra", new JSONObject(), "nickName", "测试账号"));
        }
    }

    static final class FakeClient extends StarbucksClient {
        boolean fail;
        FakeClient(Protocol p, boolean fail) { super(p, "0123456789abcdef0123456789abcdef"); token = "TEST-ONLY-NOT-A-TOKEN"; confirmed = true; this.fail = fail; }
        @Override synchronized List<JSONObject> coupons() throws Exception {
            if (fail) { logout(); throw new ApiException("测试失效", "login_required"); }
            List<JSONObject> rows = new ArrayList<>();
            rows.add(Protocol.object("id", "shared-eight", "title", "8元SELFTEST电子代金券", "status", "AVAILABLE", "qr_payload", "TEST-ONLY-NOT-REDEEMABLE-8"));
            rows.add(Protocol.object("id", "shared-fifteen", "title", "15元SELFTEST电子代金券", "status", "AVAILABLE", "qr_payload", "TEST-ONLY-NOT-REDEEMABLE-15"));
            return rows;
        }
    }
}
