public class UndefinedCall {
    public double calculate(int[] values) {
        double avg = computeAverage(values);
        return avg * getScale();
    }
}
