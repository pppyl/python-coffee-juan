package cn.goodgifts.local;

import android.content.Context;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.PrivateKey;
import java.security.SecureRandom;
import java.security.Signature;
import java.security.spec.PKCS8EncodedKeySpec;
import java.util.Base64;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;
import javax.crypto.Cipher;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.SecretKeySpec;

final class Protocol {
    static final String VERSION = "10.30.1";
    private static final String ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVXYZabcdefghijklmnopqrstuvwxyz-._~";
    final JSONObject config;
    final PrivateKey signingKey;
    private final SecureRandom random = new SecureRandom();

    Protocol(Context context) throws Exception {
        try (InputStream input = context.getAssets().open("client.private.json")) {
            config = new JSONObject(new String(read(input), StandardCharsets.UTF_8));
        }
        signingKey = KeyFactory.getInstance("RSA").generatePrivate(new PKCS8EncodedKeySpec(
            Base64.getDecoder().decode(config.getString("signing_private_key_b64"))));
    }

    static byte[] read(InputStream input) throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] block = new byte[8192]; int count;
        while ((count = input.read(block)) != -1) {
            if (output.size() + count > 12 * 1024 * 1024) throw new Exception("接口返回内容过大");
            output.write(block, 0, count);
        }
        return output.toByteArray();
    }

    static JSONObject object(Object... values) throws Exception {
        JSONObject object = new JSONObject();
        for (int i = 0; i < values.length; i += 2) object.put((String) values[i], values[i + 1]);
        return object;
    }

    static String json(Object value) throws Exception {
        if (value == null || value == JSONObject.NULL) return "null";
        if (value instanceof JSONObject) {
            JSONObject item = (JSONObject) value; StringBuilder out = new StringBuilder("{");
            Iterator<String> keys = item.keys(); boolean first = true;
            while (keys.hasNext()) {
                String key = keys.next(); Object child = item.get(key);
                if (child == JSONObject.NULL) continue;
                if (!first) out.append(','); first = false;
                out.append(json(key)).append(':').append(json(child));
            }
            return out.append('}').toString();
        }
        if (value instanceof JSONArray) {
            JSONArray items = (JSONArray) value; StringBuilder out = new StringBuilder("[");
            for (int i = 0; i < items.length(); i++) { if (i != 0) out.append(','); out.append(json(items.get(i))); }
            return out.append(']').toString();
        }
        if (!(value instanceof String)) return value.toString();
        StringBuilder out = new StringBuilder("\"");
        for (char c : ((String) value).toCharArray()) {
            switch (c) {
                case '"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                case '\b': out.append("\\b"); break;
                case '\f': out.append("\\f"); break;
                default:
                    if (c < 32 || "<>&='\u2028\u2029".indexOf(c) >= 0) out.append(String.format(java.util.Locale.ROOT, "\\u%04x", (int) c));
                    else out.append(c);
            }
        }
        return out.append('"').toString();
    }

    String encrypt(String plain, String prefix, boolean login) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/CBC/PKCS5Padding");
        cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(config.getString(prefix + "_key").getBytes(StandardCharsets.UTF_8), "AES"),
                    new IvParameterSpec(config.getString(prefix + "_iv").getBytes(StandardCharsets.UTF_8)));
        byte[] bytes = cipher.doFinal(plain.getBytes(StandardCharsets.UTF_8));
        return login ? Base64.getMimeEncoder(76, new byte[]{10}).encodeToString(bytes) + "\n" : Base64.getEncoder().encodeToString(bytes);
    }

    String decrypt(String encoded) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/CBC/PKCS5Padding");
        cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(config.getString("transport_key").getBytes(StandardCharsets.UTF_8), "AES"),
                    new IvParameterSpec(config.getString("transport_iv").getBytes(StandardCharsets.UTF_8)));
        return new String(cipher.doFinal(Base64.getDecoder().decode(encoded)), StandardCharsets.UTF_8);
    }

    String signature(String path, String body, String nonce) throws Exception {
        String channel = config.getString("channel_prefix") + "103001";
        String delimiter = config.getString("signature_delimiter");
        String message = "1" + delimiter + channel + delimiter + nonce + delimiter + path + delimiter + delimiter + body;
        Signature signature = Signature.getInstance("SHA256withRSA");
        signature.initSign(signingKey); signature.update(message.getBytes(StandardCharsets.UTF_8));
        return "version=1,channel=" + channel + ",nonce=" + nonce + ",sign=" + Base64.getEncoder().encodeToString(signature.sign());
    }

    Frame frame(String path, JSONObject payload, boolean form) throws Exception {
        StringBuilder nonce = new StringBuilder();
        for (int i = 0; i < 32; i++) nonce.append(ALPHABET.charAt(random.nextInt(ALPHABET.length())));
        String plain = json(payload);
        if (form) plain = "body=" + java.net.URLEncoder.encode(encrypt(plain, "login", true), "UTF-8").replace("+", "%20");
        Frame frame = new Frame(); frame.encrypted = !form;
        frame.headers.put("x-signature", signature(path, plain, nonce.toString()));
        if (form) {
            frame.headers.put("Content-Type", "application/x-www-form-urlencoded"); frame.body = plain;
        } else {
            frame.headers.put("Content-Type", "application/json; charset=utf-8"); frame.headers.put("x-data-track", "2");
            frame.body = new JSONObject().put("encrypted", encrypt(plain, "transport", false)).toString();
        }
        return frame;
    }

    JSONObject decode(String raw, boolean encrypted) throws Exception {
        String plain = raw;
        if (encrypted) { try { plain = decrypt(raw); } catch (Exception ignored) {} }
        try { return new JSONObject(plain); } catch (Exception error) { throw new Exception("官方接口返回格式无法识别"); }
    }

    static final class Frame {
        String body; boolean encrypted;
        final Map<String, String> headers = new LinkedHashMap<>();
    }
}
