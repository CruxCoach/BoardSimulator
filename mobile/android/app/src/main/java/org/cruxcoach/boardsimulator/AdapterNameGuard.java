package org.cruxcoach.boardsimulator;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.function.Consumer;

/**
 * The phone's Bluetooth name, lent to simulated boards and always given back.
 *
 * Android has no name per advertisement: a board's scan name renames the whole
 * phone, and cars and headphones show it too. Before the first rename the
 * phone's own name is committed to storage; an open record is never
 * overwritten and a board name is never recorded as the phone's own. The name
 * goes back once every board stopped, on the next app start, and whenever the
 * recovery receiver wakes the app while a record is open (boot, app update,
 * Bluetooth links). The record closes only once the own name is seen back.
 *
 * Main thread only. Android sits behind the small interfaces below so the
 * rules run in JVM tests.
 */
final class AdapterNameGuard {
    interface Radio {
        /** Adapter present and on; Android renames only then. */
        boolean isOn();
        /** The current name or null. Throws SecurityException without Nearby devices permission. */
        String name();
        /** Sends a rename, which lands asynchronously. Throws SecurityException without permission. */
        boolean setName(String name);
        /** Android's device name (About phone), or null. */
        String deviceName();
    }

    /** Durable record of the phone's own name; writes are synchronous commits. */
    interface Store {
        /** The open record or null. */
        Record load();
        boolean save(Record record);
        boolean clear();
    }

    interface Scheduler { void post(long delayMs, Runnable task); }

    /** Switches the receiver that wakes the app while a record is open. */
    interface Wakeups { void setArmed(boolean armed); }

    interface Listener { void changed(Status status); }

    static final class Record {
        final String original;
        /** Names lent while this record is open: a typed Shiny Wall name matches no rule. */
        final List<String> lent;

        Record(String original, List<String> lent) {
            this.original = original;
            this.lent = Collections.unmodifiableList(new ArrayList<>(lent));
        }

        Record lending(String name) {
            if (lent.contains(name)) return this;
            List<String> names = new ArrayList<>(lent);
            names.add(name);
            while (names.size() > MAX_LENT) names.remove(0);
            return new Record(original, names);
        }
    }

    enum Phase { OWN, UNREADABLE, UNKNOWN, LENT, RESTORING, NEEDS_PERMISSION }

    /** What the status line shows. */
    static final class Status {
        final Phase phase;
        final String current, original, suggestion;
        final boolean bluetoothOff;

        Status(Phase phase, String current, String original, String suggestion, boolean bluetoothOff) {
            this.phase = phase; this.current = current; this.original = original;
            this.suggestion = suggestion; this.bluetoothOff = bluetoothOff;
        }

        String text() {
            String name = "Phone Bluetooth name: " + (current == null ? "unknown" : current);
            switch (phase) {
                case OWN: return name;
                case UNREADABLE:
                    return "Phone Bluetooth name: not readable" +
                            (bluetoothOff ? " while Bluetooth is off" : " (Nearby devices permission missing)");
                case UNKNOWN:
                    return name + " is a board name; the phone's own name is unknown." +
                            (suggestion == null ? " Rename the phone in Android Settings > About phone > Device name." : "");
                case LENT: return name + " · lent to the simulator, " + original + " comes back on Stop";
                case NEEDS_PERMISSION: return "Phone Bluetooth name: giving back " + original + " needs the Nearby devices permission";
                default:
                    if (original.equals(current)) return name + " · back, confirming";
                    return name + " · giving back " + original + (bluetoothOff ? " once Bluetooth is on" : "");
            }
        }

        /** Label of the reset button, null to hide it. */
        String button() {
            switch (phase) {
                case LENT: case RESTORING: case NEEDS_PERMISSION: return "Reset phone name";
                case UNKNOWN: return suggestion == null ? null : "Set phone name to " + suggestion;
                default: return null;
            }
        }
    }

    /** Waits after each attempt to give the name back; then the next trigger retries. */
    static final long[] RETRY_MS = {1_500, 5_000, 15_000, 60_000};
    /** A board rename sent just before the give-back may still land after it: look once more. */
    static final long SETTLE_MS = 1_500;
    static final int MAX_LENT = 16;
    static final String CANNOT_SAVE = "Could not save the phone's Bluetooth name, so it is not changed.";

