package examples;

/**
 * Demo file with three clear SonarJava violations that Sorald can detect and repair.
 *
 * S1854 - Unused assignments should be removed
 * S2116 - "hashCode" should not be called on array instances
 * S4973 - Strings should be compared with "equals()", not "=="
 */
public class SoraldDemo {

    /**
     * S1854: the value of compute() is assigned to "total" and then discarded —
     * "total" is reassigned before ever being read, so the first assignment is dead.
     */
    public int deadAssignment() {
        int total = compute();
        total = 0;
        return total;
    }

    private int compute() {
        return 42;
    }

    /** S2116: identity-based hashCode on an array; Arrays.hashCode was intended. */
    public int arrayIdentityHashCode(int[] data) {
        return data.hashCode();
    }

    /** S4973: Strings must be compared with .equals(), not with ==. */
    public boolean sameName(String a, String b) {
        return a == b;
    }
}
