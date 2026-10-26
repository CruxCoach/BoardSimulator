package org.cruxcoach.boardsimulator;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Which Bluetooth names belong to a board, so are never the phone's own name.
 *
 * The board names come from {@code generated/adapter_names.txt}, which
 * tools/export_mobile.py derives from the registry ("=" exact, "^" prefix).
 * CruxCoach renames the phone while it shares a board ("CruxCoach" + middle
 * dot + board, "CruxCoach@api", earlier "CruxRelay..."); those names are never
 * recorded as the phone's own one either.
 */
final class AdapterNameRules {
    private static final String[] SHARING_PREFIXES = {"CruxCoach\u00b7", "CruxRelay"};
    private final Set<String> exact = new HashSet<>();
    private final List<String> prefixes = new ArrayList<>();

    private AdapterNameRules() {}

    static AdapterNameRules parse(String text) {
        AdapterNameRules rules = new AdapterNameRules();
        for (String line : text.split("\r?\n")) {
            if (line.length() < 2) continue;
            if (line.charAt(0) == '=') rules.exact.add(line.substring(1));
            else if (line.charAt(0) == '^') rules.prefixes.add(line.substring(1));
        }
        for (String prefix : SHARING_PREFIXES) rules.prefixes.add(prefix);
        return rules;
    }

    /** Number of board rules read from the generated list (sharing names excluded). */
    int boardRules() { return exact.size() + prefixes.size() - SHARING_PREFIXES.length; }

    boolean isBoardName(String name) {
        if (name == null) return false;
        String trimmed = name.trim();
        if (exact.contains(trimmed)) return true;
        for (String prefix : prefixes) if (trimmed.startsWith(prefix)) return true;
        // CruxCoach's fallback sharing name: CruxCoach@<api>.
        return trimmed.matches("CruxCoach@\\d+");
    }

    /** The name, when it can be the phone's own one: not blank, no board or sharing name. */
    String usableOwnName(String name) {
        return name == null || name.trim().isEmpty() || isBoardName(name) ? null : name;
    }
}
