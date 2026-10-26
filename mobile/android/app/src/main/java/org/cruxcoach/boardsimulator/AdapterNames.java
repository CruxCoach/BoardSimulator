package org.cruxcoach.boardsimulator;

import android.annotation.SuppressLint;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothManager;
import android.content.BroadcastReceiver;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.util.Log;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Collections;

/**
 * Android side of {@link AdapterNameGuard}: one guard per process, its record
 * in SharedPreferences (excluded from backup by allowBackup=false, so one
 * phone's record never renames another), the Bluetooth adapter, the recovery
 * receiver switch and a crash hook. Logcat tag BoardSimName.
 */
@SuppressLint("MissingPermission") // Every adapter call is guarded by the SecurityException handling of the guard.
final class AdapterNames {
    static final String TAG = "BoardSimName";
    private static final String PREFS = "adapter_name";
    private static final String KEY_OPEN = "open";
    private static final String KEY_ORIGINAL = "original";
    private static final String KEY_LENT = "lent";
    private static AdapterNameGuard guard;

    private AdapterNames() {}

    /** The process-wide guard; call on the main thread. */
    static AdapterNameGuard get(Context context) {
        if (guard != null) return guard;
        final Context app = context.getApplicationContext();
        final Handler main = new Handler(Looper.getMainLooper());
        final AdapterNameGuard created = new AdapterNameGuard(rules(app), new AndroidRadio(app),
                new PrefsStore(app.getSharedPreferences(PREFS, Context.MODE_PRIVATE)),
                (delayMs, task) -> main.postDelayed(task, delayMs),
                armed -> arm(app, armed), line -> Log.i(TAG, line));
        guard = created;
        BroadcastReceiver bluetooth = new BroadcastReceiver() {
            @Override public void onReceive(Context c, Intent intent) {
                if (BluetoothAdapter.ACTION_STATE_CHANGED.equals(intent.getAction()) &&
                        intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, -1) == BluetoothAdapter.STATE_ON) {
                    created.restoreIfStale();  // setName needs the adapter on
                } else {
                    created.refresh();  // renamed by us, a board or Android's settings
                }
            }
        };
        IntentFilter filter = new IntentFilter(BluetoothAdapter.ACTION_STATE_CHANGED);
        filter.addAction(BluetoothAdapter.ACTION_LOCAL_NAME_CHANGED);
        // Exported: LOCAL_NAME_CHANGED comes from the Bluetooth process. Both are protected broadcasts.
        if (Build.VERSION.SDK_INT >= 33) app.registerReceiver(bluetooth, filter, Context.RECEIVER_EXPORTED);
        else app.registerReceiver(bluetooth, filter);
        final Thread.UncaughtExceptionHandler previous = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((thread, error) -> {
            try {
                created.lastWords();
            } catch (Throwable ignored) {
                // The crash itself must still reach Android's handler.
            }
            if (previous != null) previous.uncaughtException(thread, error);
        });
        return created;
    }

    private static AdapterNameRules rules(Context context) {
        try (InputStream input = context.getAssets().open("generated/adapter_names.txt")) {
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[4096];
            int count;
            while ((count = input.read(chunk)) != -1) buffer.write(chunk, 0, count);
            AdapterNameRules rules = AdapterNameRules.parse(new String(buffer.toByteArray(), StandardCharsets.UTF_8));
            Log.i(TAG, rules.boardRules() + " board name rules loaded");
            return rules;
        } catch (Exception e) {
            Log.w(TAG, "No board name list (run tools/export_mobile.py); only sharing names are known", e);
            return AdapterNameRules.parse("");
        }
    }

    /** NameRecoveryReceiver listens to every Bluetooth link, so it is on only while a record is open. */
    private static void arm(Context context, boolean armed) {
        try {
            PackageManager pm = context.getPackageManager();
            ComponentName component = new ComponentName(context, NameRecoveryReceiver.class);
            int wanted = armed ? PackageManager.COMPONENT_ENABLED_STATE_ENABLED : PackageManager.COMPONENT_ENABLED_STATE_DISABLED;
            if (pm.getComponentEnabledSetting(component) != wanted) {
                pm.setComponentEnabledSetting(component, wanted, PackageManager.DONT_KILL_APP);
            }
        } catch (RuntimeException e) {
            Log.w(TAG, "Could not switch the name recovery receiver", e);
        }
    }

    private static final class AndroidRadio implements AdapterNameGuard.Radio {
        private final Context context;

        AndroidRadio(Context context) { this.context = context; }

        private BluetoothAdapter adapter() {
            BluetoothManager manager = (BluetoothManager) context.getSystemService(Context.BLUETOOTH_SERVICE);
            return manager == null ? null : manager.getAdapter();
        }

        @Override public boolean isOn() {
            BluetoothAdapter adapter = adapter();
            return adapter != null && adapter.isEnabled();
        }

        @Override public String name() {
            BluetoothAdapter adapter = adapter();
            return adapter == null ? null : adapter.getName();
        }

        @Override public boolean setName(String name) {
            BluetoothAdapter adapter = adapter();
            return adapter != null && adapter.setName(name);
        }

        @Override public String deviceName() {
            return Settings.Global.getString(context.getContentResolver(), Settings.Global.DEVICE_NAME);
        }
    }

    private static final class PrefsStore implements AdapterNameGuard.Store {
        private final SharedPreferences prefs;

        PrefsStore(SharedPreferences prefs) { this.prefs = prefs; }

        @Override public AdapterNameGuard.Record load() {
            if (!prefs.getBoolean(KEY_OPEN, false)) return null;
            String lent = prefs.getString(KEY_LENT, "");
            return new AdapterNameGuard.Record(prefs.getString(KEY_ORIGINAL, null),
                    lent.isEmpty() ? Collections.emptyList() : Arrays.asList(lent.split("\n")));
        }

        @Override public boolean save(AdapterNameGuard.Record record) {
            return prefs.edit().putBoolean(KEY_OPEN, true).putString(KEY_ORIGINAL, record.original)
                    .putString(KEY_LENT, String.join("\n", record.lent)).commit();
        }

        @Override public boolean clear() {
            return prefs.edit().clear().commit();
        }
    }
}