    private enum Sent { OK, REFUSED, DENIED }

    private final AdapterNameRules rules;
    private final Radio radio;
    private final Store store;
    private final Scheduler scheduler;
    private final Wakeups wakeups;
    private final Consumer<String> log;
    private Listener listener;
    /** Who uses the name right now (main activity, routing probe). Never persisted. */
    private final Set<Object> owners = new HashSet<>();
    private final List<Runnable> waiting = new ArrayList<>();
    /** A board rename was allowed and may still land after a give-back. */
    private boolean renameInFlight;
    /** This give-back run already sent the own name. */
    private boolean sent;
    private int attempt, generation;
    private boolean queued;
    private Boolean armed;

    AdapterNameGuard(AdapterNameRules rules, Radio radio, Store store, Scheduler scheduler,
                     Wakeups wakeups, Consumer<String> log) {
        this.rules = rules; this.radio = radio; this.store = store;
        this.scheduler = scheduler; this.wakeups = wakeups; this.log = log;
    }

    void setListener(Listener listener) {
        this.listener = listener;
        publish();
    }

    /**
     * Records the phone's own name (once per record) before {@code owner}
     * renames the phone to {@code desired}. Null means go ahead; otherwise the
     * reason why the phone must not be renamed.
     */
    String lend(Object owner, String desired) {
        cancel();
        String problem;
        try { problem = record(desired); }
        catch (SecurityException e) { problem = "Nearby devices permission missing: the phone's Bluetooth name cannot be read."; }
        if (problem == null) {
            owners.add(owner);
            renameInFlight = true;
            arm(true);
            done();
        } else if (owners.isEmpty()) {
            restart();  // resume a give-back this call interrupted
        } else {
            done();
        }
        return problem;
    }

    private String record(String desired) {
        if (!radio.isOn()) return "Bluetooth unavailable/off. Enable Bluetooth in Settings.";
        String current = read();
        Record record = openRecord();
        if (record != null && current != null && !current.equals(record.original) &&
                !ours(current, record) && !renameInFlight) {
            log.accept("Phone was renamed to " + current + " while its name was out; that is its own name now");
            if (!store.clear()) return CANNOT_SAVE;
            record = null;
        }
        if (record == null) {
            if (current == null) return "Cannot read the phone's Bluetooth name, so it is not changed.";
            if (rules.usableOwnName(current) == null) {
                return "The phone's Bluetooth name " + current + " is a board name and its own name is unknown. " +
                        "Set the phone name first (top of the page or Android Settings).";
            }
            // Never change a phone-wide setting before its own value is durably known.
            if (!store.save(new Record(current, Collections.emptyList()).lending(desired))) return CANNOT_SAVE;
            log.accept("Recorded the phone's own Bluetooth name " + current);
            return null;
        }
        Record updated = record.lending(desired);
        if (updated != record && !store.save(updated)) return CANNOT_SAVE;
        return null;
    }

    /** {@code owner} stopped using the name; once nobody uses it, it goes back. */
    void giveBack(Object owner) {
        boolean wasOwner = owners.remove(owner);
        if (!owners.isEmpty() || (!wasOwner && queued)) { publish(); return; }
        restart();
    }

    /** Gives back a name an earlier run (or a failed give-back) left behind. Safe any time. */
    void restoreIfStale() { restoreIfStale(null); }

    /** As {@link #restoreIfStale()}; {@code whenSettled} runs once nothing is left to do for now. */
    void restoreIfStale(Runnable whenSettled) {
        if (whenSettled != null) waiting.add(whenSettled);
        if (!owners.isEmpty()) { done(); return; }
        if (queued) return;  // a give-back is under way and settles the callback
        restart();
    }

    /**
     * The reset button: gives a recorded name back now, or, for a phone whose
     * own name is unknown, sets the device name the button showed. Callers
     * stop their boards first.
     */
    void reset() {
        if (!owners.isEmpty()) { publish(); return; }
        Status status = status();
        if (status.phase == Phase.UNKNOWN && status.suggestion != null) {
            Sent result = send(status.suggestion);
            log.accept("Set phone name to the device name " + status.suggestion + ": " + result);
            publish();
            scheduler.post(RETRY_MS[0], this::publish);
            return;
        }
        restart();
    }

    void refresh() { publish(); }

