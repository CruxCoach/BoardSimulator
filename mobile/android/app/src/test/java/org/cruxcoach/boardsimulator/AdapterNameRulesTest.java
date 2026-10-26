package org.cruxcoach.boardsimulator;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import org.junit.Test;

public class AdapterNameRulesTest {
    /** Written by tools/export_mobile.py; CI exports before the Gradle build. */
    private static AdapterNameRules generated() throws Exception {
        File file = new File("../../shared/generated/adapter_names.txt");
        assertTrue("Run tools/export_mobile.py first: " + file.getAbsolutePath(), file.isFile());
        return AdapterNameRules.parse(new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8));
    }

    @Test public void parsesExactNamesAndPrefixes() {
        AdapterNameRules rules = AdapterNameRules.parse("# comment\n=MoonBoard\r\n^Kilter Board#\n\n");
        assertEquals(2, rules.boardRules());
        assertTrue(rules.isBoardName("MoonBoard"));
        assertTrue(rules.isBoardName(" MoonBoard "));
        assertTrue(rules.isBoardName("Kilter Board#0001@3"));
        assertFalse(rules.isBoardName("Kilter Board"));
        assertFalse(rules.isBoardName("MoonBoard fan"));
        assertFalse(rules.isBoardName(null));
    }

    @Test public void cruxCoachSharingNamesAreNeverOwnNames() {
        AdapterNameRules rules = AdapterNameRules.parse("");
        assertTrue(rules.isBoardName("CruxCoach\u00b7Kilter Board@3"));
        assertTrue(rules.isBoardName("CruxCoach@3"));
        assertTrue(rules.isBoardName("CruxRelay Kilter"));
        assertFalse(rules.isBoardName("CruxCoach Test"));
    }

    @Test public void blankNamesAreNotUsable() {
        AdapterNameRules rules = AdapterNameRules.parse("=MoonBoard");
        assertNull(rules.usableOwnName(null));
        assertNull(rules.usableOwnName("  "));
        assertNull(rules.usableOwnName("MoonBoard"));
        assertEquals("Nokia 6.1", rules.usableOwnName("Nokia 6.1"));
    }

    @Test public void generatedListCoversEveryBoardTheAppCanAdvertise() throws Exception {
        AdapterNameRules rules = generated();
        assertTrue(rules.boardRules() >= 10);
        String[] boards = {
            "Kilter Board#0001@3", "Kilter Board#AB12@2", "Kilter Board", "Tension Board#0001@3",
            "Grasshopper Board#0001@3", "Decoy Board", "So iLL Board#0001@2", "Touchstone Board#0001@3",
            "MoonBoard", "QB_020000000001", "QBB_020000000001", "ShinyWall_STP_0001",
            "ShinyWall_STMF_0001", "ShinyWall_STP_Climbbox1", "Shiny Wall ST0 (ShinyMini)",
            // RoutingProbeActivity's names.
            "Kilter Board#p1@3", "Tension Board#p2@3",
        };
        for (String name : boards) assertTrue(name, rules.isBoardName(name));
        for (String name : new String[] {"Nokia 6.1", "Pixel 8", "Galaxy S24", "Kilter", "Shiny Wall"}) {
            assertFalse(name, rules.isBoardName(name));
        }
    }
}
