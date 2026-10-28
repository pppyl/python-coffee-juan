package cn.goodgifts.local;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.AtomicFile;
import org.json.JSONArray;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class AccountVault {
    private static final String ALIAS = "goodgifts-accounts-v1";
    private final AtomicFile file;
    AccountVault(Context context) { file = new AtomicFile(new File(context.getFilesDir(), "accounts.enc")); }

    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore"); store.load(null);
        if (store.containsAlias(ALIAS)) return ((KeyStore.SecretKeyEntry) store.getEntry(ALIAS, null)).getSecretKey();
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
        return generator.generateKey();
    }

    synchronized JSONArray load() throws Exception {
        if (!file.getBaseFile().exists()) return new JSONArray();
        byte[] input = file.readFully();
        if (input.length < 29 || input[0] != 1) throw new Exception("账号存储格式无效");
        byte[] iv = java.util.Arrays.copyOfRange(input, 1, 13);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, iv));
        return new JSONArray(new String(cipher.doFinal(input, 13, input.length - 13), StandardCharsets.UTF_8));
    }

    synchronized void save(JSONArray accounts) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.ENCRYPT_MODE, key());
        byte[] data = cipher.doFinal(accounts.toString().getBytes(StandardCharsets.UTF_8));
        ByteBuffer buffer = ByteBuffer.allocate(1 + 12 + data.length);
        buffer.put((byte) 1).put(cipher.getIV()).put(data);
        FileOutputStream output = null;
        try { output = file.startWrite(); output.write(buffer.array()); file.finishWrite(output); }
        catch (Exception error) { file.failWrite(output); throw error; }
    }
}
