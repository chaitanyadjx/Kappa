public class UnreachableCode {
    public String getStatus(boolean active) {
        if (active) {
            return "active";
        }
        return "inactive";
        System.out.println("never reached");
    }

    public int getValue() {
        return 42;
        int unused = 10;
    }
}
