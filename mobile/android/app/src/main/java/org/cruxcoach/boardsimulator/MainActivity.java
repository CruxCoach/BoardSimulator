package org.cruxcoach.boardsimulator;

import android.Manifest;
import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.view.WindowManager;
import org.json.JSONArray;
import org.json.JSONObject;

/** Trusted offline WebView UI with native BluetoothGattServer transport. */
public final class MainActivity extends Activity {
    private WebView web;
    private Peripheral peripheral;
    private JSONObject pending;
    private boolean foreground;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        web = new WebView(this);
        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setAllowFileAccess(true);
        web.getSettings().setAllowContentAccess(false);
        web.getSettings().setAllowFileAccessFromFileURLs(false);
        web.getSettings().setAllowUniversalAccessFromFileURLs(false);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) { return true; }
        });
        web.addJavascriptInterface(new Bridge(), "Native");
        peripheral = new Peripheral(this);
        setContentView(web);
        web.loadUrl("file:///android_asset/index.html");
    }
    public void status(String text, boolean running) {
        web.evaluateJavascript("BLE.status(" + JSONObject.quote(text) + "," + running + ")", null);
    }
    public void disconnected() { web.evaluateJavascript("BLE.disconnected()", null); }
    public void receive(byte[] data, java.util.function.Consumer<JSONArray> completion) {
        JSONArray bytes = new JSONArray(); for (byte b : data) bytes.put(b & 255);
        web.evaluateJavascript("BLE.receive(" + bytes + ")", value -> {
            try { completion.accept(new JSONArray(value)); }
            catch (Exception e) { status("Decoder failure: " + e.getMessage(), false); peripheral.stop(); }
        });
    }
    public void readState(java.util.function.Consumer<byte[]> completion) {
        web.evaluateJavascript("BLE.readState()", value -> {
            try { completion.accept(Peripheral.bytes(new JSONArray(value))); }
            catch (Exception e) { status("State read failure: " + e.getMessage(), false); peripheral.stop(); }
        });
    }
    private void start(JSONObject profile) {
        if (!foreground) { status("Return to foreground and press Start", false); return; }
        if (Build.VERSION.SDK_INT >= 31 &&
                (checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED ||
                 checkSelfPermission(Manifest.permission.BLUETOOTH_ADVERTISE) != PackageManager.PERMISSION_GRANTED)) {
            pending = profile;
            requestPermissions(new String[]{Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.BLUETOOTH_ADVERTISE}, 1);
            return;
        }
        peripheral.start(profile);
    }
    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(request, permissions, results);
        if (request != 1) return;
        JSONObject profile = pending; pending = null;
        if (results.length == 2 && results[0] == PackageManager.PERMISSION_GRANTED && results[1] == PackageManager.PERMISSION_GRANTED && profile != null) start(profile);
        else status("Nearby devices permission denied. Grant it in Settings, then Start.", false);
    }
    private final class Bridge {
        @JavascriptInterface public void postMessage(String raw) {
            runOnUiThread(() -> {
                try {
                    JSONObject message = new JSONObject(raw);
                    switch (message.getString("command")) {
                        case "start": start(message.getJSONObject("profile")); break;
                        case "stop": peripheral.stop(); status("Stopped", false); break;
                        case "copy": ((ClipboardManager)getSystemService(CLIPBOARD_SERVICE)).setPrimaryClip(ClipData.newPlainText("BoardSimulator", message.getString("text"))); break;
                        case "ready": status("Android " + Build.VERSION.RELEASE + " · One BLE identity · Start to check advertiser support", false); break;
                        default: throw new IllegalArgumentException("Unknown bridge command");
                    }
                } catch (Exception e) { peripheral.stop(); status("Error: " + e.getMessage(), false); }
            });
        }
    }
    @Override protected void onResume() { super.onResume(); foreground = true; }
    @Override protected void onStop() {
        foreground = false; pending = null;
        peripheral.stop(); status("Stopped in background. Press Start to resume.", false); super.onStop();
    }
    @Override protected void onDestroy() { peripheral.stop(); web.removeJavascriptInterface("Native"); web.destroy(); super.onDestroy(); }
}
