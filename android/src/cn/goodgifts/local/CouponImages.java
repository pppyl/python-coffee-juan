package cn.goodgifts.local;

import android.content.ContentValues;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.net.Uri;
import android.provider.MediaStore;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import io.nayuki.qrcodegen.QrCode;

final class CouponImages {
    private static final Pattern AMOUNT = Pattern.compile("(?<![\\d.])(8|10|15|20|30)(?:\\.0+)?\\s*元(?![\\d])");

    static boolean matches(JSONObject row, String category) throws Exception {
        if ("all".equals(category)) return true;
        if (!category.matches("8|10|15|20|30")) throw new ApiException("不支持的面额分类", "invalid_category");
        Matcher matcher = AMOUNT.matcher(row.optString("title", ""));
        while (matcher.find()) if (matcher.group(1).equals(category)) return true;
        return false;
    }

    static Bitmap qr(String payload, int desired) {
        QrCode code = QrCode.encodeText(payload, QrCode.Ecc.MEDIUM);
        int scale = Math.max(4, desired / (code.size + 8)), size = (code.size + 8) * scale;
        Bitmap image = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(image); canvas.drawColor(Color.WHITE); Paint ink = new Paint(); ink.setColor(Color.BLACK);
        for (int y = 0; y < code.size; y++) for (int x = 0; x < code.size; x++) {
            if (code.getModule(x, y)) canvas.drawRect((x + 4) * scale, (y + 4) * scale, (x + 5) * scale, (y + 5) * scale, ink);
        }
        return image;
    }

    static String dataUrl(String payload) {
        Bitmap image = qr(payload, 420); ByteArrayOutputStream output = new ByteArrayOutputStream();
        image.compress(Bitmap.CompressFormat.PNG, 100, output); image.recycle();
        return "data:image/png;base64," + android.util.Base64.encodeToString(output.toByteArray(), android.util.Base64.NO_WRAP);
    }

    static Bitmap named(JSONObject row) throws Exception {
        Paint title = new Paint(Paint.ANTI_ALIAS_FLAG); title.setColor(Color.rgb(24,61,49)); title.setTextSize(42);
        title.setTypeface(Typeface.create(Typeface.DEFAULT, Typeface.BOLD));
        String name = row.optString("title", "好礼券"); if (name.isEmpty()) name = "好礼券";
        if (name.length() > 160) name = name.substring(0,160) + "…";
        List<String> lines = new ArrayList<>(); StringBuilder line = new StringBuilder();
        for (int i = 0; i < name.length(); i++) {
            String next = line.toString() + name.charAt(i);
            if (line.length() > 0 && title.measureText(next) > 832) { lines.add(line.toString()); line.setLength(0); }
            line.append(name.charAt(i));
        }
        lines.add(line.toString());
        Bitmap qr = qr(row.getString("qr_payload"), 760);
        int top = 64 + lines.size() * 60 + 75, height = top + qr.getHeight() + 110;
        Bitmap image = Bitmap.createBitmap(960, height, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(image); canvas.drawColor(Color.WHITE);
        title.setTextAlign(Paint.Align.CENTER);
        for (int i = 0; i < lines.size(); i++) canvas.drawText(lines.get(i), 480, 98 + i * 60, title);
        Paint small = new Paint(Paint.ANTI_ALIAS_FLAG); small.setColor(Color.rgb(96,115,102)); small.setTextSize(24); small.setTextAlign(Paint.Align.CENTER);
        canvas.drawText("账号 " + row.getString("account_phone"), 480, top - 36, small);
        canvas.drawBitmap(qr, (960 - qr.getWidth()) / 2f, top, null); qr.recycle();
        String expiry = row.optString("expires_at", "");
        if (!expiry.isEmpty()) canvas.drawText("有效期至 " + expiry, 480, height - 50, small);
        return image;
    }

    static Uri save(Context context, JSONObject row) throws Exception {
        String name = row.optString("title", "好礼券").replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]", "_");
        if (name.length() > 60) name = name.substring(0,60);
        name += "_" + row.getString("account_phone") + "_" + System.currentTimeMillis() + "_" + java.util.UUID.randomUUID().toString().substring(0,6) + ".png";
        ContentValues values = new ContentValues(); values.put(MediaStore.Images.Media.DISPLAY_NAME, name);
        values.put(MediaStore.Images.Media.MIME_TYPE, "image/png"); values.put(MediaStore.Images.Media.RELATIVE_PATH, "Pictures/好礼随行");
        values.put(MediaStore.Images.Media.IS_PENDING, 1);
        Uri uri = context.getContentResolver().insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values);
        if (uri == null) throw new Exception("无法创建相册图片");
        Bitmap image = null;
        try {
            image = named(row);
            try (OutputStream output = context.getContentResolver().openOutputStream(uri)) {
                if (output == null || !image.compress(Bitmap.CompressFormat.PNG, 100, output)) throw new Exception("无法写入相册图片");
            }
            values.clear(); values.put(MediaStore.Images.Media.IS_PENDING, 0);
            context.getContentResolver().update(uri, values, null, null);
            return uri;
        } catch (Exception error) { context.getContentResolver().delete(uri, null, null); throw error; }
        finally { if (image != null) image.recycle(); }
    }
}
