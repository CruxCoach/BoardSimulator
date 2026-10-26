package org.cruxcoach.boardsimulator;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Wakes the app to give the phone its Bluetooth name back after the app ended
 * while a board used it: phone restarted, app updated, or a Bluetooth link
 * changed (a killed app's controller link drops at once; the next car or
 * headset link is when the name matters).
 *
 * Disabled in the manifest; {@link AdapterNames} switches it on only while a
 * name record is open, so headphones do not start the app. Android delivers
 * nothing after "Force stop" until the app is opened again; then the app
 * start gives the name back.
 */
public final class NameRecoveryReceiver extends BroadcastReceiver {
    /** Well inside the time Android gives a receiver. */
    private static final long MAX_WAIT_MS = 8_000;
    private static final Set<String> ACTIONS = new HashSet<>(Arrays.asList(
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED,
            "android.bluetooth.device.action.ACL_CONNECTED",
            "android.bluetooth.device.action.ACL_DISCONNECTED"));

    @Override public void onReceive(Context context, Intent intent) {
        if (!ACTIONS.contains(intent.getAction())) return;
        final PendingResult pending = goAsync();
        final AtomicBoolean finished = new AtomicBoolean();
        Runnable finish = () -> { if (finished.compareAndSet(false, true)) pending.finish(); };
        AdapterNames.get(context).restoreIfStale(finish);
        new Handler(Looper.getMainLooper()).postDelayed(finish, MAX_WAIT_MS);
    }
}
