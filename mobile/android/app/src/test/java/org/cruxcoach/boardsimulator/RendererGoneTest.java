package org.cruxcoach.boardsimulator;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNotSame;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.Manifest;
import android.app.Application;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothManager;
import android.content.ClipboardManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Looper;
import android.view.ViewGroup;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.WebView;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.time.Duration;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;

/**
 * The WebView renderer is a separate process. When it dies (crash, or ended
 * by Android) and nobody handles it, WebView ends the whole app natively, past
 * the crash handler, and the phone kept the board's Bluetooth name.
 */
@RunWith(RobolectricTestRunner.class)
public class RendererGoneTest {
    private static final String OWN = "Nokia 6.1";
    private static final String BOARD = "Kilter Board#0001@3";
    private Application app;
    private BluetoothAdapter adapter;

    @Before public void freshProcess() throws Exception {
        // One guard per process; every test is a new process.
        Field guard = AdapterNames.class.getDeclaredField("guard");
        guard.setAccessible(true);
        guard.set(null, null);
        app = RuntimeEnvironment.getApplication();
        shadowOf(app).grantPermissions(Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.BLUETOOTH_ADVERTISE);
        adapter = ((BluetoothManager) app.getSystemService(Context.BLUETOOTH_SERVICE)).getAdapter();
        shadowOf(adapter).setEnabled(true);
        adapter.setName(OWN);
    }

    private static WebView page(MainActivity activity) {
        ViewGroup content = activity.findViewById(android.R.id.content);
        ViewGroup root = (ViewGroup) content.getChildAt(0);
        assertEquals("exactly one page", 1, root.getChildCount());
        return (WebView) root.getChildAt(0);
    }

    private static RenderProcessGoneDetail crash() {
        return new RenderProcessGoneDetail() {
            @Override public boolean didCrash() { return true; }
            @Override public int rendererPriorityAtExit() { return 0; }
        };
    }

    private static void post(Object bridge, String message) throws Exception {
        Method postMessage = bridge.getClass().getMethod("postMessage", String.class);
        postMessage.setAccessible(true);
        postMessage.invoke(bridge, message);
    }

    private int receiverState() {
        return app.getPackageManager().getComponentEnabledSetting(new ComponentName(app, NameRecoveryReceiver.class));
    }

    @Test public void aDeadRendererGivesTheNameBackAndRebuildsThePage() {
        MainActivity activity = Robolectric.buildActivity(MainActivity.class).setup().get();
        // What a board start does: record the own name, then rename the phone.
        assertNull(AdapterNames.get(activity).lend(activity, BOARD));
        adapter.setName(BOARD);
        assertEquals(PackageManager.COMPONENT_ENABLED_STATE_ENABLED, receiverState());

        WebView dead = page(activity);
        assertTrue("handled, so the app lives on", shadowOf(dead).getWebViewClient().onRenderProcessGone(dead, crash()));
        assertTrue(shadowOf(dead).wasDestroyCalled());
        WebView fresh = page(activity);
        assertNotSame(dead, fresh);
        assertNotNull(shadowOf(fresh).getJavascriptInterface("Native"));
        assertEquals("file:///android_asset/index.html", shadowOf(fresh).getLastLoadedUrl());
        assertFalse(activity.isFinishing());

        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(10));
        assertEquals(OWN, adapter.getName());
        assertFalse("record closed", app.getSharedPreferences("adapter_name", Context.MODE_PRIVATE).getBoolean("open", false));
        assertEquals(PackageManager.COMPONENT_ENABLED_STATE_DISABLED, receiverState());
    }

    @Test public void aDeadPagesBridgeGoesQuiet() throws Exception {
        MainActivity activity = Robolectric.buildActivity(MainActivity.class).setup().get();
        WebView dead = page(activity);
        Object stale = shadowOf(dead).getJavascriptInterface("Native");
        shadowOf(dead).getWebViewClient().onRenderProcessGone(dead, crash());
        Object current = shadowOf(page(activity)).getJavascriptInterface("Native");
        ClipboardManager clipboard = (ClipboardManager) activity.getSystemService(Context.CLIPBOARD_SERVICE);

        post(stale, "{\"command\":\"copy\",\"text\":\"stale\"}");
        shadowOf(Looper.getMainLooper()).idle();
        assertFalse("a message queued by the dead page is ignored", clipboard.hasPrimaryClip());

        post(current, "{\"command\":\"copy\",\"text\":\"fresh\"}");
        shadowOf(Looper.getMainLooper()).idle();
        assertEquals("fresh", clipboard.getPrimaryClip().getItemAt(0).getText().toString());
    }
}