    /** For a dying process: send the own name once, keep the record so the next start verifies it. */
    void lastWords() {
        try {
            Record record = store.load();
            if (record != null && rules.usableOwnName(record.original) != null) radio.setName(record.original);
        } catch (RuntimeException ignored) {
            // Nothing more can be done in a crashing process.
        }
    }

    Status status() {
        boolean off = !radio.isOn();
        String current = null;
        boolean denied = false;
        try { current = read(); } catch (SecurityException e) { denied = true; }
        Record record = store.load();
        String original = record == null ? null : rules.usableOwnName(record.original);
        if (original != null) {
            Phase phase = !owners.isEmpty() ? Phase.LENT : denied ? Phase.NEEDS_PERMISSION : Phase.RESTORING;
            return new Status(phase, current, original, null, off);
        }
        if (current == null) return new Status(Phase.UNREADABLE, null, null, null, off);
        if (rules.isBoardName(current)) return new Status(Phase.UNKNOWN, current, null, suggestion(), off);
        return new Status(Phase.OWN, current, null, null, off);
    }

    private void restart() {
        cancel();
        attempt = 0;
        sent = false;
        step();
    }

    private void cancel() {
        generation++;
        queued = false;
    }

    private void step() {
        queued = false;
        if (!owners.isEmpty()) { done(); return; }
        Record record = openRecord();
        if (record == null) {
            renameInFlight = false;
            arm(false);
            done();
            return;
        }
        arm(true);
        if (!radio.isOn()) { done(); return; }  // Bluetooth on retries
        String current;
        try { current = read(); } catch (SecurityException e) { done(); return; }
        boolean back = record.original.equals(current);
        if (back && !renameInFlight) { finish(record); return; }
        if (back && sent) {
            renameInFlight = false;
            later(SETTLE_MS);
            return;
        }
        if (!back && current != null && !ours(current, record) && !renameInFlight) {
            log.accept("Phone was renamed to " + current + " meanwhile; keeping that name");
            finish(record);
            return;
        }
        if (attempt >= RETRY_MS.length) {
            log.accept("Bluetooth name not back yet; trying again on the next start or Bluetooth event");
            done();
            return;
        }
        long wait = RETRY_MS[attempt++];
        // Sent even while the own name still shows if a board rename may be in
        // flight: Android applies renames in order, so this one lands last.
        Sent result = send(record.original);
        if (result == Sent.DENIED) { done(); return; }
        if (result == Sent.OK) sent = true;
        publish();
        later(wait);
    }

    private void finish(Record record) {
        if (!store.clear()) { done(); return; }
        if (record.original.equals(safeName())) log.accept("Phone's own Bluetooth name is back: " + record.original);
        renameInFlight = false;
        sent = false;
        attempt = 0;
        arm(false);
        done();
    }

    private void done() {
        publish();
        List<Runnable> settled = new ArrayList<>(waiting);
        waiting.clear();
        for (Runnable task : settled) task.run();
    }

    private void later(long delayMs) {
        final int expected = generation;
        queued = true;
        scheduler.post(delayMs, () -> { if (expected == generation) step(); });
    }

    /** The open record; one holding no usable own name is dropped, never given "back". */
    private Record openRecord() {
        Record record = store.load();
        if (record == null || rules.usableOwnName(record.original) != null) return record;
        log.accept("Dropping a name record that holds a board name: " + record.original);
        store.clear();
        return null;
    }

    private boolean ours(String name, Record record) {
        return rules.isBoardName(name) || record.lent.contains(name);
    }

    private String read() {
        String name = radio.name();
        return name == null || name.trim().isEmpty() ? null : name;
    }

    private String safeName() {
        try { return read(); } catch (SecurityException e) { return null; }
    }

    private String suggestion() {
        try { return rules.usableOwnName(radio.deviceName()); } catch (RuntimeException e) { return null; }
    }

    private Sent send(String name) {
        try { return radio.setName(name) ? Sent.OK : Sent.REFUSED; }
        catch (SecurityException e) { return Sent.DENIED; }
        catch (RuntimeException e) { return Sent.REFUSED; }
    }

    private void arm(boolean on) {
        if (armed != null && armed == on) return;
        armed = on;
        wakeups.setArmed(on);
    }

    private void publish() {
        if (listener != null) listener.changed(status());
    }
}
