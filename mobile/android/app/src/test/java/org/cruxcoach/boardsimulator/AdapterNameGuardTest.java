package org.cruxcoach.boardsimulator;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.PriorityQueue;
import org.junit.Before;
import org.junit.Test;

public class AdapterNameGuardTest {
    private static final String OWN = "Nokia 6.1";
    private static final String QUANTUM = "QB_020000000001";
    private static final AdapterNameRules RULES = AdapterNameRules.parse(
            "=" + QUANTUM + "\n=MoonBoard\n^Kilter Board#\n^ShinyWall_\n");

    /** Virtual time: posted tasks run in order when a test advances. */
    static final class Clock implements AdapterNameGuard.Scheduler {
        private static final class Task {
            final long at, seq; final Runnable run;
            Task(long at, long seq, Runnable run) { this.at = at; this.seq = seq; this.run = run; }
        }
        long now, seq;
        final PriorityQueue<Task> tasks = new PriorityQueue<>((a, b) ->
                a.at != b.at ? Long.compare(a.at, b.at) : Long.compare(a.seq, b.seq));
        @Override public void post(long delayMs, Runnable task) { tasks.add(new Task(now + delayMs, seq++, task)); }
        void advance(long ms) {
            long end = now + ms;
            while (!tasks.isEmpty() && tasks.peek().at <= end) { Task task = tasks.poll(); now = task.at; task.run.run(); }
            now = end;
        }
    }

    /** Android's adapter: a rename lands a little later, in the order sent. */
    static final class Radio implements AdapterNameGuard.Radio {
        final Clock clock;
        String name = OWN, device = OWN;
        boolean on = true, denied, refuse;
        final List<String> sent = new ArrayList<>();
        Radio(Clock clock) { this.clock = clock; }
        @Override public boolean isOn() { return on; }
        @Override public String name() { if (denied) throw new SecurityException("no permission"); return name; }
        @Override public boolean setName(String value) {
            if (denied) throw new SecurityException("no permission");
            if (!on || refuse) return false;
            sent.add(value);
            clock.post(100, () -> name = value);
            return true;
        }
        @Override public String deviceName() { return device; }
    }

    static final class Store implements AdapterNameGuard.Store {
        AdapterNameGuard.Record record;
        boolean failSave;
        @Override public AdapterNameGuard.Record load() { return record; }
        @Override public boolean save(AdapterNameGuard.Record value) { if (failSave) return false; record = value; return true; }
        @Override public boolean clear() { record = null; return true; }
    }

    private Clock clock;
    private Radio radio;
    private Store store;
    private Boolean armed;
    private final Object activity = new Object(), probe = new Object();
    private final List<String> log = new ArrayList<>();

    @Before public void setUp() {
        clock = new Clock();
        radio = new Radio(clock);
        store = new Store();
    }

    /** A new guard on the same store and adapter, as after a process restart. */
    private AdapterNameGuard guard() {
        return new AdapterNameGuard(RULES, radio, store, clock, on -> armed = on, log::add);
    }

    /** What Peripheral does after a successful lend. */
    private void boardRenames(String name) { radio.setName(name); }

    @Test public void recordsTheOwnNameBeforeTheFirstRename() {
        AdapterNameGuard guard = guard();
        assertNull(guard.lend(activity, QUANTUM));
        assertEquals(OWN, store.record.original);
        assertTrue(radio.sent.isEmpty());  // nothing renamed yet: Peripheral renames after the lend
        assertTrue(armed);
        assertEquals(AdapterNameGuard.Phase.LENT, guard.status().phase);
    }

    @Test public void givesTheNameBackAndClosesTheRecord() {
        AdapterNameGuard guard = guard();
        guard.lend(activity, QUANTUM);
        boardRenames(QUANTUM);
        clock.advance(200);
        guard.giveBack(activity);
        assertEquals(OWN, radio.sent.get(radio.sent.size() - 1));
        clock.advance(10_000);
        assertEquals(OWN, radio.name);
        assertNull(store.record);
        assertFalse(armed);
        assertEquals(AdapterNameGuard.Phase.OWN, guard.status().phase);
        assertNull(guard.status().button());
    }

    @Test public void anOpenRecordIsNeverOverwritten() {
        AdapterNameGuard guard = guard();
        guard.lend(activity, QUANTUM);
        boardRenames(QUANTUM);
        clock.advance(200);
        assertNull(guard.lend(activity, "MoonBoard"));  // board switch while running
        assertEquals(OWN, store.record.original);
        AdapterNameGuard restarted = guard();  // process died, name still QUANTUM
        assertNull(restarted.lend(activity, "Kilter Board#0001@3"));
        assertEquals(OWN, store.record.original);
        assertEquals(3, store.record.lent.size());
    }

