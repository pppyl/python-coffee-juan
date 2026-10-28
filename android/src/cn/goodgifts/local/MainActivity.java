package cn.goodgifts.local;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.graphics.Color;
import android.os.Bundle;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import org.json.JSONObject;
import java.io.ByteArrayInputStream;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    WebView web;
    NativeBackend backend;
    String startupError;
    final ExecutorService worker = Executors.newSingleThreadExecutor();
    static final String LOCAL_HOST = "appassets.androidplatform.net";

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(Color.rgb(248,247,242)); getWindow().setNavigationBarColor(Color.rgb(248,247,242));
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        FrameLayout root = new FrameLayout(this); root.setFitsSystemWindows(true); root.setBackgroundColor(Color.rgb(248,247,242));
        web = new WebView(this); root.addView(web, new FrameLayout.LayoutParams(-1,-1)); setContentView(root);
        WebSettings settings = web.getSettings(); settings.setJavaScriptEnabled(true); settings.setDomStorageEnabled(false);
        settings.setAllowFileAccess(false); settings.setAllowContentAccess(false); settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setCacheMode(WebSettings.LOAD_NO_CACHE); settings.setSupportMultipleWindows(false);
        WebView.setWebContentsDebuggingEnabled(BuildConfig.TESTING);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) { return true; }
            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                String path = request.getUrl().getPath();
                if (!"https".equals(request.getUrl().getScheme()) || !LOCAL_HOST.equals(request.getUrl().getHost()) || path == null || path.contains("..")) return blocked();
                String name = path.equals("/") ? "index.html" : path.substring(1);
                if (!(name.equals("index.html") || name.equals("app.js") || name.equals("app.css"))) return blocked();
                try {
                    String mime = name.endsWith(".js") ? "application/javascript" : name.endsWith(".css") ? "text/css" : "text/html";
                    Map<String,String> headers = new HashMap<>();
                    headers.put("Cache-Control", "no-store"); headers.put("X-Content-Type-Options", "nosniff");
                    headers.put("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'");
                    return new WebResourceResponse(mime, "UTF-8", 200, "OK", headers, getAssets().open(name));
                } catch (Exception error) { return blocked(); }
            }
        });
        web.addJavascriptInterface(new Bridge(), "NativeApp");
        worker.execute(() -> {
            try { backend = new NativeBackend(getApplicationContext()); }
            catch (Exception error) { startupError = "无法读取本机协议或加密账号存储，请检查安装包。"; }
            runOnUiThread(() -> { if (!isFinishing()) web.loadUrl("https://" + LOCAL_HOST + "/index.html"); });
        });
    }

    private WebResourceResponse blocked() {
        return new WebResourceResponse("text/plain", "UTF-8", 403, "Forbidden", new HashMap<>(), new ByteArrayInputStream(new byte[0]));
    }

    final class Bridge {
        @JavascriptInterface public void request(String id, String path, String body) {
            if (id == null || id.length() > 80 || path == null || path.length() > 1000 || body == null || body.length() > 8192) return;
            worker.execute(() -> {
                JSONObject response;
                try {
                    if (backend == null) throw new ApiException(startupError == null ? "本机服务尚未就绪" : startupError, "not_ready");
                    response = backend.handle(path, new JSONObject(body));
                } catch (Exception error) {
                    response = new JSONObject();
                    try { response.put("ok", false).put("error", error instanceof ApiException ? ((ApiException) error).code : "operation_failed")
                        .put("message", error instanceof ApiException ? error.getMessage() : "操作失败，请检查网络或重新获取好礼券。"); } catch (Exception ignored) {}
                }
                final String result = response.toString();
                runOnUiThread(() -> { if (!isFinishing() && web != null) web.evaluateJavascript("window.receiveNativeResult(" + JSONObject.quote(id) + "," + JSONObject.quote(result) + ");", null); });
            });
        }

        @JavascriptInterface public boolean copy(String text) {
            if (text == null || text.length() > 500000) return false;
            try {
                ClipboardManager manager = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
                manager.setPrimaryClip(ClipData.newPlainText("好礼券名称与二维码信息", text));
                return true;
            } catch (Exception error) { return false; }
        }
    }

    @Override protected void onDestroy() {
        if (web != null) { web.removeJavascriptInterface("NativeApp"); web.destroy(); web = null; }
        worker.shutdown(); super.onDestroy();
    }
}
