package org.cruxcoach.boardsimulator;

/** Connection-time privacy while the shared Android GATT database is unresolved. */
final class AdvertisingPolicy {
    private AdvertisingPolicy() {}

    static boolean shouldAdvertise(boolean multi, boolean owned, boolean unresolved) {
        return multi || (!owned && !unresolved);
    }
}
