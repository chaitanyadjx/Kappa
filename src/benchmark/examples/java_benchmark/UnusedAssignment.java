public class UnusedAssignment {
    public int compute(int input) {
        int result = getValue();
        result = input * 2;
        return result;
    }

    private int getValue() {
        return 10;
    }
}