    @Test public void aBoardNameIsNeverRecordedAsTheOwnName() {
        radio.name = QUANTUM;  // left behind by a build without this guard
        AdapterNameGuard guard = guard();
        String problem = guard.lend(activity, "MoonBoard");
        assertNotNull(problem);
        assertTrue(problem, problem.contains("board name"));
        assertNull(store.record);
        assertTrue(radio.sent.isEmpty());
        radio.name = "CruxCoach\u00b7Kilter Board@3";  // CruxCoach shares a board on this phone
        assertNotNull(guard.lend(activity, "MoonBoard"));
        assertNull(store.record);
    }

    @Test public void aRecordHoldingABoardNameIsDropped() {
        store.record = new AdapterNameGuard.Record("Kilter Board#0001@3", Collections.emptyList());
        radio.name = "MoonBoard";
        AdapterNameGuard guard = guard();
        guard.restoreIfStale();
        assertNull(store.record);
        assertTrue(radio.sent.isEmpty());  // never "restored" to a board name
        AdapterNameGuard.Status status = guard.status();
        assertEquals(AdapterNameGuard.Phase.UNKNOWN, status.phase);
        assertEquals("Set phone name to " + OWN, status.button());
        guard.reset();  // the user confirms the device name
        clock.advance(2_000);
        assertEquals(OWN, radio.name);
        assertNull(store.record);
        assertEquals(AdapterNameGuard.Phase.OWN, guard.status().phase);
    }

    @Test public void aBlankRecordIsDropped() {
        store.record = new AdapterNameGuard.Record(" ", Collections.emptyList());
        guard().restoreIfStale();
        assertNull(store.record);
        assertTrue(radio.sent.isEmpty());
    }

    @Test public void unknownOwnNameWithoutDeviceNameShowsNoButton() {
        radio.name = QUANTUM;
        radio.device = "MoonBoard";
        AdapterNameGuard.Status status = guard().status();
        assertEquals(AdapterNameGuard.Phase.UNKNOWN, status.phase);
        assertNull(status.button());
        assertTrue(status.text(), status.text().contains("Android Settings"));
    }

    @Test public void aRenameStillInFlightAtStopIsOvertaken() {
        AdapterNameGuard guard = guard();
        guard.lend(activity, QUANTUM);
        boardRenames(QUANTUM);  // lands at 100 ms
        guard.giveBack(activity);  // at 0 ms the own name still shows
        assertEquals(OWN, radio.sent.get(radio.sent.size() - 1));
        clock.advance(2_000);
        assertNotNull("record closes only after the settle check", store.record);
        clock.advance(10_000);
        assertEquals(OWN, radio.name);
        assertNull(store.record);
    }

    @Test public void theNextProcessGivesTheNameBack() {
        guard().lend(activity, QUANTUM);
        boardRenames(QUANTUM);
        clock.advance(200);
        // Killed: no giveBack. The next start (or the recovery receiver) restores.
        AdapterNameGuard next = guard();
        List<String> calls = new ArrayList<>();
        next.restoreIfStale(() -> calls.add("settled"));
        assertTrue(calls.isEmpty());
        clock.advance(10_000);
        assertEquals(OWN, radio.name);
        assertNull(store.record);
        assertEquals(Collections.singletonList("settled"), calls);
        assertTrue(log.contains("Phone's own Bluetooth name is back: " + OWN));
    }

    @Test public void aNameTheUserChoseMeanwhileWins() {
        store.record = new AdapterNameGuard.Record(OWN, Collections.singletonList(QUANTUM));
        radio.name = "Sam's phone";
        AdapterNameGuard guard = guard();
        guard.restoreIfStale();
        assertTrue(radio.sent.isEmpty());
        assertNull(store.record);
        assertEquals("Sam's phone", guard.status().current);
    }

    @Test public void aTypedShinyWallNameIsNoUserChoice() {
        guard().lend(activity, "Climbbox1");
        boardRenames("Climbbox1");
        clock.advance(200);
        AdapterNameGuard next = guard();
        next.restoreIfStale();
        clock.advance(10_000);
        assertEquals(OWN, radio.name);
        assertNull(store.record);
    }

