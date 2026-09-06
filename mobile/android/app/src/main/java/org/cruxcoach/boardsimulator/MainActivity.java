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
    private final Peripheral[] peripherals=new Peripheral[2];
    private final JSONObject[] profiles=new JSONObject[2];
    private final boolean[] modes=new boolean[2];
    private final java.util.ArrayDeque<JSONObject> starts=new java.util.ArrayDeque<>();
    private int instances=1, busySlot=-1, pendingSlot;
    private JSONObject pending;
    private boolean foreground;
    private String originalName;
    private final java.util.Set<String> ownedNames=new java.util.HashSet<>();

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
        for(int slot=0;slot<2;slot++)peripherals[slot]=new Peripheral(this,slot);
        setContentView(web);
        web.loadUrl("file:///android_asset/index.html");
    }
    public void status(int slot, String text, boolean running) {
        web.evaluateJavascript("BLE.status(" + JSONObject.quote(text) + "," + running + ","+slot+")", null);
        if(!running){profiles[slot]=null;if(profiles[0]==null&&profiles[1]==null)restoreName();if(busySlot==slot)ready(slot);}
    }
    public void disconnected(int slot, String peer) { web.evaluateJavascript("BLE.disconnected("+JSONObject.quote(peer)+","+slot+")", null); }
    boolean ownedByOther(int slot, android.bluetooth.BluetoothDevice device) {
        return peripherals[1-slot].owns(device);
    }
    void refreshAdvertising() {
        for(Peripheral peripheral:peripherals)peripheral.refreshAdvertising();
    }
    public void receive(int slot, int token, String peer, byte[] data, java.util.function.Consumer<JSONArray> completion) {
        JSONArray bytes = new JSONArray(); for (byte b : data) bytes.put(b & 255);
        web.evaluateJavascript("BLE.receive(" + bytes + ","+JSONObject.quote(peer)+","+token+","+slot+")", value -> {
            try { completion.accept(new JSONArray(value)); }
            catch (Exception e) { peripherals[slot].stop();status(slot,"Decoder failure: " + e.getMessage(), false); }
        });
    }
    public void readState(int slot, java.util.function.Consumer<byte[]> completion) {
        web.evaluateJavascript("BLE.readState("+slot+")", value -> {
            try { completion.accept(Peripheral.bytes(new JSONArray(value))); }
            catch (Exception e) { peripherals[slot].stop();status(slot,"State read failure: " + e.getMessage(), false); }
        });
    }
    private void start(int slot, JSONObject profile) {
        if (!foreground) { status(slot,"Return to foreground and press Start", false); return; }
        if (Build.VERSION.SDK_INT >= 31 &&
                (checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED ||
                 checkSelfPermission(Manifest.permission.BLUETOOTH_ADVERTISE) != PackageManager.PERMISSION_GRANTED)) {
            pending = profile;pendingSlot=slot;
            requestPermissions(new String[]{Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.BLUETOOTH_ADVERTISE}, 1);
            return;
        }
        try {
            JSONObject request=new JSONObject().put("slot",slot).put("profile",profile);
            starts.removeIf(r->r.optInt("slot")==slot);starts.add(request);drain();
        }catch(Exception e){status(slot,e.getMessage(),false);}
    }
    public void ready(int slot) { if(busySlot==slot){busySlot=-1;drain();} }
    @android.annotation.SuppressLint("MissingPermission")
    private void drain() {
        if(busySlot!=-1||starts.isEmpty())return;
        JSONObject request=starts.remove();int slot=request.optInt("slot");
        try {
            JSONObject profile=request.getJSONObject("profile");
            peripherals[slot].stop();profiles[slot]=null;
            JSONObject other=profiles[1-slot];
            if(other!=null && !disjoint(profile,other)) {status(slot,"Overlapping GATT profiles: automatic advertiser-to-connection routing is unavailable. Use the routing experiment; full parity is pending.",false);drain();return;}
            android.bluetooth.BluetoothAdapter adapter=((android.bluetooth.BluetoothManager)getSystemService(BLUETOOTH_SERVICE)).getAdapter();
            if(originalName==null&&adapter!=null)originalName=adapter.getName();
            ownedNames.add(profile.getString("name"));
            profiles[slot]=profile;busySlot=slot;
            peripherals[slot].start(profile,modes[slot]);
        }catch(Exception e){status(slot,e.getMessage(),false);}
    }
    private static boolean disjoint(JSONObject a,JSONObject b) throws Exception {
        java.util.Set<String> uuids=new java.util.HashSet<>();
        JSONArray first=a.getJSONArray("services"),second=b.getJSONArray("services");
        for(int i=0;i<first.length();i++)uuids.add(first.getJSONObject(i).getString("uuid"));
        for(int i=0;i<second.length();i++)if(uuids.contains(second.getJSONObject(i).getString("uuid")))return false;
        return true;
    }
    private void stopAll() {
        starts.clear();busySlot=-1;pending=null;
        for(int slot=0;slot<2;slot++){peripherals[slot].stop();profiles[slot]=null;}
        restoreName();
    }
    @android.annotation.SuppressLint("MissingPermission")
    private void restoreName() {
        try {
            android.bluetooth.BluetoothAdapter adapter=((android.bluetooth.BluetoothManager)getSystemService(BLUETOOTH_SERVICE)).getAdapter();
            if(adapter!=null&&adapter.isEnabled()&&originalName!=null&&ownedNames.contains(adapter.getName()))adapter.setName(originalName);
        }catch(SecurityException ignored){}
        originalName=null;ownedNames.clear();
    }
    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(request, permissions, results);
        if (request != 1) return;
        JSONObject profile = pending; pending = null;
        if (results.length == 2 && results[0] == PackageManager.PERMISSION_GRANTED && results[1] == PackageManager.PERMISSION_GRANTED && profile != null) start(pendingSlot,profile);
        else status(pendingSlot,"Nearby devices permission denied. Grant it in Settings, then Start.", false);
    }
    private final class Bridge {
        @JavascriptInterface public void postMessage(String raw) {
            runOnUiThread(() -> {
                int slot=0;
                try {
                    JSONObject message = new JSONObject(raw);
                    slot=message.optInt("slot");if(slot<0||slot>=instances)throw new IllegalArgumentException("Invalid board slot");
                    switch (message.getString("command")) {
                        case "start": modes[slot]=message.optBoolean("multi"); start(slot,message.getJSONObject("profile")); break;
                        case "connections": modes[slot]=message.optBoolean("multi"); peripherals[slot].setMulti(modes[slot]); break;
                        case "instances": stopAll(); instances=message.getInt("count");if(instances<1||instances>2)throw new IllegalArgumentException("Invalid count");for(Peripheral p:peripherals)p.setMultiplexed(instances==2);break;
                        case "probe": startActivity(new android.content.Intent(MainActivity.this,RoutingProbeActivity.class)); break;
                        case "stop": {final int target=slot;starts.removeIf(r->r.optInt("slot")==target);peripherals[slot].stop();status(slot,"Stopped",false);break;}
                        case "copy": ((ClipboardManager)getSystemService(CLIPBOARD_SERVICE)).setPrimaryClip(ClipData.newPlainText("BoardSimulator", message.getString("text"))); break;
                        case "ready": web.evaluateJavascript("BLE.canProbe()",null); status(slot,"Android " + Build.VERSION.RELEASE + " · Start to check advertiser support", false); break;
                        default: throw new IllegalArgumentException("Unknown bridge command");
                    }
                } catch (Exception e) { slot=Math.max(0,Math.min(slot,1));peripherals[slot].stop(); status(slot,"Error: " + e.getMessage(), false); }
            });
        }
    }
    @Override protected void onResume() { super.onResume(); foreground = true; }
    @Override protected void onStop() {
        foreground = false; pending = null;
        stopAll();for(int slot=0;slot<instances;slot++)status(slot,"Stopped in background. Press Start to resume.",false);super.onStop();
    }
    @Override protected void onDestroy() { stopAll(); web.removeJavascriptInterface("Native"); web.destroy(); super.onDestroy(); }
}
