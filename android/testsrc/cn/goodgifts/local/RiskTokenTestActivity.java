package cn.goodgifts.local;

import android.app.Activity;
import android.os.Bundle;
import android.widget.FrameLayout;
import org.json.JSONObject;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

public final class RiskTokenTestActivity extends Activity {
    ShumeiWebTokenProvider provider;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        FrameLayout root = new FrameLayout(this);
        setContentView(root);
        provider = new ShumeiWebTokenProvider(this, root);
        new Thread(() -> {
            JSONObject report = new JSONObject();
            try {
                String token = provider.get(20000);
                report.put("ready", ShumeiWebTokenProvider.validToken(token));
                report.put("length", token == null ? 0 : token.length());
                report.put("prefix", token == null || token.isEmpty() ? "" : token.substring(0, 1));
            } catch (Exception error) {
                try { report.put("ready", false).put("error", error.getClass().getSimpleName()); } catch (Exception ignored) {}
            }
            try (FileOutputStream output = openFileOutput("risk-selftest.json", MODE_PRIVATE)) {
                output.write(report.toString().getBytes(StandardCharsets.UTF_8));
            } catch (Exception ignored) {}
            runOnUiThread(this::finish);
        }, "risk-token-selftest").start();
    }

    @Override protected void onDestroy() {
        if (provider != null) { provider.close(); provider = null; }
        super.onDestroy();
    }
}
