package org.cruxcoach.boardsimulator;

import org.junit.Test;
import static org.junit.Assert.*;

public class AdvertisingPolicyTest {
    @Test public void connectionBeforeFirstWritePausesExclusiveBoards() {
        assertTrue(AdvertisingPolicy.shouldAdvertise(false,false,false));
        assertFalse(AdvertisingPolicy.shouldAdvertise(false,false,true));
        // Once the first ATT request resolves the target, only its owner stays hidden.
        assertFalse(AdvertisingPolicy.shouldAdvertise(false,true,false));
        assertTrue(AdvertisingPolicy.shouldAdvertise(false,false,false));
    }

    @Test public void anotherUnresolvedConnectionKeepsAnUnusedBoardHidden() {
        assertFalse(AdvertisingPolicy.shouldAdvertise(false,false,true));
        assertFalse(AdvertisingPolicy.shouldAdvertise(false,true,true));
    }

    @Test public void multiConnectAndLiveModeChangesRespectOutstandingLinks() {
        assertTrue(AdvertisingPolicy.shouldAdvertise(true,true,true));
        assertFalse(AdvertisingPolicy.shouldAdvertise(false,true,true));
        assertTrue(AdvertisingPolicy.shouldAdvertise(true,false,true));
    }
}