    @Test public void bluetoothOffKeepsTheRecordUntilItIsOn() {
        AdapterNameGuard guard = guard();
        guard.lend(activity, QUANTUM);
        boardRenames(QUANTUM);
        clock.advance(200);
        radio.on = false;
        guard.giveBack(activity);
        clock.advance(120_000);
        assertNotNull(store.record);
        assertTrue(armed);
        assertTrue(guard.status().text(), guard.status().text().contains("once Bluetooth is on"));
        radio.on = true;
        guard.restoreIfStale();  // STATE_ON
        clock.advance(10_000);
        assertEquals(OWN, radio.name);
        assertNull(store.record);
    }

    @Test public void bluetoothOffAtStartRenamesNothing() {
        radio.on = false;
        assertNotNull(guard().lend(activity, QUANTUM));
        assertNull(store.record);
    }

    @Test public void missingPermissionKeepsTheRecord() {
        AdapterNameGuard guard = guard();
        guard.lend(activity, QUANTUM);
        boardRenames(QUANTUM);
        clock.advance(200);
        radio.denied = true;
        guard.giveBack(activity);
        assertNotNull(store.record);
        assertEquals(AdapterNameGuard.Phase.NEEDS_PERMISSION, guard.status().phase);
        assertEquals("Reset phone name", guard.status().button());
        radio.denied = false;
        guard.reset();
        clock.advance(10_000);
        assertEquals(OWN, radio.name);
        assertNull(store.record);
    }

    @Test public void theNameStaysOutWhileAnotherOwnerUsesIt() {
        AdapterNameGuard guard = guard();
        guard.lend(activity, QUANTUM);
        guard.lend(probe, "Kilter Board#p1@3");
        boardRenames("Kilter Board#p1@3");
        clock.advance(200);
        guard.giveBack(activity);
        clock.advance(10_000);
        assertFalse(radio.sent.contains(OWN));
        guard.giveBack(probe);
        clock.advance(10_000);
        assertEquals(OWN, radio.name);
    }

    @Test public void aFailedCommitRefusesTheRename() {
        store.failSave = true;
        AdapterNameGuard guard = guard();
        assertEquals(AdapterNameGuard.CANNOT_SAVE, guard.lend(activity, QUANTUM));
        assertNull(store.record);
        assertEquals(AdapterNameGuard.Phase.OWN, guard.status().phase);
    }

    @Test public void aRefusedRenameIsRetriedThenWaitsForTheNextTrigger() {
        AdapterNameGuard guard = guard();
        guard.lend(activity, QUANTUM);
        boardRenames(QUANTUM);
        clock.advance(200);
        radio.refuse = true;
        guard.giveBack(activity);
        clock.advance(300_000);
        assertNotNull(store.record);
        assertTrue(clock.tasks.isEmpty());  // no endless polling
        radio.refuse = false;
        guard.restoreIfStale();
        clock.advance(10_000);
        assertEquals(OWN, radio.name);
        assertNull(store.record);
    }

    @Test public void lastWordsSendTheNameAndKeepTheRecord() {
        AdapterNameGuard guard = guard();
        guard.lend(activity, QUANTUM);
        boardRenames(QUANTUM);
        clock.advance(200);
        guard.lastWords();
        assertEquals(OWN, radio.sent.get(radio.sent.size() - 1));
        assertNotNull(store.record);  // the next start verifies and closes it
    }

    @Test public void lendingAgainCancelsAPendingGiveBack() {
        AdapterNameGuard guard = guard();
        guard.lend(activity, QUANTUM);
        boardRenames(QUANTUM);
        clock.advance(200);
        guard.giveBack(activity);
        assertNull(guard.lend(activity, "MoonBoard"));  // Start again right away
        boardRenames("MoonBoard");
        clock.advance(10_000);
        assertEquals("MoonBoard", radio.name);
        assertEquals(OWN, store.record.original);
        assertEquals(AdapterNameGuard.Phase.LENT, guard.status().phase);
    }

    @Test public void statusLineNamesBothNames() {
        AdapterNameGuard guard = guard();
        List<AdapterNameGuard.Status> seen = new ArrayList<>();
        guard.setListener(seen::add);
        assertEquals("Bluetooth name: " + OWN, seen.get(0).text());
        guard.lend(activity, QUANTUM);
        boardRenames(QUANTUM);
        clock.advance(200);
        AdapterNameGuard.Status lent = guard.status();
        assertEquals("Bluetooth name: " + QUANTUM + " \u00b7 " + OWN + " back on Stop", lent.text());
        assertEquals("Reset phone name", lent.button());
    }
}
