package cn.goodgifts.local;

import android.app.Activity;
import android.graphics.Color;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;

final class ShumeiWebTokenProvider implements RiskTokenProvider {
    static final String BASE_URL = "https://artwork.starbucks.com.cn/goodgifts-fingerprint-runtime";
    static final String BASE_HOST = "artwork.starbucks.com.cn";
    static final String BASE_PATH = "/goodgifts-fingerprint-runtime";
    static final String DEVICE_PROFILE_HOST = "bff.starbucks.com.cn";
    static final String DEVICE_PROFILE_PATH = "/deviceprofile/v4";
    private final Activity activity;
    private final FrameLayout container;
    private final Object lock = new Object();
    private WebView web;
    private boolean started, closed;
    private String token, error;

    ShumeiWebTokenProvider(Activity activity, FrameLayout container) {
        this.activity = activity;
        this.container = container;
    }

    static boolean validToken(String value) {
        return value != null && value.length() == 89 && value.charAt(0) == 'B'
            && value.matches("[A-Za-z0-9+/=]+");
    }

    @Override public String get(long timeoutMs) throws Exception {
        start();
        long deadline = android.os.SystemClock.elapsedRealtime() + Math.max(0, timeoutMs);
        synchronized (lock) {
            while (!closed && !validToken(token)) {
                long remaining = deadline - android.os.SystemClock.elapsedRealtime();
                if (remaining <= 0) break;
                lock.wait(remaining);
            }
            return validToken(token) ? token : null;
        }
    }

    private void start() {
        synchronized (lock) {
            if (started || closed) return;
            started = true;
        }
        activity.runOnUiThread(this::startOnUiThread);
    }

    private void startOnUiThread() {
        synchronized (lock) { if (closed) return; }
        try {
            web = new WebView(activity);
            web.setBackgroundColor(Color.TRANSPARENT);
            web.setAlpha(0.01f);
            web.setClickable(false);
            web.setFocusable(false);
            web.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
            FrameLayout.LayoutParams layout = new FrameLayout.LayoutParams(1, 1);
            container.addView(web, 0, layout);
            WebSettings settings = web.getSettings();
            settings.setJavaScriptEnabled(true);
            settings.setDomStorageEnabled(true);
            settings.setAllowFileAccess(false);
            settings.setAllowContentAccess(false);
            settings.setAllowFileAccessFromFileURLs(false);
            settings.setAllowUniversalAccessFromFileURLs(false);
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
            settings.setCacheMode(WebSettings.LOAD_DEFAULT);
            settings.setSupportMultipleWindows(false);
            web.addJavascriptInterface(new Callback(), "GoodGiftsFingerprint");
            web.setWebViewClient(new WebViewClient() {
                @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) { return true; }
                @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                    String host = request.getUrl().getHost(), path = request.getUrl().getPath();
                    if ("https".equals(request.getUrl().getScheme()) && BASE_HOST.equals(host)
                            && BASE_PATH.equals(path) && "GET".equals(request.getMethod())) return htmlResponse();
                    if ("https".equals(request.getUrl().getScheme()) && DEVICE_PROFILE_HOST.equals(host)
                            && DEVICE_PROFILE_PATH.equals(path)
                            && ("POST".equals(request.getMethod()) || "OPTIONS".equals(request.getMethod()))) return null;
                    return blocked();
                }
                @Override public void onPageFinished(WebView view, String url) {
                    if (!BASE_URL.equals(url)) return;
                    try (InputStream input = activity.getAssets().open("fp.min.js")) {
                        String sdk = new String(Protocol.read(input), StandardCharsets.UTF_8);
                        view.evaluateJavascript(sdk + "\n;" + pollScript(), null);
                    } catch (Exception failure) { fail("asset_load_failed"); }
                }
            });
            web.loadUrl(BASE_URL);
        } catch (Exception failure) { fail("webview_start_failed"); }
    }

    private static String html() {
        return "<!doctype html><meta charset=utf-8><script>"
            + "window._smReadyFuncs=[];window.__fpErrors=[];window._smConf={"
            + "organization:'BbYdjRZ8pcNU9gt6YcBV',appId:'default',"
            + "publicKey:'MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCSI8/YFlLqQAaVwwqACyCYy93DVM3/9q0urfDQ8XM9A0pZiQt1NOVM2XbEoZalISdDXHw6PjmHWLzMzZXALWIIERmCp+MXUkvU+iCzWBqiYG40phAO3iBAWc/huNYCmoBUXnGWDja5r6aG1XuLgg5OebZNSzmqcc+H6vOC2doc+QIDAQAB',"
            + "apiHost:'bff.starbucks.com.cn',staticHost:'https://bff.starbucks.com.cn',"
            + "useShortBox:true,protocol:'https',apiPath:'/deviceprofile/v4',"
            + "onError:function(c){GoodGiftsFingerprint.onError(String(c));}};"
            + "</script>";
    }

    private static String pollScript() {
        return "(function(){var end=Date.now()+15000;(function poll(){try{var v=window.SMSdk&&SMSdk.getDeviceId?String(SMSdk.getDeviceId()):'';"
            + "if(v.length===89&&v.charAt(0)==='B'){GoodGiftsFingerprint.onToken(v);return;}"
            + "if(Date.now()>=end){GoodGiftsFingerprint.onError('timeout');return;}}catch(e){GoodGiftsFingerprint.onError(e&&e.name?e.name:'Error');return;}"
            + "setTimeout(poll,100);})();})();";
    }

    private static WebResourceResponse blocked() {
        return new WebResourceResponse("text/plain", "UTF-8", 403, "Forbidden", new HashMap<>(), new ByteArrayInputStream(new byte[0]));
    }

    private static WebResourceResponse htmlResponse() {
        HashMap<String, String> headers = new HashMap<>();
        headers.put("Cache-Control", "no-store");
        headers.put("X-Content-Type-Options", "nosniff");
        return new WebResourceResponse("text/html", "UTF-8", 200, "OK", headers,
            new ByteArrayInputStream(html().getBytes(StandardCharsets.UTF_8)));
    }

    private void fail(String value) {
        synchronized (lock) { error = value; lock.notifyAll(); }
    }

    final class Callback {
        @JavascriptInterface public void onToken(String value) {
            if (!validToken(value)) { fail("invalid_token"); return; }
            synchronized (lock) { token = value; error = null; lock.notifyAll(); }
        }
        @JavascriptInterface public void onError(String value) {
            if (value == null || value.length() > 80) value = "runtime_error";
            fail(value);
        }
    }

    @Override public void close() {
        synchronized (lock) { closed = true; token = null; lock.notifyAll(); }
        activity.runOnUiThread(() -> {
            if (web != null) {
                web.removeJavascriptInterface("GoodGiftsFingerprint");
                container.removeView(web);
                web.destroy();
                web = null;
            }
        });
    }
}
