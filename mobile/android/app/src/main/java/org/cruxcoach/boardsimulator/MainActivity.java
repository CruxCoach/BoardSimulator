package org.cruxcoach.boardsimulator;

import android.Manifest;
import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.webkit.JavascriptInterface;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.widget.FrameLayout;
import org.json.JSONArray;
import org.json.JSONObject;

/** Trusted offline WebView UI with native BluetoothGattServer transport. */
public final class MainActivity extends Activity {
    private FrameLayout root;
    /** The page; null between a dead renderer and its replacement. */
    private WebView web;
    /** Counts pages; a dead page's bridge goes quiet. */
    private int page;
    /** Shown in the first panel once a rebuilt page is ready. */
    private String rebuiltNotice;
    private final Peripheral[] peripherals=new Peripheral[2];
    private final JSONObject[] profiles=new JSONObject[2];
    private final boolean[] modes=new boolean[2];
    private final java.util.ArrayDeque<JSONObject> starts=new java.util.ArrayDeque<>();
    private int instances=1, busySlot=-1, pendingSlot;
    private JSONObject pending;
    private boolean foreground;
    /** Lends the phone's Bluetooth name to the boards and gives it back. */
    private AdapterNameGuard names;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        for(int slot=0;slot<2;slot++)peripherals[slot]=new Peripheral(this,slot);
        names=AdapterNames.get(this);
        names.setListener(this::phoneName);
        names.restoreIfStale();  // a name an earlier run left behind
        root=new FrameLayout(this);
        if (Build.VERSION.SDK_INT >= 35) root.setOnApplyWindowInsetsListener((view, insets) -> {
            // Android 15 lays apps out under the system bars: keep the page and its buttons clear of them.
            android.graphics.Insets bars=insets.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.displayCutout());
            view.setPadding(bars.left,bars.top,bars.right,bars.bottom);
            return WindowInsets.CONSUMED;
        });
        setContentView(root);
        createPage();
    }
    private void createPage() {
        final int generation=++page;
        web = new WebView(this);
        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setAllowFileAccess(true);
        web.getSettings().setAllowContentAccess(false);
        web.getSettings().setAllowFileAccessFromFileURLs(false);
        web.getSettings().setAllowUniversalAccessFromFileURLs(false);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) { return true; }
            /** Unhandled, a dead renderer (crash or killed for memory) ends the whole app natively,
             * past every give-back of the phone's name. */
            @Override public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
                rendererGone(view, detail!=null&&detail.didCrash());
                return true;
            }
        });
        web.addJavascriptInterface(new Bridge(generation), "Native");
        root.addView(web, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        web.loadUrl("file:///android_asset/index.html");
    }
    /** The boards' decoders lived in that page: stop the boards, give the name back, start a fresh page. */
    private void rendererGone(WebView view, boolean crashed) {
        if(view!=web)return;
        String what="WebView renderer "+(crashed?"crashed":"was ended by Android");
        android.util.Log.w("BoardSimUI", what+"; boards stopped, page rebuilt");
        root.removeView(view);view.destroy();web=null;
        stopAll();
        instances=1;for(Peripheral p:peripherals)p.setMultiplexed(false);
        if(isFinishing()||isDestroyed())return;
        rebuiltNotice=what+". Boards stopped and the page restarted; press Start.";
        createPage();
    }
    private void js(String script) { if(web!=null)web.evaluateJavascript(script, null); }
    /** Status line with the phone's Bluetooth name and the reset button. */
    private void phoneName(AdapterNameGuard.Status state) {
        try {
            JSONObject line=new JSONObject().put("text",state.text()).put("button",state.button()==null?JSONObject.NULL:state.button());
            js("window.BLE&&BLE.phoneName&&BLE.phoneName("+line+")");
        } catch (Exception ignored) { /* The page shows the line once it can. */ }
    }
    public void status(int slot, String text, boolean running) {
        js("BLE.status(" + JSONObject.quote(text) + "," + running + ","+slot+")");
        if(!running){profiles[slot]=null;if(profiles[0]==null&&profiles[1]==null)names.giveBack(this);if(busySlot==slot)ready(slot);}
    }
    public void disconnected(int slot, String peer) { js("BLE.disconnected("+JSONObject.quote(peer)+","+slot+")"); }
    boolean ownedByOther(int slot, android.bluetooth.BluetoothDevice device) {
        return peripherals[1-slot].owns(device);
    }
    void refreshAdvertising() {
        for(Peripheral peripheral:peripherals)peripheral.refreshAdvertising();
    }
    public void receive(int slot, int token, String peer, byte[] data, boolean withResponse, long atMs, java.util.function.Consumer<JSONArray> completion) {
        if (web == null) return;  // stopped with its page; nothing waits for this write any more
        JSONArray bytes = new JSONArray(); for (byte b : data) bytes.put(b & 255);
        String type = withResponse ? "'request'" : "'command'";
        web.evaluateJavascript("BLE.receive(" + bytes + ","+JSONObject.quote(peer)+","+token+","+slot+","+type+","+atMs+")", value -> {
            try { completion.accept(new JSONArray(value)); }
            catch (Exception e) { peripherals[slot].stop();status(slot,"Decoder failure: " + e.getMessage(), false); }
        });
    }
    public void readState(int slot, java.util.function.Consumer<byte[]> completion) {
        if (web == null) return;
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
            // The phone's own name is recorded durably before any board renames it.
            String problem=names.lend(this,profile.getString("name"));
            if(problem!=null){status(slot,problem,false);drain();return;}
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
        names.giveBack(this);
    }
    /** Reset button: stops the boards, then gives the phone its own name back. */
    private void resetName() {
        if (Build.VERSION.SDK_INT >= 31 && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.BLUETOOTH_CONNECT}, 2);
            return;
        }
        boolean[] running={profiles[0]!=null,profiles[1]!=null};
        stopAll();
        for(int slot=0;slot<instances;slot++)if(running[slot])status(slot,"Stopped to reset the phone name",false);
        names.reset();
    }
    @Override public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(request, permissions, results);
        if (request == 2) {
            if (results.length == 1 && results[0] == PackageManager.PERMISSION_GRANTED) resetName(); else names.refresh();
            return;
        }
        if (request != 1) return;
        JSONObject profile = pending; pending = null;
        if (results.length == 2 && results[0] == PackageManager.PERMISSION_GRANTED && results[1] == PackageManager.PERMISSION_GRANTED && profile != null) start(pendingSlot,profile);
        else status(pendingSlot,"Nearby devices permission denied. Grant it in Settings, then Start.", false);
    }
    private final class Bridge {
        private final int generation;
        Bridge(int generation) { this.generation=generation; }
        /** Mirrors the page log into logcat (tag BoardSimUI): `adb logcat -s BoardSimUI BoardSimWire`. */
        @JavascriptInterface public void log(String line) { android.util.Log.i("BoardSimUI", line); }
        @JavascriptInterface public void postMessage(String raw) {
            runOnUiThread(() -> {
                if(generation!=page)return;  // a dead page
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
                        case "resetName": resetName(); break;
                        case "copy": ((ClipboardManager)getSystemService(CLIPBOARD_SERVICE)).setPrimaryClip(ClipData.newPlainText("BoardSimulator", message.getString("text"))); break;
                        case "ready": {
                            js("BLE.canProbe()");
                            String text=slot==0&&rebuiltNotice!=null?rebuiltNotice:"Android " + Build.VERSION.RELEASE + " · Start to check advertiser support";
                            if(slot==0)rebuiltNotice=null;
                            status(slot,text,false);
                            break;
                        }
                        default: throw new IllegalArgumentException("Unknown bridge command");
                    }
                } catch (Exception e) { slot=Math.max(0,Math.min(slot,1));peripherals[slot].stop(); status(slot,"Error: " + e.getMessage(), false); }
            });
        }
    }
    @Override protected void onResume() { super.onResume(); foreground = true; names.restoreIfStale(); }
    @Override protected void onStop() {
        foreground = false; pending = null;
        stopAll();for(int slot=0;slot<instances;slot++)status(slot,"Stopped in background. Press Start to resume.",false);super.onStop();
    }
    @Override protected void onDestroy() {
        stopAll(); names.setListener(null);
        if(web!=null){web.removeJavascriptInterface("Native");web.destroy();web=null;}
        super.onDestroy();
    }
}
